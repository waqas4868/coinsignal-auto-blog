"""Phase 9 tests for reels/facebook_reel_queue.py.

Run: python reels/tests/test_facebook_reel_queue.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import facebook_reel_queue as frq  # noqa: E402


def _enqueue(queue, *, job_id="REEL-2026-09-23-FUNNY-abc", date="2026-09-23", video_exists: bool = True, video_dir: Path = None):
    if video_exists:
        video_path = video_dir / f"{job_id}.mp4"
        video_path.write_bytes(b"fake mp4 bytes")
    else:
        video_path = video_dir / f"{job_id}_missing.mp4"
    return frq.enqueue_reel(
        queue, job_id=job_id, date=date, source_article_id="abc123",
        source_article_url="https://blogsdrip4u.blogspot.com/abc123.html",
        source_article_title="Bitcoin Breaks Key Resistance", video_path=str(video_path), caption="Bitcoin just broke resistance!",
    )


# ---------------------------------------------------------------------------
# enqueue_reel
# ---------------------------------------------------------------------------

def test_enqueue_creates_ready_job() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        assert job["status"] == "READY"
        assert job["attempts"] == 0
        assert job["facebook_post_id"] == ""
        assert len(queue) == 1
    print("PASS: enqueueing creates a READY job with the correct initial fields")


def test_enqueue_is_idempotent_on_job_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        first = _enqueue(queue, video_dir=Path(tmp))
        second = _enqueue(queue, video_dir=Path(tmp))
        assert first is second or first == second
        assert len(queue) == 1
    print("PASS: enqueueing the same job_id twice does not create a duplicate")


def test_enqueue_refuses_second_job_for_same_date() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        queue = []
        _enqueue(queue, job_id="REEL-A", date="2026-09-23", video_dir=tmp)
        raised = False
        try:
            _enqueue(queue, job_id="REEL-B", date="2026-09-23", video_dir=tmp)
        except frq.QueueError as exc:
            raised = True
            assert "REEL_DAILY_LIMIT" in str(exc)
        assert raised
        assert len(queue) == 1
    print("PASS: a second different job for a date that already has one is refused (REEL_DAILY_LIMIT=1)")


def test_enqueue_allows_different_dates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        queue = []
        _enqueue(queue, job_id="REEL-A", date="2026-09-23", video_dir=tmp)
        _enqueue(queue, job_id="REEL-B", date="2026-09-24", video_dir=tmp)
        assert len(queue) == 2
    print("PASS: jobs for different dates coexist fine")


# ---------------------------------------------------------------------------
# can_publish / mark_publishing
# ---------------------------------------------------------------------------

def test_can_publish_true_for_ready_with_existing_video() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        ok, reason = frq.can_publish(job)
        assert ok, reason
    print("PASS: can_publish is True for a READY job whose video exists")


def test_can_publish_false_when_video_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp), video_exists=False)
        ok, reason = frq.can_publish(job)
        assert not ok and "does not exist" in reason, (ok, reason)
    print("PASS: can_publish is False when the video file doesn't actually exist on disk")


def test_can_publish_false_when_already_published() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        frq.mark_publishing(job)
        frq.mark_published(job, "fb-post-123")
        ok, reason = frq.can_publish(job)
        assert not ok and "already PUBLISHED" in reason, (ok, reason)
    print("PASS: can_publish refuses a job that's already PUBLISHED")


def test_mark_publishing_transitions_and_increments_attempts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        frq.mark_publishing(job)
        assert job["status"] == "PUBLISHING"
        assert job["attempts"] == 1
        assert job["started_at"]
    print("PASS: mark_publishing transitions READY->PUBLISHING and increments attempts")


def test_mark_publishing_refuses_when_cannot_publish() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp), video_exists=False)
        raised = False
        try:
            frq.mark_publishing(job)
        except frq.QueueError:
            raised = True
        assert raised
        assert job["status"] == "READY", "a refused transition must not have mutated status"
    print("PASS: mark_publishing raises and leaves the job untouched when preconditions aren't met")


# ---------------------------------------------------------------------------
# mark_published / mark_failed
# ---------------------------------------------------------------------------

def test_mark_published_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        frq.mark_publishing(job)
        frq.mark_published(job, "fb-post-999")
        assert job["status"] == "PUBLISHED"
        assert job["facebook_post_id"] == "fb-post-999"
        assert job["published_at"]
    print("PASS: mark_published transitions PUBLISHING->PUBLISHED with the real post id recorded")


def test_mark_published_refuses_wrong_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))  # still READY, never went through mark_publishing
        raised = False
        try:
            frq.mark_published(job, "fb-post-1")
        except frq.QueueError:
            raised = True
        assert raised
    print("PASS: mark_published refuses to fire from a non-PUBLISHING status")


def test_mark_failed_retryable_sets_retry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        frq.mark_publishing(job)
        frq.mark_failed(job, "temporary Facebook 5xx", retryable=True)
        assert job["status"] == "RETRY"
        assert "temporary" in job["error"]
    print("PASS: a retryable failure sets status RETRY, not terminal FAILED")


def test_mark_failed_non_retryable_is_terminal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        frq.mark_publishing(job)
        frq.mark_failed(job, "(#200) permission error", retryable=False)
        assert job["status"] == "FAILED"
    print("PASS: a non-retryable failure is immediately terminal FAILED")


def test_mark_failed_escalates_to_terminal_after_max_attempts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        for _ in range(frq.MAX_ATTEMPTS):
            frq.mark_publishing(job)
            frq.mark_failed(job, "still failing", retryable=True)
            if job["status"] == "FAILED":
                break
        assert job["status"] == "FAILED", job
        assert job["attempts"] == frq.MAX_ATTEMPTS, job["attempts"]
    print(f"PASS: a persistently-retryable failure escalates to terminal FAILED after {frq.MAX_ATTEMPTS} attempts instead of looping forever")


# ---------------------------------------------------------------------------
# Full lifecycles
# ---------------------------------------------------------------------------

def test_full_lifecycle_ready_to_published() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        frq.mark_publishing(job)
        frq.mark_published(job, "fb-post-42")
        assert job["status"] == "PUBLISHED"
        assert job["attempts"] == 1
        ok, reason = frq.can_publish(job)
        assert not ok
    print("PASS: full READY -> PUBLISHING -> PUBLISHED lifecycle behaves correctly end to end")


def test_retry_lifecycle_then_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = []
        job = _enqueue(queue, video_dir=Path(tmp))
        frq.mark_publishing(job)
        frq.mark_failed(job, "transient error", retryable=True)
        assert job["status"] == "RETRY"
        frq.mark_publishing(job)  # second attempt, allowed from RETRY
        assert job["attempts"] == 2
        frq.mark_published(job, "fb-post-77")
        assert job["status"] == "PUBLISHED"
    print("PASS: a job can retry after a transient failure and still reach PUBLISHED")


# ---------------------------------------------------------------------------
# Persistence + structural isolation guard
# ---------------------------------------------------------------------------

def test_save_and_load_queue_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        queue = []
        _enqueue(queue, video_dir=tmp)
        queue_path = tmp / "facebook_reel_queue.json"
        frq.save_queue(queue, queue_path)
        reloaded = frq.load_queue(queue_path)
        assert reloaded == queue
    print("PASS: queue persists and reloads exactly via save_queue/load_queue")


def test_module_never_imports_the_generation_pipeline() -> None:
    """Structural guard for section 26 ('Facebook failure must NOT
    regenerate article/image/video/voice'): this queue module must have no
    import path into any generation-capable module at all, so that
    guarantee can't be silently broken by a future edit that adds a
    'helpful' regeneration call."""
    forbidden_substrings = [
        "scene_image_provider", "scene_video_provider", "scene_voice_provider",
        "final_render", "reel_planner", "part_orchestrator", "article_selector",
        "coinsignal_runtime",
    ]
    source = Path(__file__).resolve().parent.parent.joinpath("facebook_reel_queue.py").read_text(encoding="utf-8")
    hits = [name for name in forbidden_substrings if name in source]
    assert not hits, f"facebook_reel_queue.py references generation-pipeline modules: {hits}"
    print("PASS: facebook_reel_queue.py has no import path into any content-generation module")


if __name__ == "__main__":
    test_enqueue_creates_ready_job()
    test_enqueue_is_idempotent_on_job_id()
    test_enqueue_refuses_second_job_for_same_date()
    test_enqueue_allows_different_dates()
    test_can_publish_true_for_ready_with_existing_video()
    test_can_publish_false_when_video_missing()
    test_can_publish_false_when_already_published()
    test_mark_publishing_transitions_and_increments_attempts()
    test_mark_publishing_refuses_when_cannot_publish()
    test_mark_published_success()
    test_mark_published_refuses_wrong_status()
    test_mark_failed_retryable_sets_retry()
    test_mark_failed_non_retryable_is_terminal()
    test_mark_failed_escalates_to_terminal_after_max_attempts()
    test_full_lifecycle_ready_to_published()
    test_retry_lifecycle_then_success()
    test_save_and_load_queue_round_trip()
    test_module_never_imports_the_generation_pipeline()
    print("\nALL PHASE 9 TESTS PASSED")
