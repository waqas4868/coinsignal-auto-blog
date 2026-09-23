"""Phase 10 tests for reels/publish_facebook_reel.py.

No live Facebook or GitHub calls: the underlying facebook_reel_publisher
functions are monkeypatched with fakes. What IS real: a genuine ffmpeg-
rendered 1080x1920/h264/aac fixture is used so check_final_render's
pre-flight gate is exercised for real, not bypassed.

Run: python reels/tests/test_publish_facebook_reel.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import publish_facebook_reel as pub  # noqa: E402
import facebook_reel_queue as frq  # noqa: E402
import facebook_reel_publisher as fb_api  # noqa: E402


def _make_valid_reel_video(path: Path, *, duration: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=1080x1920:rate=15",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return path


def _job(video_path: Path, job_id: str = "REEL-2026-09-23-FUNNY-test") -> dict:
    queue: list = []
    return frq.enqueue_reel(
        queue, job_id=job_id, date="2026-09-23", source_article_id="abc123",
        source_article_url="https://blogsdrip4u.blogspot.com/abc123.html",
        source_article_title="Bitcoin Breaks Key Resistance", video_path=str(video_path),
        caption="Bitcoin just broke resistance!",
    )


class _Recorder:
    def __init__(self):
        self.calls: list[str] = []


def _patch_fb_api(recorder: _Recorder, *, fail_at: str | None = None):
    """Monkeypatches every fb_api function with a fake that records the call
    and optionally raises at a named step, restoring originals via the
    returned callable."""
    originals = {
        name: getattr(fb_api, name)
        for name in [
            "resolve_page_access_token", "upload_temp_release_asset", "start_upload_session",
            "upload_hosted_video", "wait_for_upload_complete", "finish_and_publish", "cleanup_temp_release",
        ]
    }

    def make(name, retval):
        def fn(*args, **kwargs):
            recorder.calls.append(name)
            if fail_at == name:
                raise RuntimeError(f"simulated failure at {name}")
            return retval
        return fn

    fb_api.resolve_page_access_token = make("resolve_page_access_token", "resolved-token")
    fb_api.upload_temp_release_asset = make("upload_temp_release_asset", {"release_id": 1, "asset_id": 2, "download_url": "https://example.com/x.mp4", "tag": "t"})
    fb_api.start_upload_session = make("start_upload_session", "fb-video-id-123")
    fb_api.upload_hosted_video = make("upload_hosted_video", None)
    fb_api.wait_for_upload_complete = make("wait_for_upload_complete", "ready")
    fb_api.finish_and_publish = make("finish_and_publish", {"video_id": "fb-video-id-123", "success": True})
    fb_api.cleanup_temp_release = make("cleanup_temp_release", None)

    def restore():
        for name, fn in originals.items():
            setattr(fb_api, name, fn)

    return restore


COMMON_KWARGS = dict(page_id="123", access_token="tok", graph_version="v26.0", github_repo="owner/repo", github_token="ghtok")


def test_dry_run_by_default_makes_no_calls_and_leaves_queue_untouched() -> None:
    os.environ.pop("REEL_PUBLISH_LIVE", None)
    with tempfile.TemporaryDirectory() as tmp:
        video = _make_valid_reel_video(Path(tmp) / "reel.mp4")
        job = _job(video)
        recorder = _Recorder()
        restore = _patch_fb_api(recorder)
        try:
            result = pub.publish_reel_job(job, **COMMON_KWARGS)
        finally:
            restore()
        assert result["dry_run"] is True and result["would_publish"] is True, result
        assert recorder.calls == [], recorder.calls
        assert job["status"] == "READY", job["status"]
    print("PASS: dry run (default) validates everything but makes zero Facebook/GitHub calls and never touches queue status")


def test_dry_run_rejects_invalid_video_without_touching_queue() -> None:
    os.environ.pop("REEL_PUBLISH_LIVE", None)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Wrong resolution -> fails check_final_render.
        bad_video = tmp / "bad.mp4"
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=10", "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(bad_video)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        job = _job(bad_video)
        raised = False
        try:
            pub.publish_reel_job(job, **COMMON_KWARGS)
        except pub.PublishError as exc:
            raised = True
            assert "video failed validation" in str(exc)
        assert raised
        assert job["status"] == "READY"
    print("PASS: an invalid video is refused before any publish attempt, queue status untouched")


def test_live_publish_happy_path_marks_published_and_cleans_up() -> None:
    os.environ["REEL_PUBLISH_LIVE"] = "true"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            video = _make_valid_reel_video(Path(tmp) / "reel.mp4")
            job = _job(video)
            recorder = _Recorder()
            restore = _patch_fb_api(recorder)
            try:
                result = pub.publish_reel_job(job, **COMMON_KWARGS)
            finally:
                restore()
            assert result["dry_run"] is False
            assert result["facebook_post_id"] == "fb-video-id-123"
            assert job["status"] == "PUBLISHED"
            assert job["facebook_post_id"] == "fb-video-id-123"
            assert "cleanup_temp_release" in recorder.calls
            assert recorder.calls.index("start_upload_session") < recorder.calls.index("finish_and_publish")
    finally:
        os.environ.pop("REEL_PUBLISH_LIVE", None)
    print("PASS: live publish (all steps faked to succeed) marks the job PUBLISHED with the real post id, cleans up the temp release")


def test_failure_before_video_id_is_retryable() -> None:
    os.environ["REEL_PUBLISH_LIVE"] = "true"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            video = _make_valid_reel_video(Path(tmp) / "reel.mp4")
            job = _job(video)
            recorder = _Recorder()
            restore = _patch_fb_api(recorder, fail_at="upload_temp_release_asset")
            try:
                raised = False
                try:
                    pub.publish_reel_job(job, **COMMON_KWARGS)
                except pub.PublishError:
                    raised = True
            finally:
                restore()
            assert raised
            assert job["status"] == "RETRY", job["status"]
    finally:
        os.environ.pop("REEL_PUBLISH_LIVE", None)
    print("PASS: a failure before Facebook ever issued a video_id is safely retryable")


def test_failure_after_video_id_is_not_retryable() -> None:
    """The critical duplicate-Reel guard: once start_upload_session()
    returns a real video_id, ANY later failure must be terminal, never
    RETRY - an automatic retry starting a second upload session would
    create a duplicate Reel on the live Page."""
    os.environ["REEL_PUBLISH_LIVE"] = "true"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            video = _make_valid_reel_video(Path(tmp) / "reel.mp4")
            job = _job(video)
            recorder = _Recorder()
            restore = _patch_fb_api(recorder, fail_at="upload_hosted_video")
            try:
                raised = False
                try:
                    pub.publish_reel_job(job, **COMMON_KWARGS)
                except pub.PublishError:
                    raised = True
            finally:
                restore()
            assert raised
            assert job["status"] == "FAILED", job["status"]
            assert "start_upload_session" in recorder.calls
            assert "cleanup_temp_release" in recorder.calls, "cleanup must still run even on failure"
    finally:
        os.environ.pop("REEL_PUBLISH_LIVE", None)
    print("PASS: a failure AFTER a real video_id exists is terminal (FAILED, not RETRY) - prevents a duplicate Reel on automatic retry")


def test_already_published_job_is_refused() -> None:
    os.environ.pop("REEL_PUBLISH_LIVE", None)
    with tempfile.TemporaryDirectory() as tmp:
        video = _make_valid_reel_video(Path(tmp) / "reel.mp4")
        job = _job(video)
        job["status"] = "PUBLISHED"
        job["facebook_post_id"] = "already-there"
        raised = False
        try:
            pub.publish_reel_job(job, **COMMON_KWARGS)
        except pub.PublishError as exc:
            raised = True
            assert "already PUBLISHED" in str(exc)
        assert raised
    print("PASS: an already-PUBLISHED job is refused outright, even in dry-run mode")


if __name__ == "__main__":
    test_dry_run_by_default_makes_no_calls_and_leaves_queue_untouched()
    test_dry_run_rejects_invalid_video_without_touching_queue()
    test_live_publish_happy_path_marks_published_and_cleans_up()
    test_failure_before_video_id_is_retryable()
    test_failure_after_video_id_is_not_retryable()
    test_already_published_job_is_refused()
    print("\nALL PHASE 10 TESTS PASSED")
