"""Local text-to-speech narration via Piper (no network dependency, no API cost).

Voice choice matters: en_US-lessac-* (the "obvious" default) is trained on
the Blizzard 2013 corpus, which restricts commercial use. en_US-ljspeech-*
is MIT-licensed and trained on the public-domain LJSpeech dataset - safe for
a commercial Page's content. Verified directly against the model's
HuggingFace MODEL_CARD before picking it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from common import REPO_ROOT, run_subprocess

VOICE = "en_US-ljspeech-medium"
VOICE_DATA_DIR = REPO_ROOT / "reels" / ".piper_voices"


def ensure_voice_downloaded() -> None:
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    result = run_subprocess(
        [sys.executable, "-m", "piper.download_voices", "--data-dir", str(VOICE_DATA_DIR), VOICE], timeout=120
    )
    if result.returncode != 0:
        # download_voices exits non-zero if the voice is already present in some
        # versions; only fail hard if synthesis later actually can't find it.
        print("piper.download_voices warning (continuing):", result.stderr[-500:])


def synthesize(script: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable, "-m", "piper",
        "-m", VOICE,
        "--data-dir", str(VOICE_DATA_DIR),
        "-f", str(output_path),
        "--", script,
    ]
    result = run_subprocess(args, timeout=120)
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Piper synthesis failed: {result.stderr[-1500:]}")
