"""FreeTestBackend: Wan2.2 14B I2V (Lightning LoRA) via the zerogpu-aoti Hugging
Face Space, called through the free gradio_client API. $0 cost, ZeroGPU.

Evaluated against the requirements and selected over the two alternatives
actually checked (see conversation record, not reproduced here):
- HunyuanVideo-Avatar: no working free public environment found (3 community
  Spaces all broken at evaluation time - build error, storage limit exceeded,
  scheduling failure).
- LongCat-Video-Avatar: talking-head/portrait only, fails the full-body
  motion requirement.

What this backend does NOT support (report honestly, never fake it):
- No native audio-driven lip sync.
- No native multi-character scene coordination - generate_multi_character_scene
  and generate_dialogue_scene are NotImplementedError, not faked.

Preflight cost calculator: reverse-engineered directly from the Space's own
source (zerogpu-aoti/wan2-2-fp8da-aoti-faster/blob/main/app.py, get_duration())
rather than guessed - see _predict_gpu_seconds(). Verified empirically: a
predicted 29.0s request produced a generation whose actual output dimensions
(480x832, 49 frames) matched the formula's inputs exactly.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image

from config import COST_MODE, NoFreeBackendAvailable, QuotaExceeded
from motion_backend import MotionBackend, SceneResult

SPACE_ID = "zerogpu-aoti/wan2-2-fp8da-aoti-faster"

# --- Constants copied from the Space's own app.py (get_duration/resize_image) ---
MAX_DIM, MIN_DIM, SQUARE_DIM, MULTIPLE_OF = 832, 480, 640, 16
FIXED_FPS, MIN_FRAMES_MODEL, MAX_FRAMES_MODEL = 16, 8, 80
BASE_FRAMES_HEIGHT_WIDTH = 81 * 832 * 624
BASE_STEP_DURATION = 15


def _resized_dims(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as img:
        width, height = img.size
    if width == height:
        return SQUARE_DIM, SQUARE_DIM
    aspect_ratio = width / height
    max_ar = MAX_DIM / MIN_DIM
    min_ar = MIN_DIM / MAX_DIM
    if aspect_ratio > max_ar:
        target_w, target_h = MAX_DIM, MIN_DIM
    elif aspect_ratio < min_ar:
        target_w, target_h = MIN_DIM, MAX_DIM
    elif width > height:
        target_w = MAX_DIM
        target_h = round(target_w / aspect_ratio)
    else:
        target_h = MAX_DIM
        target_w = round(target_h * aspect_ratio)
    final_w = max(MIN_DIM, min(MAX_DIM, round(target_w / MULTIPLE_OF) * MULTIPLE_OF))
    final_h = max(MIN_DIM, min(MAX_DIM, round(target_h / MULTIPLE_OF) * MULTIPLE_OF))
    return final_w, final_h


def _num_frames(duration_seconds: float) -> int:
    return 1 + int(min(max(round(duration_seconds * FIXED_FPS), MIN_FRAMES_MODEL), MAX_FRAMES_MODEL))


def _predict_gpu_seconds(reference_image: Path, steps: int, duration_seconds: float) -> float:
    """Reimplements the Space's get_duration() so we can preflight-check cost
    BEFORE spending a real generation attempt on it."""
    width, height = _resized_dims(reference_image)
    frames = _num_frames(duration_seconds)
    factor = frames * width * height / BASE_FRAMES_HEIGHT_WIDTH
    step_duration = BASE_STEP_DURATION * factor**1.5
    return 10 + int(steps) * step_duration


_RESET_TIME_RE = re.compile(r"Try again in ([0-9:]+)")


class FreeTestBackend(MotionBackend):
    def __init__(self):
        if COST_MODE != "FREE_ONLY":
            raise NoFreeBackendAvailable(f"FreeTestBackend requires COST_MODE=FREE_ONLY, got {COST_MODE!r}")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from gradio_client import Client

            self._client = Client(SPACE_ID)
        return self._client

    def capabilities(self) -> dict[str, Any]:
        return {
            "backend": "FreeTestBackend (Wan2.2 14B I2V, zerogpu-aoti Space)",
            "cost_mode": COST_MODE,
            "single_character_motion": True,
            "audio_driven_lipsync": False,
            "multi_character_scene": False,
            "dialogue_scene": False,
            "max_duration_seconds": MAX_FRAMES_MODEL / FIXED_FPS,
            "min_duration_seconds": MIN_FRAMES_MODEL / FIXED_FPS,
            "notes": "General image-to-video motion model, not an audio-driven avatar model.",
        }

    def health_check(self) -> tuple[bool, str]:
        try:
            self._get_client()
            return True, f"Space {SPACE_ID} reachable"
        except Exception as exc:
            return False, f"Space {SPACE_ID} unreachable: {exc}"

    def estimate_cost(self, *, steps: int, duration_seconds: float, reference_image: Path) -> float:
        """Returns predicted ZeroGPU seconds requested (not dollars - this backend is $0)."""
        return _predict_gpu_seconds(reference_image, steps, duration_seconds)

    def generate_single_character_scene(
        self,
        reference_image: Path,
        motion_prompt: str,
        *,
        steps: int = 4,
        duration_seconds: float = 3.0,
        seed: int = 42,
        negative_prompt: str = "blurry, distorted, extra limbs, photorealistic, 3d render, static, no motion, realistic",
    ) -> SceneResult:
        if COST_MODE != "FREE_ONLY":
            raise NoFreeBackendAvailable(f"COST_MODE={COST_MODE!r} not supported by FreeTestBackend")

        predicted = self.estimate_cost(steps=steps, duration_seconds=duration_seconds, reference_image=reference_image)
        print(f"FreeTestBackend preflight: predicted ZeroGPU request = {predicted:.1f}s "
              f"(steps={steps}, duration={duration_seconds}s)")

        from gradio_client import handle_file

        client = self._get_client()
        try:
            video_path, used_seed = client.predict(
                input_image=handle_file(str(reference_image)),
                prompt=motion_prompt,
                steps=steps,
                negative_prompt=negative_prompt,
                duration_seconds=duration_seconds,
                guidance_scale=1,
                guidance_scale_2=1,
                seed=seed,
                randomize_seed=False,
                api_name="/generate_video",
            )
        except Exception as exc:
            message = str(exc)
            if "ZeroGPU quota" in message or "exceeded your" in message.lower():
                match = _RESET_TIME_RE.search(message)
                reset_hint = match.group(1) if match else "unknown - see message"
                raise QuotaExceeded(
                    f"Not enough free ZeroGPU quota for this test. Try after the quota reset "
                    f"(reset in: {reset_hint}). Raw provider message: {message}",
                    reset_hint=reset_hint,
                ) from exc
            raise

        width, height = _resized_dims(reference_image)
        frames = _num_frames(duration_seconds)
        return SceneResult(
            video_path=Path(video_path),
            width=width,
            height=height,
            num_frames=frames,
            duration_seconds=duration_seconds,
            seed=int(used_seed),
            backend_name="FreeTestBackend",
            cost_usd=0.0,
        )
