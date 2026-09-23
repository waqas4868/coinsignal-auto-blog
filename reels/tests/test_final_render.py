"""Phase 8 tests for reels/final_render.py.

Uses real ffmpeg to synthesize tiny (short-duration, low-resolution) test
fixtures and real ffprobe/ffmpeg calls throughout - no mocked subprocess
output. Fixtures are kept small deliberately: this sandbox's disk was
critically low this phase (see the Phase 8 report).

Run: python reels/tests/test_final_render.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import final_render as fr  # noqa: E402


def _make_video(path: Path, *, duration: float, width: int = 320, height: int = 180) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={width}x{height}:rate=15", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return path


def _make_voice(path: Path, *, duration: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return path


def _probe(path: Path) -> dict:
    import json

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# compute_scene_timing / wrap_caption (pure)
# ---------------------------------------------------------------------------

def test_compute_scene_timing_video_shorter_than_voice() -> None:
    timing = fr.compute_scene_timing(video_duration=3.0, voice_duration=5.0)
    assert timing["output_duration"] == 5.0
    assert abs(timing["video_pad_seconds"] - 2.0) < 1e-9
    assert timing["needs_trim"] is False
    print("PASS: a shorter video gets a freeze-extend plan matching the voice duration")


def test_compute_scene_timing_video_longer_than_voice() -> None:
    timing = fr.compute_scene_timing(video_duration=8.0, voice_duration=4.0)
    assert timing["output_duration"] == 4.0
    assert timing["video_pad_seconds"] == 0.0
    assert timing["needs_trim"] is True
    print("PASS: a longer video gets a trim plan matching the voice duration (voice is never sped up)")


def test_wrap_caption_breaks_long_text_into_lines() -> None:
    wrapped = fr.wrap_caption("Bitcoin just broke through a key resistance level today", max_chars=20)
    lines = wrapped.split("\n")
    assert len(lines) > 1
    assert all(len(line) <= 25 for line in lines)  # a little slack for the last word that tips a line over
    assert " ".join(lines).replace("  ", " ") == "Bitcoin just broke through a key resistance level today"
    print("PASS: caption wrapping breaks long text into multiple lines without dropping words")


def test_resolve_caption_font_finds_a_real_font() -> None:
    font = fr.resolve_caption_font()
    assert font.exists(), font
    print(f"PASS: a real usable caption font was resolved ({font})")


# ---------------------------------------------------------------------------
# render_scene_clip (real ffmpeg)
# ---------------------------------------------------------------------------

def test_render_scene_clip_freeze_extends_and_matches_voice_duration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = _make_video(tmp / "v.mp4", duration=1.0)
        voice = _make_voice(tmp / "a.wav", duration=2.5)
        output = tmp / "scene.mp4"
        timing = fr.render_scene_clip(
            video_path=video, voice_path=voice, caption_text="Bitcoin just broke key resistance today.",
            output_path=output, caption_file_path=tmp / "caption.txt",
        )
        assert timing["video_pad_seconds"] > 0
        probe = _probe(output)
        video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        assert video_stream["width"] == fr.FINAL_WIDTH and video_stream["height"] == fr.FINAL_HEIGHT
        assert abs(float(probe["format"]["duration"]) - 2.5) < 0.15
    print("PASS: a scene shorter than its voice is freeze-extended to 1080x1920 matching the voice's duration")


def test_render_scene_clip_trims_when_video_longer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = _make_video(tmp / "v.mp4", duration=3.0)
        voice = _make_voice(tmp / "a.wav", duration=1.0)
        output = tmp / "scene.mp4"
        fr.render_scene_clip(video_path=video, voice_path=voice, caption_text="Short line.", output_path=output, caption_file_path=tmp / "caption.txt")
        probe = _probe(output)
        assert abs(float(probe["format"]["duration"]) - 1.0) < 0.15
    print("PASS: a scene longer than its voice is trimmed to the voice's duration")


# ---------------------------------------------------------------------------
# concat_scene_clips
# ---------------------------------------------------------------------------

def test_concat_produces_correct_total_duration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clips = []
        for i, dur in enumerate([1.0, 1.5]):
            video = _make_video(tmp / f"v{i}.mp4", duration=dur)
            voice = _make_voice(tmp / f"a{i}.wav", duration=dur)
            clip_path = tmp / f"clip{i}.mp4"
            fr.render_scene_clip(video_path=video, voice_path=voice, caption_text=f"Scene {i}.", output_path=clip_path, caption_file_path=tmp / f"cap{i}.txt")
            clips.append(clip_path)

        final = tmp / "final.mp4"
        fr.concat_scene_clips(clips, final, tmp / "list.txt")
        probe = _probe(final)
        video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        assert video_stream["width"] == fr.FINAL_WIDTH and video_stream["height"] == fr.FINAL_HEIGHT
        assert abs(float(probe["format"]["duration"]) - 2.5) < 0.2
    print("PASS: concatenated clips produce the correct 1080x1920 output with the summed duration")


# ---------------------------------------------------------------------------
# check_final_render
# ---------------------------------------------------------------------------

def test_check_final_render_accepts_a_real_valid_clip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = _make_video(tmp / "v.mp4", duration=1.0)
        voice = _make_voice(tmp / "a.wav", duration=1.0)
        output = tmp / "scene.mp4"
        fr.render_scene_clip(video_path=video, voice_path=voice, caption_text="Scene.", output_path=output, caption_file_path=tmp / "cap.txt")
        ok, reason, summary = fr.check_final_render(output)
        assert ok, reason
        assert summary["width"] == fr.FINAL_WIDTH and summary["video_codec"] == "h264" and summary["audio_codec"] == "aac"
    print("PASS: check_final_render accepts a real, correctly composed clip")


def test_check_final_render_rejects_wrong_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = _make_video(tmp / "v.mp4", duration=1.0, width=640, height=360)
        voice = _make_voice(tmp / "a.wav", duration=1.0)
        output = tmp / "scene.mp4"
        # render_scene_clip always normalizes to 1080x1920, so build the
        # wrong-resolution fixture directly (with real audio, to isolate
        # this test to the resolution check rather than the audio check).
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(voice), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(output)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        ok, reason, _ = fr.check_final_render(output)
        assert not ok and "resolution" in reason, (ok, reason)
    print("PASS: check_final_render rejects the wrong resolution")


def test_check_final_render_rejects_missing_audio() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        video_only = _make_video(Path(tmp) / "video_only.mp4", duration=1.0, width=fr.FINAL_WIDTH, height=fr.FINAL_HEIGHT)
        ok, reason, _ = fr.check_final_render(video_only)
        assert not ok and "no audio stream" in reason, (ok, reason)
    print("PASS: check_final_render rejects a clip with no audio stream")


# ---------------------------------------------------------------------------
# render_final_reel (end-to-end orchestration)
# ---------------------------------------------------------------------------

def test_render_final_reel_end_to_end_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        scenes = []
        for i in range(2):
            video = _make_video(tmp / f"scene{i}_v.mp4", duration=1.0)
            voice = _make_voice(tmp / f"scene{i}_a.wav", duration=1.2)
            scenes.append({"video_path": str(video), "voice_path": str(voice), "caption_text": f"Scene {i} caption."})

        output = tmp / "final_reel.mp4"
        state_path = tmp / "render_state.json"
        work_dir = tmp / "work"
        record = fr.render_final_reel(scenes=scenes, output_path=output, work_dir=work_dir, state_path=state_path)
        assert record["status"] == "RENDER_READY", record
        assert record["width"] == fr.FINAL_WIDTH and record["video_codec"] == "h264"
        assert abs(record["duration_seconds"] - 2.4) < 0.3

        # Corrupt the scene inputs so a re-render would necessarily fail -
        # proves the idempotent early-return path is what's actually taken.
        for scene in scenes:
            Path(scene["video_path"]).unlink()
            Path(scene["voice_path"]).unlink()

        record2 = fr.render_final_reel(scenes=scenes, output_path=output, work_dir=work_dir, state_path=state_path)
        assert record2["status"] == "RENDER_READY"
        assert record2["path"] == str(output)
    print("PASS: end-to-end render produces a valid final MP4, and a rerun is idempotent (inputs deleted, still succeeds)")


def test_render_final_reel_failure_persists_failed_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        scenes = [{"video_path": str(tmp / "does_not_exist.mp4"), "voice_path": str(tmp / "also_missing.wav"), "caption_text": "x"}]
        output = tmp / "final_reel.mp4"
        state_path = tmp / "render_state.json"

        raised = False
        try:
            fr.render_final_reel(scenes=scenes, output_path=output, work_dir=tmp / "work", state_path=state_path)
        except fr.RenderError:
            raised = True
        assert raised

        from common import read_json

        state = read_json(state_path, {})
        assert state.get("status") == "RENDER_FAILED", state
        assert not output.exists(), "no output file should be left behind after a failed render"
    print("PASS: a render failure raises RenderError and persists RENDER_FAILED state, no stray output file")


if __name__ == "__main__":
    test_compute_scene_timing_video_shorter_than_voice()
    test_compute_scene_timing_video_longer_than_voice()
    test_wrap_caption_breaks_long_text_into_lines()
    test_resolve_caption_font_finds_a_real_font()
    test_render_scene_clip_freeze_extends_and_matches_voice_duration()
    test_render_scene_clip_trims_when_video_longer()
    test_concat_produces_correct_total_duration()
    test_check_final_render_accepts_a_real_valid_clip()
    test_check_final_render_rejects_wrong_resolution()
    test_check_final_render_rejects_missing_audio()
    test_render_final_reel_end_to_end_and_idempotent()
    test_render_final_reel_failure_persists_failed_state()
    print("\nALL PHASE 8 TESTS PASSED")
