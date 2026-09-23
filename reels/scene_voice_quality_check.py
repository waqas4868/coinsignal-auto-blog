"""Phase 7: technical validation for generated per-scene voiceover audio
(ffprobe + ffmpeg volumedetect, real subprocess calls). Format-agnostic on
purpose: the two providers return different containers (ElevenLabs -> mp3,
Piper -> wav), so this validates via ffprobe/ffmpeg rather than the wave
stdlib module, one codepath for both.

No fixed target duration here, unlike scene_image/video checks - per
section 20, voiceover DRIVES scene timing rather than being fit to a
pre-set length, so only sanity bounds apply (not empty, not absurdly
long for a 1-2 line scene script) plus a real silence check (verified
this phase: ffmpeg's volumedetect reports ~-91dB mean_volume for a
genuinely silent clip vs. ~-15dB for real audible content).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

MIN_DURATION_SECONDS = 0.3
MAX_DURATION_SECONDS = 30.0
SILENCE_MEAN_VOLUME_DB = -50.0  # below this, treat the clip as effectively silent/failed synthesis


def _ffprobe_json(path: Path) -> dict | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _mean_volume_db(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return None
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr)
    return float(match.group(1)) if match else None


def check_voice_audio(
    path: Path,
    *,
    min_duration: float = MIN_DURATION_SECONDS,
    max_duration: float = MAX_DURATION_SECONDS,
) -> tuple[bool, str]:
    """Returns (ok, reason). reason is empty on success."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty"

    probe = _ffprobe_json(path)
    if probe is None:
        return False, "ffprobe could not read the file"

    audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        return False, "no audio stream found"

    duration_str = probe.get("format", {}).get("duration") or audio_streams[0].get("duration")
    if not duration_str:
        return False, "could not determine audio duration"
    try:
        duration = float(duration_str)
    except ValueError:
        return False, f"unparseable duration value: {duration_str!r}"

    if duration < min_duration:
        return False, f"duration {duration:.2f}s is below the minimum {min_duration}s - likely a failed/empty synthesis"
    if duration > max_duration:
        return False, f"duration {duration:.2f}s exceeds the maximum {max_duration}s for a single scene's voiceover"

    mean_volume = _mean_volume_db(path)
    if mean_volume is not None and mean_volume < SILENCE_MEAN_VOLUME_DB:
        return False, f"clip is effectively silent (mean_volume={mean_volume:.1f}dB, threshold={SILENCE_MEAN_VOLUME_DB}dB)"

    return True, ""
