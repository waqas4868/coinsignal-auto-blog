"""Phase 6 tests for reels/scene_video_quality_check.py and
reels/scene_video_provider.py.

Uses REAL ffmpeg to synthesize test video fixtures (valid/wrong-duration/
wrong-aspect/audio-only) and real ffprobe-based validation - no mocked
subprocess output. VeoApiProvider is tested only for its disabled/
misconfigured fail-closed behavior (no live network calls, no billing).

Run: python reels/tests/test_scene_video_pipeline.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import scene_video_provider as svp  # noqa: E402
from scene_video_quality_check import check_video  # noqa: E402
from common import read_json, write_json  # noqa: E402

TARGET_DURATION = svp.SCENE_TARGET_DURATION_SECONDS  # 6.0


def _ffmpeg(*args: str) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fixture generation failed: {result.stderr}")


def _make_video_clip(path: Path, *, duration: float, width: int = 1280, height: int = 720) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=duration={duration}:size={width}x{height}:rate=25", "-pix_fmt", "yuv420p", str(path))
    return path


def _make_audio_only_clip(path: Path, *, duration: float = 3.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono", "-t", str(duration), "-c:a", "aac", str(path))
    return path


# ---------------------------------------------------------------------------
# check_video (real ffprobe against real ffmpeg-generated fixtures)
# ---------------------------------------------------------------------------

def test_accepts_valid_clip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _make_video_clip(Path(tmp) / "good.mp4", duration=TARGET_DURATION)
        ok, reason = check_video(clip, TARGET_DURATION)
        assert ok, reason
    print("PASS: accepts a real 16:9, correctly-timed clip")


def test_rejects_wrong_duration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _make_video_clip(Path(tmp) / "short.mp4", duration=1.0)
        ok, reason = check_video(clip, TARGET_DURATION)
        assert not ok and "duration" in reason, (ok, reason)
    print("PASS: rejects a clip whose duration is far from the target")


def test_rejects_wrong_aspect_ratio() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _make_video_clip(Path(tmp) / "square.mp4", duration=TARGET_DURATION, width=720, height=720)
        ok, reason = check_video(clip, TARGET_DURATION)
        assert not ok and "aspect ratio" in reason, (ok, reason)
    print("PASS: rejects a non-16:9 clip")


def test_rejects_missing_file() -> None:
    ok, reason = check_video(Path("nope/does/not/exist.mp4"), TARGET_DURATION)
    assert not ok and "missing" in reason, (ok, reason)
    print("PASS: rejects a missing file")


def test_rejects_clip_with_no_video_stream() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _make_audio_only_clip(Path(tmp) / "audio_only.mp4", duration=TARGET_DURATION)
        ok, reason = check_video(clip, TARGET_DURATION)
        assert not ok and "no video stream" in reason, (ok, reason)
    print("PASS: rejects a file with no video stream")


# ---------------------------------------------------------------------------
# FlowManualProvider
# ---------------------------------------------------------------------------

def test_flow_manual_provider_writes_manifest_and_awaits() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        provider = svp.FlowManualProvider(manifest_path=tmp / "manifest.json")
        outcome = provider.generate(
            image_path=tmp / "scene1.png", motion_prompt="animate arms only", output_path=tmp / "scene1.mp4", target_duration_seconds=TARGET_DURATION
        )
        assert outcome.status == "awaiting_manual_import", outcome
        manifest = read_json(tmp / "manifest.json", [])
        assert len(manifest) == 1
        assert manifest[0]["expected_output_path"] == str(tmp / "scene1.mp4")
        assert manifest[0]["motion_prompt"] == "animate arms only"
    print("PASS: FlowManualProvider writes a manifest entry and never claims a finished video itself")


def test_flow_manual_provider_manifest_entry_not_duplicated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        provider = svp.FlowManualProvider(manifest_path=tmp / "manifest.json")
        for _ in range(3):
            provider.generate(image_path=tmp / "scene1.png", motion_prompt="x", output_path=tmp / "scene1.mp4", target_duration_seconds=TARGET_DURATION)
        manifest = read_json(tmp / "manifest.json", [])
        assert len(manifest) == 1, f"expected exactly 1 manifest entry, got {len(manifest)}"
    print("PASS: calling generate() repeatedly for the same scene does not duplicate manifest entries")


# ---------------------------------------------------------------------------
# generate_scene_video orchestration + manual-import two-step flow
# ---------------------------------------------------------------------------

def test_generate_scene_video_reaches_awaiting_manual_import() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        providers = svp.default_providers(manifest_path=tmp / "manifest.json")
        record = svp.generate_scene_video(
            image_path=tmp / "scene1.png", motion_prompt="animate arms only", output_path=tmp / "scene1.mp4",
            target_duration_seconds=TARGET_DURATION, providers=providers, state_path=tmp / "scene1_video.json",
        )
        assert record["status"] == "VIDEO_AWAITING_MANUAL_IMPORT", record
        assert record["path"] == str(tmp / "scene1.mp4")
        assert record["provider"] == "flow_manual"
    print("PASS: with only the free manual provider, a scene reaches VIDEO_AWAITING_MANUAL_IMPORT")


def test_complete_manual_import_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state_path = tmp / "scene1_video.json"
        output_path = tmp / "scene1.mp4"
        providers = svp.default_providers(manifest_path=tmp / "manifest.json")
        svp.generate_scene_video(
            image_path=tmp / "scene1.png", motion_prompt="x", output_path=output_path,
            target_duration_seconds=TARGET_DURATION, providers=providers, state_path=state_path,
        )
        # Simulate a human placing the Flow-generated clip at the expected path.
        _make_video_clip(output_path, duration=TARGET_DURATION)

        record = svp.complete_manual_import(state_path, TARGET_DURATION)
        assert record["status"] == "VIDEO_READY", record
        assert record["checksum"] == svp._sha256_of_file(output_path)
    print("PASS: completing a manual import with a valid real clip finalizes the record to VIDEO_READY")


def test_complete_manual_import_rejects_invalid_clip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state_path = tmp / "scene1_video.json"
        output_path = tmp / "scene1.mp4"
        providers = svp.default_providers(manifest_path=tmp / "manifest.json")
        svp.generate_scene_video(
            image_path=tmp / "scene1.png", motion_prompt="x", output_path=output_path,
            target_duration_seconds=TARGET_DURATION, providers=providers, state_path=state_path,
        )
        _make_video_clip(output_path, duration=1.0)  # human placed the wrong clip

        record = svp.complete_manual_import(state_path, TARGET_DURATION)
        assert record["status"] == "VIDEO_FAILED", record
        assert "duration" in record["error"]
    print("PASS: completing a manual import with an invalid clip marks the record VIDEO_FAILED, not READY")


def test_complete_manual_import_raises_if_not_awaiting() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "scene1_video.json"
        write_json(state_path, {"status": "VIDEO_GENERATING"})
        raised = False
        try:
            svp.complete_manual_import(state_path, TARGET_DURATION)
        except ValueError:
            raised = True
        assert raised
    print("PASS: complete_manual_import refuses to run on a state that isn't VIDEO_AWAITING_MANUAL_IMPORT")


def test_already_ready_video_never_regenerated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        output_path = tmp / "scene1.mp4"
        _make_video_clip(output_path, duration=TARGET_DURATION)
        preexisting = {
            "motion_prompt": "old", "image_path": "old.png", "provider": "flow_manual", "generation_id": "",
            "path": str(output_path), "checksum": svp._sha256_of_file(output_path), "status": "VIDEO_READY",
            "created_at": "2026-01-01T00:00:00+00:00", "ready_at": "2026-01-01T00:00:01+00:00", "error": "",
        }
        state_path = tmp / "scene1_video.json"
        write_json(state_path, preexisting)

        class SpyProvider:
            name = "spy"

            def generate(self, **kwargs):
                raise AssertionError("provider must not be called when the video is already VIDEO_READY")

        record = svp.generate_scene_video(
            image_path=tmp / "scene1.png", motion_prompt="a totally different prompt", output_path=output_path,
            target_duration_seconds=TARGET_DURATION, providers=[SpyProvider()], state_path=state_path,
        )
        assert record == preexisting
    print("PASS: a scene already VIDEO_READY is never regenerated")


def test_fallback_and_all_providers_failing() -> None:
    class FailingProvider:
        def __init__(self, name):
            self.name = name

        def generate(self, **kwargs):
            return svp.VideoOutcome(status="failed", error=f"{self.name} simulated failure")

    class ReadyProvider:
        name = "ready_fake"

        def generate(self, *, output_path, **kwargs):
            _make_video_clip(output_path, duration=TARGET_DURATION)
            return svp.VideoOutcome(status="ready", generation_id="ready-fake-1")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        record = svp.generate_scene_video(
            image_path=tmp / "scene1.png", motion_prompt="x", output_path=tmp / "scene1.mp4",
            target_duration_seconds=TARGET_DURATION, providers=[FailingProvider("first"), ReadyProvider()],
            state_path=tmp / "scene1_video.json",
        )
        assert record["status"] == "VIDEO_READY" and record["provider"] == "ready_fake", record

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        record = svp.generate_scene_video(
            image_path=tmp / "scene1.png", motion_prompt="x", output_path=tmp / "scene1.mp4",
            target_duration_seconds=TARGET_DURATION, providers=[FailingProvider("first"), FailingProvider("second")],
            state_path=tmp / "scene1_video.json",
        )
        assert record["status"] == "VIDEO_FAILED" and "second" in record["error"], record
    print("PASS: falls back past a failing provider to a working one; marks VIDEO_FAILED when all fail")


# ---------------------------------------------------------------------------
# VeoApiProvider - fail-closed behavior only, no live network calls
# ---------------------------------------------------------------------------

def test_veo_disabled_by_default_never_touches_network_or_files() -> None:
    os.environ.pop("ALLOW_PAID_VIDEO_PROVIDERS", None)
    provider = svp.VeoApiProvider(project_id="fake-project", access_token="fake-token")
    outcome = provider.generate(
        image_path=Path("this/file/does/not/exist.png"),  # would raise if the code ever tried to read it
        motion_prompt="x", output_path=Path("nope.mp4"), target_duration_seconds=TARGET_DURATION,
    )
    assert outcome.status == "failed"
    assert "PROVIDER_REQUIRES_PAID_ACCESS" in outcome.error, outcome.error
    print("PASS: VeoApiProvider is disabled by default and short-circuits before touching any file or network")


def test_veo_enabled_but_missing_credentials_fails_closed() -> None:
    os.environ["ALLOW_PAID_VIDEO_PROVIDERS"] = "true"
    try:
        provider = svp.VeoApiProvider(project_id="", access_token="")
        outcome = provider.generate(
            image_path=Path("this/file/does/not/exist.png"), motion_prompt="x", output_path=Path("nope.mp4"),
            target_duration_seconds=TARGET_DURATION,
        )
        assert outcome.status == "failed"
        assert "PROVIDER_UNAVAILABLE" in outcome.error, outcome.error
    finally:
        os.environ.pop("ALLOW_PAID_VIDEO_PROVIDERS", None)
    print("PASS: enabling the flag without real credentials still fails closed (no network call attempted)")


def test_default_providers_never_includes_veo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        providers = svp.default_providers(manifest_path=Path(tmp) / "manifest.json")
        names = [p.name for p in providers]
        assert "veo_api" not in names, names
        assert names == ["flow_manual"], names
    print("PASS: default_providers() never includes the paid Veo provider")


if __name__ == "__main__":
    test_accepts_valid_clip()
    test_rejects_wrong_duration()
    test_rejects_wrong_aspect_ratio()
    test_rejects_missing_file()
    test_rejects_clip_with_no_video_stream()
    test_flow_manual_provider_writes_manifest_and_awaits()
    test_flow_manual_provider_manifest_entry_not_duplicated()
    test_generate_scene_video_reaches_awaiting_manual_import()
    test_complete_manual_import_success()
    test_complete_manual_import_rejects_invalid_clip()
    test_complete_manual_import_raises_if_not_awaiting()
    test_already_ready_video_never_regenerated()
    test_fallback_and_all_providers_failing()
    test_veo_disabled_by_default_never_touches_network_or_files()
    test_veo_enabled_but_missing_credentials_fails_closed()
    test_default_providers_never_includes_veo()
    print("\nALL PHASE 6 TESTS PASSED")
