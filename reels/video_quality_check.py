"""Technical validation for the assembled Reel MP4 before it's uploaded.

Checks against Facebook's documented Reel requirements (9:16, >=540x960,
>=23fps, 4-60s) plus basic sanity checks (has audio, isn't all black).
Nothing here checks content quality - only "would Facebook accept this /
is this file actually a valid video," same spirit as image_quality_check.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from common import run_subprocess

MIN_DURATION_S = 4.0
MAX_DURATION_S = 60.0
MIN_WIDTH = 540
MIN_HEIGHT = 960
MIN_FPS = 23.0
TARGET_ASPECT = 9 / 16
ASPECT_TOLERANCE = 0.03
BLACK_FRAME_STDDEV_MAX = 5.0


def probe_media_duration(path: Path) -> float:
    """Works for audio or video files - just reads format.duration."""
    info = _ffprobe_json(path)
    try:
        return float(info.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ffprobe_json(path: Path) -> dict:
    result = run_subprocess(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def check_video(path: Path) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty"

    try:
        info = _ffprobe_json(path)
    except Exception as exc:
        return False, str(exc)

    fmt = info.get("format", {})
    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        return False, "no video stream found"
    if not audio_streams:
        return False, "no audio stream found"

    v = video_streams[0]
    try:
        duration = float(fmt.get("duration") or v.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
        return False, f"duration {duration:.1f}s outside Facebook's 4-60s Reel range"

    width = int(v.get("width") or 0)
    height = int(v.get("height") or 0)
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return False, f"resolution {width}x{height} below Facebook's minimum {MIN_WIDTH}x{MIN_HEIGHT}"

    aspect = width / height if height else 0
    if abs(aspect - TARGET_ASPECT) > ASPECT_TOLERANCE:
        return False, f"aspect ratio {aspect:.3f} is not 9:16"

    fps_raw = v.get("avg_frame_rate", "0/1")
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps = 0.0
    if fps < MIN_FPS:
        return False, f"frame rate {fps:.1f} below Facebook's minimum {MIN_FPS}"

    ok, reason = _check_not_black(path, duration)
    if not ok:
        return False, reason

    return True, ""


def _check_not_black(path: Path, duration: float) -> tuple[bool, str]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return False, "OpenCV could not open the assembled video"

    try:
        sample_points = [0.1, 0.5, 0.9]
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or (duration * fps)
        for point in sample_points:
            frame_idx = max(0, min(int(total_frames * point), int(total_frames) - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if float(np.std(gray)) < BLACK_FRAME_STDDEV_MAX:
                return False, f"black/blank frame detected at {point:.0%} of video"
        return True, ""
    finally:
        cap.release()
