"""Phase 10 (Master Handoff 2026-09-23): Facebook Reel publishing (spec
section 24).

Reuses reels/facebook_reel_publisher.py's Facebook Reels API functions
directly - unlike the image/video/voice generators (Phases 5-7), the Reels
API itself has no pipeline-specific assumptions baked into it: same Page,
same documented 4-step upload flow, same token resolution, regardless of
which pipeline produced the video. That module was already verified
against a REAL LIVE publish earlier in this project
(facebook_video_id 1950173775657353). Re-deriving it fresh here would only
risk reintroducing bugs in something already proven, for no benefit - the
isolation principle applied in Phases 5-7 was about not coupling to
pipeline-specific STATE and STYLE assumptions, not about refusing to reuse
stateless, generic API-calling functions.

What IS new here: DRY_RUN-by-default gating (publishing is never live
unless REEL_PUBLISH_LIVE=true is explicitly set), wiring to Phase 9's
queue, and duplicate-Reel prevention on retry. On that last point: this
project already solved the exact same problem for the OLD Reels pipeline
(an "upload_may_have_reached_facebook" flag, only trusted after
start_upload_session() succeeds - see git history). The same pattern is
reused here: once a real video_id exists on Facebook's side, a later
failure marks the job terminally FAILED rather than RETRY, so an automatic
retry can never start a second upload_reels session for the same job
("reruns must not create duplicate Reels", section 24).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import facebook_reel_publisher as fb_api
import facebook_reel_queue as queue_mod
from common import iso_now
from final_render import check_final_render


class PublishError(RuntimeError):
    pass


def is_live_publish_enabled() -> bool:
    return os.environ.get("REEL_PUBLISH_LIVE", "").strip().lower() == "true"


def publish_reel_job(
    job: dict[str, Any],
    *,
    page_id: str,
    access_token: str,
    graph_version: str,
    github_repo: str,
    github_token: str,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    """Runs the section-24 publish flow for one queue job.

    dry_run defaults to `not is_live_publish_enabled()`: publishing is
    never live by accident. Pre-flight checks (queue state, video exists,
    video is technically valid) run in BOTH modes, so a dry run genuinely
    tells you whether a live attempt would pass them.
    """
    if dry_run is None:
        dry_run = not is_live_publish_enabled()

    ok, reason = queue_mod.can_publish(job)
    if not ok:
        raise PublishError(f"pre-flight check failed: {reason}")

    video_path = Path(job["video_path"])
    valid, reason, _ = check_final_render(video_path)
    if not valid:
        raise PublishError(f"video failed validation, refusing to publish: {reason}")

    if dry_run:
        return {
            "dry_run": True,
            "would_publish": True,
            "job_id": job["job_id"],
            "page_id": page_id,
            "video_path": str(video_path),
            "title": job.get("source_article_title", ""),
            "caption": job.get("caption", ""),
            "checked_at": iso_now(),
        }

    queue_mod.mark_publishing(job)
    tag = f"reel-temp-{job['job_id']}"
    release_info: dict[str, Any] | None = None
    upload_may_have_reached_facebook = False

    try:
        resolved_token = fb_api.resolve_page_access_token(page_id, access_token, graph_version)
        release_info = fb_api.upload_temp_release_asset(github_repo, github_token, video_path, tag)

        video_id = fb_api.start_upload_session(page_id, resolved_token, graph_version)
        upload_may_have_reached_facebook = True  # a real video_id now exists on Facebook's side

        fb_api.upload_hosted_video(video_id, resolved_token, graph_version, release_info["download_url"])
        phase = fb_api.wait_for_upload_complete(video_id, resolved_token, graph_version)
        if phase.lower() in {"error", "failed"}:
            raise PublishError(f"Facebook reported upload phase {phase!r}")

        result = fb_api.finish_and_publish(
            page_id, video_id, resolved_token, graph_version,
            title=(job.get("source_article_title") or "")[:255],
            description=job.get("caption", ""),
        )
        published_id = str(result.get("video_id") or video_id)
        queue_mod.mark_published(job, published_id)
        return {"dry_run": False, "job_id": job["job_id"], "facebook_post_id": published_id}

    except Exception as exc:
        # Once Facebook has a real video_id for this job, an automatic
        # retry must never start a second upload session - mark it
        # terminally FAILED instead and require a human to check Facebook
        # before manually resetting it.
        retryable = not upload_may_have_reached_facebook
        queue_mod.mark_failed(job, str(exc), retryable=retryable)
        raise PublishError(str(exc)) from exc

    finally:
        if release_info is not None:
            fb_api.cleanup_temp_release(github_repo, github_token, release_info["release_id"], tag)
