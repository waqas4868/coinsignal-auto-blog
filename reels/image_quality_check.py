"""Technical (not content/relevance) validation for generated scene images.

Content/relevance checking (does the image actually match the article) is
explicitly out of scope for V1 - the user's own spec lists that as an
"optional future" item pending a free vision-model provider. This module
only catches objective technical failures: corrupt files, wrong aspect
ratio, near-blank frames, and heavily blurred output.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

TARGET_ASPECT = 1080 / 1920
ASPECT_TOLERANCE = 0.03
BLUR_VARIANCE_MIN = 40.0  # Laplacian variance below this = likely blurred/blank
NEAR_BLANK_STDDEV_MAX = 8.0  # pixel stddev below this = near-solid-color frame


def check_image(path: Path) -> tuple[bool, str]:
    """Returns (ok, reason). reason is empty on success."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty"

    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            width, height = img.size
    except Exception as exc:
        return False, f"Pillow could not open/verify image: {exc}"

    if width <= 0 or height <= 0:
        return False, f"invalid dimensions {width}x{height}"

    aspect = width / height
    if abs(aspect - TARGET_ASPECT) > ASPECT_TOLERANCE:
        return False, f"aspect ratio {aspect:.3f} outside tolerance of {TARGET_ASPECT:.3f}"

    frame = cv2.imread(str(path))
    if frame is None:
        return False, "OpenCV could not decode image"

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    stddev = float(np.std(gray))
    if stddev < NEAR_BLANK_STDDEV_MAX:
        return False, f"near-blank/solid-color image (stddev={stddev:.2f})"

    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if laplacian_var < BLUR_VARIANCE_MIN:
        return False, f"image likely blurred (laplacian variance={laplacian_var:.2f})"

    return True, ""
