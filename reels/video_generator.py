"""ffmpeg-based Reel assembly: validated scene images + voice.wav -> final MP4.

Not AI video generation - simulated camera movement (zoompan) over static
images, burned-in captions, then muxed with narration audio. This is the
"Option A" free-tools path from the user's spec (section 3), deliberately
chosen over paid image-to-video APIs.

Built as several small, independently-debuggable ffmpeg subprocess calls
(per-scene clip -> concat -> audio mux) rather than one giant filter_complex
expression across every input at once - easier to reason about and matches
this repo's existing style of many small, clear subprocess calls.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

from common import run_subprocess

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
FPS = 30
ZOOM_PER_SECOND = 0.04  # slow, subtle zoom - not dizzying on a phone screen


def _resolve_font_path() -> str | None:
    override = os.environ.get("REEL_FONT_PATH", "").strip()
    if override and Path(override).exists():
        return override
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # ubuntu-latest runner
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",  # local Windows dev testing
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _escape_drawtext(text: str) -> str:
    # ffmpeg filter-argument escaping: backslash, colon, single-quote, percent.
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")  # sidestep quote-escaping entirely
    text = text.replace("%", "\\%")
    return text


def _scene_clip(image_path: Path, caption: str, duration: float, output_path: Path) -> None:
    frames = max(1, int(round(duration * FPS)))
    max_zoom = 1.0 + ZOOM_PER_SECOND * duration
    font = _resolve_font_path()
    caption_escaped = _escape_drawtext(caption)

    vf_parts = [
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}",
        f"zoompan=z='min(zoom+{ZOOM_PER_SECOND / FPS:.6f},{max_zoom:.3f})':d={frames}:s={TARGET_WIDTH}x{TARGET_HEIGHT}:fps={FPS}",
    ]
    if caption:
        drawtext = (
            f"drawtext=text='{caption_escaped}':fontcolor=white:fontsize=54:"
            "box=1:boxcolor=black@0.55:boxborderw=20:"
            "x=(w-text_w)/2:y=h-th-140:line_spacing=8"
        )
        if font:
            # A raw ':' (e.g. Windows drive letters like C:/...) breaks ffmpeg's
            # filter-option parser even inside single quotes - escape it like any
            # other special char rather than relying on quoting alone.
            font_escaped = _escape_drawtext(Path(font).as_posix())
            drawtext += f":fontfile={font_escaped}"
        vf_parts.append(drawtext)
    vf_parts.append("format=yuv420p")

    args = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", f"{duration}", "-i", str(image_path),
        "-vf", ",".join(vf_parts),
        "-r", str(FPS),
        "-t", f"{duration}",
        "-an",
        str(output_path),
    ]
    result = run_subprocess(args, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg scene render failed for {image_path.name}: {result.stderr[-1500:]}")


def _concat_clips(clip_paths: list[Path], output_path: Path, work_dir: Path) -> None:
    list_file = work_dir / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in clip_paths), encoding="utf-8"
    )
    args = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output_path)]
    result = run_subprocess(args, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-1500:]}")


def _mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    # Video length is authoritative (scenes are pre-scaled to the real narration
    # length by reel_worker.py before this runs). -af apad pads short audio with
    # silence instead of -shortest truncating the *video* if audio is a hair
    # shorter than expected; -shortest here just guards against audio overrun.
    args = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-af", "apad",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-shortest",
        str(output_path),
    ]
    result = run_subprocess(args, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio mux failed: {result.stderr[-1500:]}")


def build_reel(scenes: list[dict], voice_path: Path, output_path: Path, work_dir: Path) -> None:
    """scenes: [{image_path: Path, caption: str, duration: float}, ...]"""
    work_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []
    for i, scene in enumerate(scenes):
        clip_path = work_dir / f"scene_{i:02d}.mp4"
        _scene_clip(scene["image_path"], scene.get("caption", ""), float(scene["duration"]), clip_path)
        clip_paths.append(clip_path)

    concatenated_path = work_dir / "concatenated.mp4"
    _concat_clips(clip_paths, concatenated_path, work_dir)
    _mux_audio(concatenated_path, voice_path, output_path)
