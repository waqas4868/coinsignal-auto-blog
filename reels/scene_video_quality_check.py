"""Phase 6: technical validation for generated scene video clips (ffprobe-
based, real subprocess calls - ffmpeg/ffprobe are already a hard dependency
of this repo's CI, per .github/workflows/coinsignal_reels.yml's "Ensure
ffmpeg is installed" step).

Scene clips stay 16:9 here (spec section 16: image stays 16:9, only the
FINAL render - a later phase - composes to 9:16), and each clip should run
close to its scene's target duration (~6s per the prompt's own "~6 sec
each" scene-timing assumption).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

TARGET_ASPECT = 16 / 9
ASPECT_TOLERANCE = 0.03
DEFAULT_DURATION_TOLERANCE_SECONDS = 1.5


def check_video(path: Path, target_duration_seconds: float, *, duration_tolerance: float = DEFAULT_DURATION_TOLERANCE_SECONDS) -> tuple[bool, str]:
    """Returns (ok, reason). reason is empty on success."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty"

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return False, "ffprobe is not available on this system"

    if result.returncode != 0:
        return False, f"ffprobe could not read the file: {result.stderr.strip()[:400]}"

    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "ffprobe returned invalid JSON"

    video_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        return False, "no video stream found"
    stream = video_streams[0]

    width, height = stream.get("width"), stream.get("height")
    if not width or not height:
        return False, "video stream is missing width/height"
    aspect = width / height
    if abs(aspect - TARGET_ASPECT) > ASPECT_TOLERANCE:
        return False, f"aspect ratio {aspect:.3f} outside 16:9 tolerance ({TARGET_ASPECT:.3f})"

    duration_str = probe.get("format", {}).get("duration") or stream.get("duration")
    if not duration_str:
        return False, "could not determine video duration"
    try:
        duration = float(duration_str)
    except ValueError:
        return False, f"unparseable duration value: {duration_str!r}"

    if abs(duration - target_duration_seconds) > duration_tolerance:
        return False, (
            f"duration {duration:.2f}s is outside tolerance of target {target_duration_seconds:.2f}s "
            f"(+/-{duration_tolerance}s)"
        )

    return True, ""
