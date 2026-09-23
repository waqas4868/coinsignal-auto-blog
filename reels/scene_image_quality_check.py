"""Phase 5: technical (not content/relevance) validation for generated
scene images, tuned for this pipeline's minimalist black-line-on-white
stickman art - NOT a copy of the existing image_quality_check.py, whose
blur/near-blank heuristics assume busy photographic content and would
false-positive-reject a legitimate mostly-white line drawing (a clean
stickman frame is *supposed* to be >95% flat white background).

Also validates against 16:9, not that module's hardcoded 9:16 - section 16
is explicit that scene stills stay 16:9 and must not be edited to 9:16.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

TARGET_ASPECT = 16 / 9
ASPECT_TOLERANCE = 0.03

# A clean stickman line drawing on white is mostly background by design -
# these bounds catch "nothing was drawn" and "the whole frame is filled/
# corrupt" without penalizing legitimate large white margins.
MIN_CONTENT_FRACTION = 0.002
MAX_CONTENT_FRACTION = 0.6
BACKGROUND_DISTANCE_THRESHOLD = 40  # per-pixel summed RGB distance from white counted as "drawn"


def check_image(path: Path) -> tuple[bool, str]:
    """Returns (ok, reason). reason is empty on success."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty"

    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            width, height = rgb.size
    except Exception as exc:
        return False, f"Pillow could not open/verify image: {exc}"

    if width <= 0 or height <= 0:
        return False, f"invalid dimensions {width}x{height}"

    aspect = width / height
    if abs(aspect - TARGET_ASPECT) > ASPECT_TOLERANCE:
        return False, f"aspect ratio {aspect:.3f} outside 16:9 tolerance ({TARGET_ASPECT:.3f})"

    arr = np.asarray(rgb, dtype=np.int16)
    distance_from_white = np.abs(arr - 255).sum(axis=2)
    drawn_mask = distance_from_white > BACKGROUND_DISTANCE_THRESHOLD
    content_fraction = float(drawn_mask.mean())

    if content_fraction < MIN_CONTENT_FRACTION:
        return False, f"near-blank image: only {content_fraction:.4%} of pixels differ from a white background"
    if content_fraction > MAX_CONTENT_FRACTION:
        return False, f"suspiciously dense/corrupt image: {content_fraction:.1%} of pixels differ from a white background"

    return True, ""
