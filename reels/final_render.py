"""Phase 8 (Master Handoff 2026-09-23): timing/captions/FFmpeg final render
(spec sections 20-22).

Per-scene composition, real ffmpeg (verified this phase by hand-building
and inspecting an actual rendered frame - see the Phase 8 report):
  1. scale the scene's 16:9 video to fit 1080px width, letterbox onto a
     1080x1920 white canvas (matches the stickman's own white-background
     style from Phase 4 - no crop, no zoom/pan as the composition
     mechanism, consistent with section 18's "no unnecessary camera
     movement").
  2. "Voiceover drives scene timing" (section 20): the scene's OUTPUT
     duration always equals its voice clip's real duration, never the
     reverse. If the video is shorter, its last frame is held (ffmpeg's
     tpad stop_mode=clone) to fill the gap; if longer, it's trimmed. The
     voice itself is never sped up, slowed down, or cut.
  3. captions (section 21) are burned in via drawtext using a textfile (not
     inline text=) so no fragile manual escaping of colons/quotes/percent
     signs is needed, positioned in the lower third with a semi-opaque box
     for readability, sized to leave headroom above the platform-UI zone
     most short-form apps overlay near the very bottom edge.
  4. per-scene clips share one consistent format (H.264/yuv420p/AAC/same
     resolution) specifically so concatenation in render_final_reel() can
     use the fast, lossless concat demuxer.

Final validation (section 22): 1080x1920, H.264 video, AAC audio, audio
stream present, file readable - duration is REPORTED, not force-matched to
138s, since voiceover-driven timing means the natural total will vary
(section 22 itself only asks for "a small technical muxing tolerance" and
to "report actual measured duration", not to fabricate an exact number).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from common import iso_now, read_json, write_json

FINAL_WIDTH = 1080
FINAL_HEIGHT = 1920
TARGET_TOTAL_DURATION_SECONDS = 138.0
MAX_CAPTION_CHARS_PER_LINE = 28
CAPTION_BOTTOM_MARGIN_PX = 400  # keeps captions clear of the bottom UI-overlay zone most short-form apps use


class RenderError(RuntimeError):
    """A rendering step failed. Never silently produces a partial/invented
    output - see render_final_reel()'s state persistence on failure."""


# ---------------------------------------------------------------------------
# Timing (section 20)
# ---------------------------------------------------------------------------

def compute_scene_timing(video_duration: float, voice_duration: float) -> dict:
    """Voiceover always drives the scene's final duration. Returns how much
    the video needs to be frozen-extended (tpad) or trimmed to match it."""
    return {
        "output_duration": voice_duration,
        "video_pad_seconds": max(0.0, voice_duration - video_duration),
        "needs_trim": video_duration > voice_duration,
    }


# ---------------------------------------------------------------------------
# Captions (section 21)
# ---------------------------------------------------------------------------

def wrap_caption(text: str, max_chars: int = MAX_CAPTION_CHARS_PER_LINE) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


_FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
]


def resolve_caption_font() -> Path:
    override = os.environ.get("REEL_CAPTION_FONT", "").strip()
    if override and Path(override).exists():
        return Path(override)
    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise RenderError(
        "No usable caption font found (checked DejaVu/Liberation on Linux and Arial on Windows); "
        "set REEL_CAPTION_FONT to a .ttf path"
    )


def _ffmpeg_filter_option_escape(path: Path) -> str:
    """ffmpeg filtergraph OPTION VALUES (fontfile=, textfile=) split on ':'
    and treat '\\' as an escape char - Windows paths need both escaped
    here. Do NOT use this for the concat demuxer's file list - that's a
    different syntax (see _concat_list_path_escape) and escaping ':' there
    corrupts the path instead (found by an actual failing ffmpeg run this
    phase: "C\\:/Users/..." could not be opened)."""
    return str(path).replace("\\", "/").replace(":", "\\:")


def _concat_list_path_escape(path: Path) -> str:
    """concat demuxer 'file' directive syntax: single-quoted path, only an
    embedded single quote needs escaping (as '\\''). Colons and backslashes
    are NOT special here."""
    return str(path).replace("\\", "/").replace("'", "'\\''")


# ---------------------------------------------------------------------------
# Per-scene composition
# ---------------------------------------------------------------------------

def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RenderError(f"ffmpeg failed: {result.stderr.strip()[:2000]}")


