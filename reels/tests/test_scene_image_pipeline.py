"""Phase 5 tests for reels/scene_image_quality_check.py and
reels/scene_image_provider.py. No live network calls (fake providers) -
the validator itself IS exercised against real image bytes, including one
manual check earlier this phase against a real (and genuinely bad) live
Pollinations response, documented in the Phase 5 report.

Run: python reels/tests/test_scene_image_pipeline.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import scene_image_provider as sip  # noqa: E402
from scene_image_quality_check import check_image  # noqa: E402


WIDTH, HEIGHT = sip.SCENE_WIDTH, sip.SCENE_HEIGHT


def _save(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def _draw_valid_stickman(width: int = WIDTH, height: int = HEIGHT) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    cx, cy, r = width // 2, height // 3, 60
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=5)
    draw.line([(cx, cy + r), (cx, cy + r + 150)], fill="black", width=5)
    draw.line([(cx, cy + r + 150), (cx - 60, cy + r + 300)], fill="black", width=5)
    draw.line([(cx, cy + r + 150), (cx + 60, cy + r + 300)], fill="black", width=5)
    return img


# ---------------------------------------------------------------------------
# check_image
# ---------------------------------------------------------------------------

def test_rejects_wrong_aspect_ratio() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _save(_draw_valid_stickman(720, 720), Path(tmp) / "square.png")
        ok, reason = check_image(path)
        assert not ok and "aspect ratio" in reason, (ok, reason)
    print("PASS: rejects a non-16:9 image")


def test_rejects_blank_white_image() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _save(Image.new("RGB", (WIDTH, HEIGHT), "white"), Path(tmp) / "blank.png")
        ok, reason = check_image(path)
        assert not ok and "near-blank" in reason, (ok, reason)
    print("PASS: rejects a pure-white (nothing drawn) image")


def test_rejects_solid_dense_image() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _save(Image.new("RGB", (WIDTH, HEIGHT), "black"), Path(tmp) / "solid.png")
        ok, reason = check_image(path)
        assert not ok and "dense/corrupt" in reason, (ok, reason)
    print("PASS: rejects a solid-black (corrupt/inverted) image")


def test_rejects_missing_file() -> None:
    ok, reason = check_image(Path("this/does/not/exist.png"))
    assert not ok and "missing" in reason, (ok, reason)
    print("PASS: rejects a missing file")


def test_accepts_valid_line_drawing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _save(_draw_valid_stickman(), Path(tmp) / "good.png")
        ok, reason = check_image(path)
        assert ok, reason
    print("PASS: accepts a real 16:9 minimalist line drawing (same shape of content Pollinations failed to produce)")


# ---------------------------------------------------------------------------
# generate_scene_image / provider fallback / state machine
# ---------------------------------------------------------------------------

class FakeProvider:
    def __init__(self, name: str, *, succeeds: bool, valid_output: bool = True, error: str = ""):
        self.name = name
        self.succeeds = succeeds
        self.valid_output = valid_output
        self.error = error
        self.call_count = 0

    def generate(self, prompt: str, output_path: Path) -> sip.ProviderOutcome:
        self.call_count += 1
        if not self.succeeds:
            return sip.ProviderOutcome(False, error=self.error or f"{self.name} simulated failure")
        img = _draw_valid_stickman() if self.valid_output else Image.new("RGB", (WIDTH, HEIGHT), "white")
        _save(img, output_path)
        return sip.ProviderOutcome(True, generation_id=f"{self.name}-gen-1")


def test_falls_back_to_second_provider_on_network_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        first = FakeProvider("first", succeeds=False, error="network error: timed out")
        second = FakeProvider("second", succeeds=True)
        record = sip.generate_scene_image(
            prompt="a test scene", output_path=tmp / "scene1.png", providers=[first, second], state_path=tmp / "scene1.json"
        )
        assert record["status"] == "IMAGE_READY", record
        assert record["provider"] == "second", record
        assert first.call_count == 1 and second.call_count == 1
    print("PASS: falls back to the second provider when the first fails outright")


def test_falls_back_when_first_provider_output_fails_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        first = FakeProvider("first", succeeds=True, valid_output=False)  # "succeeds" but writes a blank image
        second = FakeProvider("second", succeeds=True, valid_output=True)
        record = sip.generate_scene_image(
            prompt="a test scene", output_path=tmp / "scene1.png", providers=[first, second], state_path=tmp / "scene1.json"
        )
        assert record["status"] == "IMAGE_READY", record
        assert record["provider"] == "second", record
        assert first.call_count == 1 and second.call_count == 1
    print("PASS: a provider that 'succeeds' but produces an invalid image is not accepted - falls through to the next provider")


def test_all_providers_failing_marks_image_failed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        first = FakeProvider("first", succeeds=False, error="network error: A")
        second = FakeProvider("second", succeeds=False, error="network error: B")
        record = sip.generate_scene_image(
            prompt="a test scene", output_path=tmp / "scene1.png", providers=[first, second], state_path=tmp / "scene1.json"
        )
        assert record["status"] == "IMAGE_FAILED", record
        assert "second" in record["error"], record
        assert not (tmp / "scene1.png").exists(), "no image file should be left behind when every provider fails"
    print("PASS: when every provider fails, the record is marked IMAGE_FAILED with the last error, no stray file left")


def test_state_record_has_correct_checksum_and_dimensions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        provider = FakeProvider("only", succeeds=True)
        output_path = tmp / "scene1.png"
        record = sip.generate_scene_image(
            prompt="scene prompt text", output_path=output_path, providers=[provider], state_path=tmp / "scene1.json"
        )
        assert record["width"] == WIDTH and record["height"] == HEIGHT, record
        assert record["checksum"] == sip._sha256_of_file(output_path), "checksum must be the real sha256 of the saved file"
        assert record["generation_id"] == "only-gen-1"
        assert record["prompt"] == "scene prompt text"
    print("PASS: state record carries the real checksum, correct dimensions, provider, and generation_id")


def test_already_ready_image_is_never_regenerated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        output_path = tmp / "scene1.png"
        _save(_draw_valid_stickman(), output_path)
        preexisting = {
            "prompt": "old prompt",
            "provider": "pollinations",
            "generation_id": "old-gen",
            "path": str(output_path),
            "checksum": sip._sha256_of_file(output_path),
            "width": WIDTH,
            "height": HEIGHT,
            "status": "IMAGE_READY",
            "created_at": "2026-01-01T00:00:00+00:00",
            "ready_at": "2026-01-01T00:00:01+00:00",
            "error": "",
        }
        from common import write_json

        state_path = tmp / "scene1.json"
        write_json(state_path, preexisting)

        def must_not_be_called(prompt, output_path):
            raise AssertionError("provider must not be called when the image is already IMAGE_READY")

        class SpyProvider:
            name = "spy"
            generate = staticmethod(must_not_be_called)

        record = sip.generate_scene_image(
            prompt="a completely different new prompt", output_path=output_path, providers=[SpyProvider()], state_path=state_path
        )
        assert record == preexisting, "an already-READY image must be returned unchanged, never regenerated"
    print("PASS: a scene image already IMAGE_READY is never regenerated, even with a different prompt/providers passed in")


if __name__ == "__main__":
    test_rejects_wrong_aspect_ratio()
    test_rejects_blank_white_image()
    test_rejects_solid_dense_image()
    test_rejects_missing_file()
    test_accepts_valid_line_drawing()
    test_falls_back_to_second_provider_on_network_failure()
    test_falls_back_when_first_provider_output_fails_validation()
    test_all_providers_failing_marks_image_failed()
    test_state_record_has_correct_checksum_and_dimensions()
    test_already_ready_image_is_never_regenerated()
    print("\nALL PHASE 5 TESTS PASSED")
