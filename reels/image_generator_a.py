"""Provider A: Pollinations (primary Reel scene image generator).

GET https://image.pollinations.ai/prompt/{prompt} - no API key required for
basic use (anonymous tier: ~1 request/15s). Free tier images carry a
watermark unless POLLINATIONS_TOKEN is set (removes it via nologo=true).
MIT licensed. Verified against the live docs at implementation time.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import quote

import requests

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
TIMEOUT = 60
MIN_INTERVAL_SECONDS = 16  # stay just above the documented 1-req/15s anonymous limit

_last_call_at = 0.0


def _respect_rate_limit() -> None:
    global _last_call_at
    elapsed = time.monotonic() - _last_call_at
    if elapsed < MIN_INTERVAL_SECONDS:
        time.sleep(MIN_INTERVAL_SECONDS - elapsed)


def generate_image(prompt: str, output_path: Path, *, width: int = 1080, height: int = 1920) -> bool:
    """Fetch one vertical scene image from Pollinations. Returns True on success."""
    _respect_rate_limit()
    global _last_call_at

    token = os.environ.get("POLLINATIONS_TOKEN", "").strip()
    params = {
        "width": str(width),
        "height": str(height),
        "model": "flux",
        "nologo": "true" if token else "false",
        "seed": str(int(time.time() * 1000) % 1_000_000),
    }
    if token:
        params["token"] = token

    url = f"{POLLINATIONS_BASE}/{quote(prompt, safe='')}"
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
        _last_call_at = time.monotonic()
    except requests.RequestException as exc:
        print("Pollinations request failed:", exc)
        return False

    if not response.ok:
        print(f"Pollinations HTTP {response.status_code}: {response.text[:300]}")
        return False

    content_type = response.headers.get("Content-Type", "")
    if "image" not in content_type:
        print(f"Pollinations returned non-image content-type: {content_type!r}")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return True
