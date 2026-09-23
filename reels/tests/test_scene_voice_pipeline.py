"""Phase 7 tests for reels/scene_voice_quality_check.py and
reels/scene_voice_provider.py.

Uses real ffmpeg/ffprobe against real (tiny, stdlib-generated) WAV
fixtures for the validator - no mocked subprocess output. PiperProvider's
actual synthesis call is NOT exercised here: the local sandbox's disk was
completely full this phase (140GB/140GB used) so the ~60MB voice model
could not be downloaded - see the Phase 7 report. What IS tested for real:
the CLI-invocation contract (missing-model fail path) and the full
provider-fallback/state-machine orchestration via fake providers.

Run: python reels/tests/test_scene_voice_pipeline.py
"""
from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import scene_voice_provider as svp  # noqa: E402
from scene_voice_quality_check import check_voice_audio  # noqa: E402
from common import write_json  # noqa: E402


def _write_wav(path: Path, *, duration: float, freq: float = 440.0, amplitude: int = 8000, sample_rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sample_rate * duration)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = b"".join(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate))) for i in range(n))
        w.writeframes(frames)
    return path


def _write_silent_wav(path: Path, *, duration: float, sample_rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sample_rate * duration)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n)
    return path


def _write_video_only_clip(path: Path, *, duration: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fixture generation failed: {result.stderr}")
    return path


# ---------------------------------------------------------------------------
# check_voice_audio (real ffmpeg/ffprobe against real fixtures)
# ---------------------------------------------------------------------------

def test_accepts_real_audible_clip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _write_wav(Path(tmp) / "tone.wav", duration=2.0)
        ok, reason = check_voice_audio(clip)
        assert ok, reason
    print("PASS: accepts a real audible short clip")


def test_rejects_silent_clip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _write_silent_wav(Path(tmp) / "silent.wav", duration=2.0)
        ok, reason = check_voice_audio(clip)
        assert not ok and "silent" in reason, (ok, reason)
    print("PASS: rejects a genuinely silent clip (verified real ffmpeg volumedetect: ~-91dB vs ~-15dB for tone)")


def test_rejects_too_short_clip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _write_wav(Path(tmp) / "tiny.wav", duration=0.05)
        ok, reason = check_voice_audio(clip)
        assert not ok and "below the minimum" in reason, (ok, reason)
    print("PASS: rejects a near-zero-duration clip (likely a failed/empty synthesis)")


def test_rejects_too_long_clip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _write_wav(Path(tmp) / "long.wav", duration=2.0)
        ok, reason = check_voice_audio(clip, max_duration=1.0)
        assert not ok and "exceeds the maximum" in reason, (ok, reason)
    print("PASS: rejects a clip longer than the configured maximum")


def test_rejects_missing_file() -> None:
    ok, reason = check_voice_audio(Path("nope/does/not/exist.wav"))
    assert not ok and "missing" in reason, (ok, reason)
    print("PASS: rejects a missing file")


def test_rejects_file_with_no_audio_stream() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = _write_video_only_clip(Path(tmp) / "video_only.mp4")
        ok, reason = check_voice_audio(clip)
        assert not ok and "no audio stream" in reason, (ok, reason)
    print("PASS: rejects a file with no audio stream")


# ---------------------------------------------------------------------------
# generate_scene_voice orchestration (fake providers, no real TTS calls)
# ---------------------------------------------------------------------------

class FakeProvider:
    def __init__(self, name: str, *, succeeds: bool, valid_output: bool = True, error: str = ""):
        self.name = name
        self.succeeds = succeeds
        self.valid_output = valid_output
        self.error = error
        self.call_count = 0

    def generate(self, text: str, output_path: Path) -> svp.VoiceOutcome:
        self.call_count += 1
        if not self.succeeds:
            return svp.VoiceOutcome(False, error=self.error or f"{self.name} simulated failure")
        if self.valid_output:
            _write_wav(output_path, duration=1.5)
        else:
            _write_silent_wav(output_path, duration=1.5)
        return svp.VoiceOutcome(True, generation_id=f"{self.name}-gen-1")


def test_falls_back_to_second_provider_on_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        first = FakeProvider("first", succeeds=False, error="network error")
        second = FakeProvider("second", succeeds=True)
        record = svp.generate_scene_voice(text="Bitcoin just broke resistance.", output_path=tmp / "s1.wav", providers=[first, second], state_path=tmp / "s1.json")
        assert record["status"] == "VOICE_READY" and record["provider"] == "second", record
        assert first.call_count == 1 and second.call_count == 1
    print("PASS: falls back to the second provider when the first fails outright")


def test_falls_back_when_output_fails_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        first = FakeProvider("first", succeeds=True, valid_output=False)  # "succeeds" but produces silence
        second = FakeProvider("second", succeeds=True, valid_output=True)
        record = svp.generate_scene_voice(text="text", output_path=tmp / "s1.wav", providers=[first, second], state_path=tmp / "s1.json")
        assert record["status"] == "VOICE_READY" and record["provider"] == "second", record
    print("PASS: a provider that 'succeeds' but produces silent audio is not accepted - falls through")


def test_all_providers_failing_marks_voice_failed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        record = svp.generate_scene_voice(
            text="text", output_path=tmp / "s1.wav",
            providers=[FakeProvider("first", succeeds=False, error="A"), FakeProvider("second", succeeds=False, error="B")],
            state_path=tmp / "s1.json",
        )
        assert record["status"] == "VOICE_FAILED" and "second" in record["error"], record
        assert not (tmp / "s1.wav").exists()
    print("PASS: when every provider fails, marks VOICE_FAILED with the last error, no stray file")


def test_already_ready_voice_never_regenerated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        output_path = tmp / "s1.wav"
        _write_wav(output_path, duration=1.5)
        preexisting = {
            "text": "old text", "provider": "piper", "generation_id": "old-gen", "path": str(output_path),
            "checksum": svp._sha256_of_file(output_path), "status": "VOICE_READY",
            "created_at": "2026-01-01T00:00:00+00:00", "ready_at": "2026-01-01T00:00:01+00:00", "error": "",
        }
        state_path = tmp / "s1.json"
        write_json(state_path, preexisting)

        class SpyProvider:
            name = "spy"

            def generate(self, text, output_path):
                raise AssertionError("provider must not be called when the voice is already VOICE_READY")

        record = svp.generate_scene_voice(text="a totally different new line", output_path=output_path, providers=[SpyProvider()], state_path=state_path)
        assert record == preexisting
    print("PASS: a scene voice already VOICE_READY is never regenerated")


def test_state_record_has_correct_checksum() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        output_path = tmp / "s1.wav"
        record = svp.generate_scene_voice(text="Bitcoin just broke resistance.", output_path=output_path, providers=[FakeProvider("only", succeeds=True)], state_path=tmp / "s1.json")
        assert record["checksum"] == svp._sha256_of_file(output_path)
        assert record["text"] == "Bitcoin just broke resistance."
        assert record["generation_id"] == "only-gen-1"
    print("PASS: state record carries the real checksum, text, and generation_id")


# ---------------------------------------------------------------------------
# PiperProvider / ElevenLabsProvider gating (no live calls)
# ---------------------------------------------------------------------------

def test_piper_missing_model_returns_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        provider = svp.PiperProvider(model_path=Path(tmp) / "nonexistent-voice.onnx")
        outcome = provider.generate("hello", Path(tmp) / "out.wav")
        assert not outcome.success
        assert "PROVIDER_UNAVAILABLE" in outcome.error, outcome.error
    print("PASS: PiperProvider fails cleanly (PROVIDER_UNAVAILABLE) when its voice model isn't present, without crashing")


def test_elevenlabs_disabled_by_default_never_touches_network() -> None:
    os.environ.pop("ALLOW_PAID_VOICE_PROVIDERS", None)
    provider = svp.ElevenLabsProvider(api_key="fake-key", voice_id="fake-voice")
    outcome = provider.generate("hello", Path("nope.mp3"))
    assert not outcome.success
    assert "PROVIDER_REQUIRES_PAID_ACCESS" in outcome.error, outcome.error
    print("PASS: ElevenLabsProvider is disabled by default and short-circuits before any network call")


def test_elevenlabs_enabled_but_missing_credentials_fails_closed() -> None:
    os.environ["ALLOW_PAID_VOICE_PROVIDERS"] = "true"
    try:
        provider = svp.ElevenLabsProvider(api_key="", voice_id="")
        outcome = provider.generate("hello", Path("nope.mp3"))
        assert not outcome.success
        assert "PROVIDER_UNAVAILABLE" in outcome.error, outcome.error
    finally:
        os.environ.pop("ALLOW_PAID_VOICE_PROVIDERS", None)
    print("PASS: enabling the flag without real credentials still fails closed")


def test_default_providers_never_includes_elevenlabs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        providers = svp.default_providers(model_path=Path(tmp) / "voice.onnx")
        names = [p.name for p in providers]
        assert names == ["piper"], names
    print("PASS: default_providers() is Piper only - the paid ElevenLabs provider is never included by default")


if __name__ == "__main__":
    test_accepts_real_audible_clip()
    test_rejects_silent_clip()
    test_rejects_too_short_clip()
    test_rejects_too_long_clip()
    test_rejects_missing_file()
    test_rejects_file_with_no_audio_stream()
    test_falls_back_to_second_provider_on_failure()
    test_falls_back_when_output_fails_validation()
    test_all_providers_failing_marks_voice_failed()
    test_already_ready_voice_never_regenerated()
    test_state_record_has_correct_checksum()
    test_piper_missing_model_returns_unavailable()
    test_elevenlabs_disabled_by_default_never_touches_network()
    test_elevenlabs_enabled_but_missing_credentials_fails_closed()
    test_default_providers_never_includes_elevenlabs()
    print("\nALL PHASE 7 TESTS PASSED")
