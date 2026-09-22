"""Entrypoint: orchestrates one Reel job end to end.

CORE RULE (per the user's spec): a Reel failure must never stop or damage
Blogger article publishing or the existing Facebook image-post system. This
module never imports scripts/coinsignal_runtime.py and never writes any file
outside state/ and its own temp work directory - see common.py's docstring.

Any expected/"stop the reel" failure (image generation exhausted, video
failed validation, ambiguous upload outcome) is caught, persisted to
state/reel_queue.json, logged clearly, and the process exits 0 (a Reel not
being posted this run is a normal, successful outcome - same philosophy the
article pipeline uses for HardQuotaError/ServicePauseError). Only a genuine
bug/unexpected exception exits non-zero, so it surfaces as a red X.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import requests

import common
import facebook_reel_publisher as fb
import image_generator_a
import image_generator_b
import image_quality_check
import reel_planner
import video_generator
import video_quality_check
import voice_generator

from common import PipelineStop, iso_now, read_json, write_json


class ReelStopped(RuntimeError):
    """Raised internally to stop THIS reel job only. Caught in main()."""


def load_config() -> dict[str, str]:
    values = common.require_env(
        "FACEBOOK_PAGE_ID",
        "FACEBOOK_PAGE_ACCESS_TOKEN",
        "BLOGGER_BLOG_ID",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REFRESH_TOKEN",
        "GEMINI_API_KEY",
        "GITHUB_TOKEN",
    )
    values["FACEBOOK_GRAPH_VERSION"] = os.environ.get("FACEBOOK_GRAPH_VERSION", "v26.0").strip() or "v26.0"
    values["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
    values["HF_TOKEN"] = os.environ.get("HF_TOKEN", "").strip()
    values["GITHUB_REPOSITORY"] = os.environ.get("GITHUB_REPOSITORY", "").strip()
    values["GITHUB_REF_NAME"] = os.environ.get("GITHUB_REF_NAME", "main").strip() or "main"
    values["REEL_TYPE"] = os.environ.get("REEL_TYPE", "news").strip().lower()
    return values


def load_queue_and_state() -> tuple[list[dict], dict]:
    queue = read_json(common.REEL_QUEUE_PATH, [])
    if not isinstance(queue, list):
        queue = []
    state = read_json(common.REEL_STATE_PATH, {"date": "", "posted_today": 0})
    if not isinstance(state, dict):
        state = {"date": "", "posted_today": 0}
    return queue, state


def persist(queue: list[dict], state: dict, cfg: dict[str, str], message: str) -> None:
    write_json(common.REEL_QUEUE_PATH, queue)
    write_json(common.REEL_STATE_PATH, state)
    try:
        common.git_commit_push_state(
            [common.REEL_QUEUE_PATH, common.REEL_STATE_PATH], message, cfg["GITHUB_REF_NAME"]
        )
    except Exception as exc:
        # Bookkeeping only - never let a git hiccup mask an already-decided outcome.
        print("Reel state Git persistence warning (non-fatal):", exc)


def notify(message: str) -> None:
    ntfy_url = os.environ.get("NTFY_URL", "").strip()
    if not ntfy_url:
        return
    try:
        requests.post(ntfy_url, data=message.encode("utf-8"), timeout=15)
    except Exception as exc:
        print("ntfy notification warning (non-fatal):", exc)


def update_job(queue: list[dict], job: dict, state: str, *, error_reason: str = "", **fields) -> None:
    job["state"] = state
    job["updated_at"] = iso_now()
    if error_reason:
        job["error_reason"] = error_reason[:1500]
    job.update(fields)
    for i, existing in enumerate(queue):
        if existing.get("reel_job_id") == job["reel_job_id"]:
            queue[i] = job
            return
    queue.append(job)


def _rescale_scenes_to_narration(scenes: list[dict], voice_path: Path) -> None:
    """Scale scene durations to match the REAL synthesized narration length.

    The Gemini-generated duration_seconds per scene and the actual TTS output
    length are only loosely related (same LLM call, not the same measurement) -
    without this, ffmpeg's -shortest mux would either truncate the video mid-
    narration or leave a long silent tail. Enforces Facebook's 4-60s floor/ceiling.
    """
    real_duration = video_quality_check.probe_media_duration(voice_path)
    if real_duration <= 0:
        return  # fall back to the planner's own estimates
    target_total = max(4.5, min(58.0, real_duration))
    current_total = sum(s["duration"] for s in scenes) or 1.0
    scale = target_total / current_total
    for scene in scenes:
        scene["duration"] = max(1.2, round(scene["duration"] * scale, 2))


def generate_scene_image(prompt: str, output_path: Path, cfg: dict[str, str]) -> None:
    ok = image_generator_a.generate_image(prompt, output_path)
    if ok:
        valid, reason = image_quality_check.check_image(output_path)
        if valid:
            return
        print(f"Pollinations image failed validation ({reason}); trying HF fallback")
    else:
        print("Pollinations image generation failed; trying HF fallback")

    ok = image_generator_b.generate_image(prompt, output_path, cfg["HF_TOKEN"])
    if ok:
        valid, reason = image_quality_check.check_image(output_path)
        if valid:
            return
        raise ReelStopped(f"HF fallback image also failed validation: {reason}")
    raise ReelStopped("Both image providers (Pollinations and HF fallback) failed for a scene")


def main() -> int:
    cfg = load_config()
    queue, state = load_queue_and_state()
    work_dir = common.WORK_DIR
    work_dir.mkdir(parents=True, exist_ok=True)

    reel_job_id = f"REEL-{time.strftime('%Y-%m-%d')}-{cfg['REEL_TYPE'].upper()}-{uuid.uuid4().hex[:8]}"
    job = {
        "reel_job_id": reel_job_id,
        "article_source_id": "",
        "content_type": cfg["REEL_TYPE"],
        "created_at": iso_now(),
        "state": "planned",
        "facebook_video_id": "",
        "updated_at": iso_now(),
        "error_reason": "",
    }

    try:
        token = reel_planner.refresh_google_token(
            cfg["GOOGLE_CLIENT_ID"], cfg["GOOGLE_CLIENT_SECRET"], cfg["GOOGLE_REFRESH_TOKEN"]
        )
        article = reel_planner.pick_unreeled_article(cfg["BLOGGER_BLOG_ID"], token)
        if not article:
            print("No unreeled published article available; successful no-op")
            return 0
        job["article_source_id"] = article["source_id"]
        print(f"Selected source article: {article['title']}")

        plan = reel_planner.generate_reel_plan(article, cfg["REEL_TYPE"], cfg["GEMINI_API_KEY"], cfg["GEMINI_MODEL"])
        print(f"Reel plan generated: {plan['title']} ({len(plan['scenes'])} scenes)")

        update_job(queue, job, "generating_images")
        persist(queue, state, cfg, f"Start reel job {reel_job_id}")

        scenes = []
        for i, scene in enumerate(plan["scenes"]):
            image_path = work_dir / reel_job_id / f"scene_{i:02d}.png"
            generate_scene_image(scene["image_prompt"], image_path, cfg)
            scenes.append(
                {
                    "image_path": image_path,
                    "caption": scene.get("caption", ""),
                    "duration": float(scene["duration_seconds"]),
                }
            )
        update_job(queue, job, "images_approved")

        voice_path = work_dir / reel_job_id / "voice.wav"
        voice_generator.ensure_voice_downloaded()
        voice_generator.synthesize(plan["full_script"], voice_path)
        _rescale_scenes_to_narration(scenes, voice_path)

        update_job(queue, job, "video_generating")
        video_path = work_dir / reel_job_id / "final.mp4"
        video_generator.build_reel(scenes, voice_path, video_path, work_dir / reel_job_id / "clips")

        valid, reason = video_quality_check.check_video(video_path)
        if not valid:
            raise ReelStopped(f"Assembled video failed validation: {reason}")
        update_job(queue, job, "video_ready")
        persist(queue, state, cfg, f"Reel job {reel_job_id} video ready")

        release_tag = f"reel-temp-{reel_job_id.lower()}"
        release = fb.upload_temp_release_asset(cfg["GITHUB_REPOSITORY"], cfg["GITHUB_TOKEN"], video_path, release_tag)
        update_job(queue, job, "upload_started")
        try:
            video_id = fb.start_upload_session(
                cfg["FACEBOOK_PAGE_ID"], cfg["FACEBOOK_PAGE_ACCESS_TOKEN"], cfg["FACEBOOK_GRAPH_VERSION"]
            )
            fb.upload_hosted_video(
                video_id, cfg["FACEBOOK_PAGE_ACCESS_TOKEN"], cfg["FACEBOOK_GRAPH_VERSION"], release["download_url"]
            )
            update_job(queue, job, "upload_completed", facebook_video_id=video_id)
            persist(queue, state, cfg, f"Reel job {reel_job_id} upload completed")

            phase = fb.wait_for_upload_complete(
                video_id, cfg["FACEBOOK_PAGE_ACCESS_TOKEN"], cfg["FACEBOOK_GRAPH_VERSION"]
            )
            update_job(queue, job, "processing")
            if phase.lower() in {"error", "failed"}:
                raise ReelStopped(f"Facebook reported upload processing failure: {phase}")

            fb.finish_and_publish(
                cfg["FACEBOOK_PAGE_ID"],
                video_id,
                cfg["FACEBOOK_PAGE_ACCESS_TOKEN"],
                cfg["FACEBOOK_GRAPH_VERSION"],
                title=plan["title"],
                description=plan["description"],
            )
            update_job(queue, job, "published")
            state["date"] = time.strftime("%Y-%m-%d")
            state["posted_today"] = int(state.get("posted_today", 0)) + 1
            persist(queue, state, cfg, f"Reel job {reel_job_id} published")
            notify(f"CoinSignal Reel published: {plan['title']} ✅")
            print("SUCCESS: Reel published, facebook_video_id =", video_id)
            return 0
        finally:
            fb.cleanup_temp_release(cfg["GITHUB_REPOSITORY"], cfg["GITHUB_TOKEN"], release["release_id"], release["tag"])

    except ReelStopped as exc:
        print("REEL STOPPED (expected, article pipeline unaffected):", exc)
        update_job(queue, job, "failed", error_reason=str(exc))
        persist(queue, state, cfg, f"Reel job {reel_job_id} stopped: {exc}")
        notify(f"CoinSignal Reel failed ❌: {exc}")
        return 0
    except Exception as exc:
        print("REEL UNKNOWN/UNEXPECTED ERROR (article pipeline unaffected):", exc)
        update_job(queue, job, "unknown", error_reason=str(exc))
        try:
            persist(queue, state, cfg, f"Reel job {reel_job_id} unknown error: {exc}")
        except Exception as persist_exc:
            print("Additionally failed to persist reel state:", persist_exc)
        notify(f"CoinSignal Reel upload unknown ⚠️: verification required")
        raise


if __name__ == "__main__":
    sys.exit(main())
