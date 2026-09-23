"""Phase 9 (Master Handoff 2026-09-23): durable Facebook Reel queue
(spec sections 23-24, 26).

Deliberately separate from state/reel_queue.json - that's the existing,
still-scheduled free-tools Reels pipeline's own queue, a different schema
for a different pipeline; see reels/common.py's docstring for why
cross-pipeline coupling is avoided structurally throughout this project,
not just by convention. This one lives at reels/queue/facebook_reel_queue.json
per the spec's own file layout (section 29).

This module is PURE QUEUE STATE MANAGEMENT. It never calls Facebook's API
(that's Phase 10) and never regenerates content - it only ever reads a
video_path that Phase 8 already rendered onto disk. "Facebook failure must
NOT regenerate article/image/video/voice; only the failed publishing stage
is retried" (section 26) is satisfied structurally here: this module has
no function that could reach back into generation at all (verified by
test_facebook_reel_queue.py, which inspects this file's own imports).

Statuses (section 23): READY -> PUBLISHING -> PUBLISHED
                                            -> RETRY -> PUBLISHING (loop)
                                            -> FAILED (terminal)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common import REPO_ROOT, iso_now, read_json, write_json

QUEUE_PATH = REPO_ROOT / "reels" / "queue" / "facebook_reel_queue.json"

STATUSES = {"READY", "PUBLISHING", "PUBLISHED", "FAILED", "RETRY"}

# The spec doesn't set a numeric retry cap; without one, a persistently
# failing publish (e.g. a wrong-but-parseable video) could loop READY/RETRY
# forever across scheduled runs. Capping attempts and escalating to
# terminal FAILED afterward is a deliberate, disclosed addition - see the
# Phase 9 report.
MAX_ATTEMPTS = 5


class QueueError(RuntimeError):
    pass


def load_queue(queue_path: Path = QUEUE_PATH) -> list[dict[str, Any]]:
    raw = read_json(queue_path, [])
    return raw if isinstance(raw, list) else []


def save_queue(queue: list[dict[str, Any]], queue_path: Path = QUEUE_PATH) -> None:
    write_json(queue_path, queue)


def find_job(queue: list[dict[str, Any]], job_id: str) -> dict[str, Any] | None:
    return next((job for job in queue if job.get("job_id") == job_id), None)


def find_job_for_date(queue: list[dict[str, Any]], date: str) -> dict[str, Any] | None:
    return next((job for job in queue if job.get("date") == date), None)


def enqueue_reel(
    queue: list[dict[str, Any]],
    *,
    job_id: str,
    date: str,
    source_article_id: str,
    source_article_url: str,
    source_article_title: str,
    video_path: str,
    caption: str,
) -> dict[str, Any]:
    """Idempotent on two levels (section 24: "reruns must not create
    duplicate Reels"): the exact job_id is never duplicated, AND a second
    DIFFERENT job for a date that already has one is refused outright -
    REEL_DAILY_LIMIT=1 is enforced structurally here, not left to upstream
    discipline alone."""
    existing = find_job(queue, job_id)
    if existing is not None:
        return existing

    existing_for_date = find_job_for_date(queue, date)
    if existing_for_date is not None:
        raise QueueError(
            f"date {date} already has a queued Reel job ({existing_for_date['job_id']!r}, "
            f"status={existing_for_date['status']!r}); REEL_DAILY_LIMIT=1 - refusing to enqueue {job_id!r}"
        )

    job = {
        "job_id": job_id,
        "date": date,
        "source_article_id": source_article_id,
        "source_article_url": source_article_url,
        "source_article_title": source_article_title,
        "video_path": video_path,
        "caption": caption,
        "status": "READY",
        "attempts": 0,
        "created_at": iso_now(),
        "started_at": "",
        "published_at": "",
        "facebook_post_id": "",
        "error": "",
    }
    queue.append(job)
    return job


def can_publish(job: dict[str, Any]) -> tuple[bool, str]:
    """Pre-flight checks (section 24): "verify status is READY, verify
    video exists, verify daily Reel limit has not been reached" - the
    daily-limit half of that is enforced at enqueue time above (only one
    job can ever exist per date), so this checks the remaining two plus a
    not-already-published guard."""
    if job.get("status") == "PUBLISHED":
        return False, "job is already PUBLISHED - refusing to publish again"
    if job.get("status") not in ("READY", "RETRY"):
        return False, f"job status is {job.get('status')!r}, not READY/RETRY"
    video_path = job.get("video_path", "")
    if not video_path or not Path(video_path).exists():
        return False, f"video file does not exist: {video_path!r}"
    return True, ""


def mark_publishing(job: dict[str, Any]) -> dict[str, Any]:
    ok, reason = can_publish(job)
    if not ok:
        raise QueueError(reason)
    job["status"] = "PUBLISHING"
    job["started_at"] = iso_now()
    job["attempts"] = int(job.get("attempts", 0)) + 1
    job["error"] = ""
    return job


def mark_published(job: dict[str, Any], facebook_post_id: str) -> dict[str, Any]:
    if job.get("status") != "PUBLISHING":
        raise QueueError(f"cannot mark PUBLISHED from status {job.get('status')!r} (expected PUBLISHING)")
    job["status"] = "PUBLISHED"
    job["published_at"] = iso_now()
    job["facebook_post_id"] = facebook_post_id
    job["error"] = ""
    return job


def mark_failed(job: dict[str, Any], error: str, *, retryable: bool) -> dict[str, Any]:
    if job.get("status") != "PUBLISHING":
        raise QueueError(f"cannot mark failed from status {job.get('status')!r} (expected PUBLISHING)")
    job["error"] = str(error)[:2000]
    if retryable and int(job.get("attempts", 0)) < MAX_ATTEMPTS:
        job["status"] = "RETRY"
    else:
        job["status"] = "FAILED"
    return job
