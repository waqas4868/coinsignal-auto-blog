"""Phase 11 (Master Handoff 2026-09-23): daily orchestration (spec sections
25, 32).

Wires Phases 1-10 into one resumable daily run. "Resumable" is load-bearing
here, not a nicety: Phase 6's free video path (FlowManualProvider) needs a
human in Google Flow's UI, so a single invocation of this worker usually
CANNOT finish a Reel in one run - it has to advance whatever's currently
possible and stop cleanly, the same way the article pipeline's own
publisher_inflight.json checkpoint lets a multi-step job survive across
scheduled runs.

determine_next_stage() is the pure decision core: given only the already-
persisted state of each phase (never touching the network itself), it says
what should happen next. run_daily_pipeline() is the real orchestration
loop built around it, with every external-service boundary (Gemini, image/
video/voice providers, Facebook) accepted as an injected callable/list
defaulting to the real implementation - the same dependency-injection
pattern Phase 3's run_part_orchestration() already established, so the
sequencing/wiring itself is fully testable with fakes even where the real
calls require credentials this sandbox doesn't have (see the Phase 11/12
reports).

The OTHER half of section 25 (the 1 Reel + 2 text + = 3 total daily mix)
is enforced on the article-pipeline side by a small, separate, surgical
fix to scripts/coinsignal_runtime.py's facebook_main() - see that file's
FACEBOOK_TEXT_DAILY_LIMIT/_todays_reel_source_article_id() and
scripts/tests/test_facebook_daily_mix.py.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from common import PKT, REEL_DAILY_SELECTION_PATH, pkt_date, read_json
import article_selector
import character_reference
import facebook_reel_queue as queue_mod
import final_render
import part_orchestrator
import publish_facebook_reel
import reel_story
import scene_image_provider
import scene_video_provider
import scene_voice_provider

CHARACTER_NAME = character_reference.CHARACTER_NAME
JOBS_DIR = Path(__file__).resolve().parent / "jobs"


def determine_next_stage(
    *,
    selection: dict[str, Any] | None,
    story_doc: dict[str, Any] | None,
    scene_video_states: list[dict[str, Any]],
    scene_voice_states: list[dict[str, Any]],
    render_state: dict[str, Any] | None,
    queue_job: dict[str, Any] | None,
) -> str:
    """Pure - no I/O. Returns one of: NOT_READY, GENERATE_STORY,
    GENERATE_SCENE_ASSETS, AWAIT_MANUAL_VIDEO_IMPORT, RENDER_FINAL,
    ENQUEUE, PUBLISH, DONE."""
    if selection is None or selection.get("reel_status") != "SELECTED":
        return "NOT_READY"
    if story_doc is None:
        return "GENERATE_STORY"

    total_scenes = sum(len(part["scenes"]) for part in story_doc["parts"])
    videos_ready = sum(1 for s in scene_video_states if s.get("status") == "VIDEO_READY")
    voices_ready = sum(1 for s in scene_voice_states if s.get("status") == "VOICE_READY")
    awaiting_manual = any(s.get("status") == "VIDEO_AWAITING_MANUAL_IMPORT" for s in scene_video_states)

    if videos_ready < total_scenes or voices_ready < total_scenes:
        return "AWAIT_MANUAL_VIDEO_IMPORT" if awaiting_manual else "GENERATE_SCENE_ASSETS"

    if render_state is None or render_state.get("status") != "RENDER_READY":
        return "RENDER_FINAL"

    if queue_job is None:
        return "ENQUEUE"
    if queue_job.get("status") == "PUBLISHED":
        return "DONE"
    return "PUBLISH"


def make_job_id(date: str, source_article_id: str) -> str:
    short = hashlib.sha256(source_article_id.encode("utf-8")).hexdigest()[:8]
    return f"REEL-{date}-FUNNY-{short}"


def run_daily_pipeline(
    *,
    date: str | None = None,
    generate_part_fn: Callable = reel_story.generate_part_via_gemini,
    image_providers: list | None = None,
    video_providers: list | None = None,
    voice_providers: list | None = None,
    voice_model_path: Path | None = None,
    publish_kwargs: dict[str, Any] | None = None,
    jobs_dir: Path = JOBS_DIR,
    selection_state_path: Path = REEL_DAILY_SELECTION_PATH,
    queue_path: Path = queue_mod.QUEUE_PATH,
) -> dict[str, Any]:
    """Advances the daily Reel job as far as currently possible and returns
    a status report. Never regenerates already-completed work (every stage
    below delegates to a phase module whose own idempotency guarantee is
    already tested independently - see that phase's own test file).

    selection_state_path/queue_path default to the real production files
    but are overridable so this can be exercised end-to-end against a
    throwaway sandbox (see the Phase 12 dry-run test) without touching
    this repo's actual state/ or reels/queue/ files.
    """
    date = date or pkt_date()
    job_dir = jobs_dir / date

    selection = article_selector.load_selection(date, selection_state_path)
    if selection is None:
        # No persisted selection yet for this date - this is the ONLY
        # place anything ever performs the live Blogger fetch + selection
        # (a real bug found in production: this call was missing entirely,
        # so the pipeline reported NOT_READY forever regardless of how
        # many articles actually existed, since load_selection() alone is
        # a pure local file read and nothing else ever writes that file).
        selection = article_selector.run_selection_for_today(date, selection_state_path)
    if selection is None or selection.get("reel_status") != "SELECTED":
        detail = selection.get("detail") if isinstance(selection, dict) else None
        articles_available = selection.get("articles_available") if isinstance(selection, dict) else None
        return {
            "stage": "NOT_READY",
            "date": date,
            "detail": detail or f"fewer than 6 same-day Blogger articles so far ({articles_available if articles_available is not None else '?'}/6)",
        }

    job_id = make_job_id(date, selection["reel_source_article_id"])

    story_doc = read_json(job_dir / "job.json", None)
    story_doc = story_doc if isinstance(story_doc, dict) and story_doc.get("status") == "COMPLETE" else None

    if story_doc is None:
        source_data = reel_story.build_reel_source_data(
            article_title=selection["reel_source_article_title"],
            article_url=selection["reel_source_article_url"],
            article_content_html=selection.get("reel_source_article_content_html", ""),
        )
        if source_data["status"] == "NEEDS_REVIEW":
            return {"stage": "NEEDS_REVIEW", "date": date, "job_id": job_id, "detail": "source article too thin to script from"}

        character_reference.save_character_reference(CHARACTER_NAME, version=1)
        story_doc = part_orchestrator.run_part_orchestration(
            job_id=job_id, source_data=source_data, selection=selection, generate_part=generate_part_fn, job_dir=job_dir
        )
        return {"stage": "GENERATE_STORY", "date": date, "job_id": job_id, "detail": "story generated this run"}

    scenes = [scene for part in story_doc["parts"] for scene in part["scenes"]]
    image_providers = image_providers or scene_image_provider.default_providers(os.environ.get("HF_TOKEN", ""))
    video_providers = video_providers or scene_video_provider.default_providers(job_dir / "video_manifest.json")
    voice_model_path = voice_model_path or (Path(__file__).resolve().parent / ".piper_voices" / "en_US-ljspeech-medium.onnx")
    voice_providers = voice_providers or scene_voice_provider.default_providers(voice_model_path)

    video_states, voice_states = [], []
    for scene in scenes:
        scene_num = scene["scene_number"]
        image_state_path = job_dir / f"scene_{scene_num:02d}_image.json"
        image_path = job_dir / f"scene_{scene_num:02d}_image.png"
        image_record = scene_image_provider.generate_scene_image(
            prompt=scene["image_prompt"], output_path=image_path, providers=image_providers, state_path=image_state_path
        )

        video_state_path = job_dir / f"scene_{scene_num:02d}_video.json"
        video_path = job_dir / f"scene_{scene_num:02d}_video.mp4"
        if image_record.get("status") == "IMAGE_READY":
            video_record = scene_video_provider.generate_scene_video(
                image_path=image_path, motion_prompt=scene["motion_prompt"], output_path=video_path,
                target_duration_seconds=scene_video_provider.SCENE_TARGET_DURATION_SECONDS,
                providers=video_providers, state_path=video_state_path,
            )
        else:
            video_record = read_json(video_state_path, {"status": "VIDEO_PENDING"})
        video_states.append(video_record)

        voice_state_path = job_dir / f"scene_{scene_num:02d}_voice.json"
        voice_path = job_dir / f"scene_{scene_num:02d}_voice.wav"
        voice_record = scene_voice_provider.generate_scene_voice(
            text=scene["voiceover"], output_path=voice_path, providers=voice_providers, state_path=voice_state_path
        )
        voice_states.append(voice_record)

    render_state = read_json(job_dir / "render_state.json", None)
    render_state = render_state if isinstance(render_state, dict) else None

    queue = queue_mod.load_queue(queue_path)
    queue_job = queue_mod.find_job(queue, job_id)

    stage = determine_next_stage(
        selection=selection, story_doc=story_doc, scene_video_states=video_states, scene_voice_states=voice_states,
        render_state=render_state, queue_job=queue_job,
    )

    if stage in ("GENERATE_SCENE_ASSETS", "AWAIT_MANUAL_VIDEO_IMPORT"):
        videos_ready = sum(1 for s in video_states if s.get("status") == "VIDEO_READY")
        return {
            "stage": stage, "date": date, "job_id": job_id,
            "detail": f"{videos_ready}/{len(scenes)} scene videos ready" + (" - awaiting manual Flow import" if stage == "AWAIT_MANUAL_VIDEO_IMPORT" else ""),
        }

    if stage == "RENDER_FINAL":
        render_scenes = [
            {"video_path": v["path"], "voice_path": a["path"], "caption_text": scene["voiceover"]}
            for scene, v, a in zip(scenes, video_states, voice_states)
        ]
        render_record = final_render.render_final_reel(
            scenes=render_scenes, output_path=job_dir / "final_reel.mp4", work_dir=job_dir / "render_work",
            state_path=job_dir / "render_state.json",
        )
        return {"stage": "RENDER_FINAL", "date": date, "job_id": job_id, "render": render_record}

    if stage == "ENQUEUE":
        job = queue_mod.enqueue_reel(
            queue, job_id=job_id, date=date, source_article_id=selection["reel_source_article_id"],
            source_article_url=selection["reel_source_article_url"], source_article_title=selection["reel_source_article_title"],
            video_path=render_state["path"], caption=scenes[0]["voiceover"],
        )
        queue_mod.save_queue(queue, queue_path)
        return {"stage": "ENQUEUE", "date": date, "job_id": job_id, "job": job}

    if stage == "PUBLISH":
        kwargs = publish_kwargs or {}
        result = publish_facebook_reel.publish_reel_job(queue_job, **kwargs)
        queue_mod.save_queue(queue, queue_path)
        return {"stage": "PUBLISH", "date": date, "job_id": job_id, "publish": result}

    return {"stage": "DONE", "date": date, "job_id": job_id}