def probe_duration(path: Path) -> float:
    """Real ffprobe-based duration lookup - render_scene_clip() always
    probes the actual files rather than trusting a caller-supplied number,
    which could go stale relative to what's really on disk."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RenderError(f"could not probe duration of {path}: {result.stderr.strip()[:400]}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RenderError(f"unparseable duration for {path}: {result.stdout.strip()!r}") from exc


def render_scene_clip(
    *,
    video_path: Path,
    voice_path: Path,
    caption_text: str,
    output_path: Path,
    caption_file_path: Path,
) -> dict:
    """Composes one scene into a 1080x1920 clip whose duration matches its
    voice track exactly, with burned-in captions. Returns the timing plan
    used (useful for tests/debugging)."""
    video_duration = probe_duration(video_path)
    voice_duration = probe_duration(voice_path)
    timing = compute_scene_timing(video_duration, voice_duration)

    caption_file_path.parent.mkdir(parents=True, exist_ok=True)
    caption_file_path.write_text(wrap_caption(caption_text), encoding="utf-8")

    font = resolve_caption_font()
    filter_complex = (
        f"[0:v]scale={FINAL_WIDTH}:-2,"
        f"pad={FINAL_WIDTH}:{FINAL_HEIGHT}:({FINAL_WIDTH}-iw)/2:({FINAL_HEIGHT}-ih)/2:white,"
        f"tpad=stop_mode=clone:stop_duration={timing['video_pad_seconds']:.3f},"
        f"drawtext=fontfile='{_ffmpeg_filter_option_escape(font)}':textfile='{_ffmpeg_filter_option_escape(caption_file_path)}':"
        f"fontcolor=black:fontsize=48:line_spacing=8:box=1:boxcolor=white@0.75:boxborderw=16:"
        f"x=(w-text_w)/2:y=h-{CAPTION_BOTTOM_MARGIN_PX}[v]"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(video_path),
            "-i", str(voice_path),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "1:a",
            "-t", f"{timing['output_duration']:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-ar", "44100", "-shortest",
            str(output_path),
        ]
    )
    return timing


# ---------------------------------------------------------------------------
# Concatenation + final validation
# ---------------------------------------------------------------------------

def concat_scene_clips(clip_paths: list[Path], output_path: Path, concat_list_path: Path) -> None:
    """Uses ffmpeg's concat demuxer. Safe as a lossless `-c copy` concat
    because render_scene_clip() always outputs an identical format
    (H.264/yuv420p/30fps/AAC/44100Hz) for every scene."""
    concat_list_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"file '{_concat_list_path_escape(p.resolve())}'" for p in clip_paths]
    concat_list_path.write_text("\n".join(lines), encoding="utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_list_path), "-c", "copy", str(output_path)])


def check_final_render(path: Path) -> tuple[bool, str, dict]:
    """Returns (ok, reason, probe_summary). Section 22: validate resolution/
    codecs/audio presence/readability; REPORT actual duration rather than
    hard-failing on it (see module docstring)."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty", {}

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        return False, f"ffprobe could not read the file: {result.stderr.strip()[:400]}", {}
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "ffprobe returned invalid JSON", {}

    video_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not video_streams:
        return False, "no video stream found", {}
    if not audio_streams:
        return False, "no audio stream found", {}

    video = video_streams[0]
    audio = audio_streams[0]
    duration = float(probe.get("format", {}).get("duration", 0.0))

    summary = {
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "duration_seconds": duration,
        "target_duration_seconds": TARGET_TOTAL_DURATION_SECONDS,
    }

    if video.get("width") != FINAL_WIDTH or video.get("height") != FINAL_HEIGHT:
        return False, f"resolution {video.get('width')}x{video.get('height')} is not {FINAL_WIDTH}x{FINAL_HEIGHT}", summary
    if video.get("codec_name") != "h264":
        return False, f"video codec {video.get('codec_name')!r} is not h264", summary
    if audio.get("codec_name") != "aac":
        return False, f"audio codec {audio.get('codec_name')!r} is not aac", summary
    if duration <= 0:
        return False, "invalid (non-positive) duration", summary

    return True, "", summary


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def render_final_reel(
    *,
    scenes: list[dict],
    output_path: Path,
    work_dir: Path,
    state_path: Path,
) -> dict:
    """scenes: ordered list of {video_path, voice_path, caption_text}.
    Idempotent: if output_path already exists and passes
    check_final_render(), returns the existing summary without
    re-rendering anything."""
    if output_path.exists():
        ok, _, summary = check_final_render(output_path)
        if ok:
            return {"status": "RENDER_READY", "path": str(output_path), **summary}

    record = {"status": "RENDER_GENERATING", "path": str(output_path), "scene_count": len(scenes), "created_at": iso_now()}
    write_json(state_path, record)

    clip_paths: list[Path] = []
    try:
        for i, scene in enumerate(scenes):
            clip_path = work_dir / f"scene_{i + 1:02d}.mp4"
            caption_path = work_dir / f"scene_{i + 1:02d}_caption.txt"
            render_scene_clip(
                video_path=Path(scene["video_path"]),
                voice_path=Path(scene["voice_path"]),
                caption_text=scene["caption_text"],
                output_path=clip_path,
                caption_file_path=caption_path,
            )
            clip_paths.append(clip_path)

        concat_scene_clips(clip_paths, output_path, work_dir / "concat_list.txt")
    except RenderError as exc:
        record = {**record, "status": "RENDER_FAILED", "error": str(exc)[:2000], "failed_at": iso_now()}
        write_json(state_path, record)
        raise

    ok, reason, summary = check_final_render(output_path)
    if not ok:
        record = {**record, "status": "RENDER_FAILED", "error": reason, "failed_at": iso_now()}
        write_json(state_path, record)
        raise RenderError(f"final render failed validation: {reason}")

    record = {**record, "status": "RENDER_READY", "ready_at": iso_now(), **summary}
    write_json(state_path, record)
    return record
