"""Phase 5 (Master Handoff 2026-09-23): image generation abstraction for
stickman Reel scenes (spec section 16).

Provider-agnostic by design: an ImageProvider is just an object with a
`.name` and a `.generate(prompt, output_path) -> ProviderOutcome` method,
so generate_scene_image() can try a primary provider and fall back to a
secondary one without either knowing the other exists. Two free-tier
providers are included, same precedent already used elsewhere in this
project (Pollinations primary, Hugging Face FLUX fallback) - both keep
COST_MODE=FREE_ONLY.

Deliberately self-contained: does NOT import image_generator_a.py /
image_generator_b.py / image_quality_check.py. Those already serve the
existing, still-scheduled 8-30s Reels pipeline at a different aspect ratio
(9:16) and a different visual style (photorealistic crypto news) - reusing
them here would couple two pipelines that should stay independent, the
same reasoning common.py already documents one level up (Reel pipeline vs
article pipeline).

State machine (section 16): IMAGE_PENDING -> IMAGE_GENERATING -> IMAGE_READY
| IMAGE_FAILED, persisted per scene with prompt/provider/generation_id/
path/checksum/dimensions/timestamp. A scene already IMAGE_READY on disk is
never regenerated just because a later pipeline stage failed.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import requests
from PIL import Image

from common import iso_now, read_json, write_json
from scene_image_quality_check import check_image

SCENE_WIDTH = 1280
SCENE_HEIGHT = 720  # 16:9, per section 16 - do NOT edit to 9:16 here


@dataclass
class ProviderOutcome:
    success: bool
    generation_id: str = ""
    error: str = ""


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, output_path: Path) -> ProviderOutcome: ...


POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
POLLINATIONS_MIN_INTERVAL_SECONDS = 16  # documented anonymous-tier limit: ~1 req/15s
_pollinations_last_call_at = 0.0


class PollinationsProvider:
    """Free, no API key required. In practice (verified this phase against
    the live endpoint) it does not reliably produce clean minimalist
    line-art on request - see the Phase 5 report for a real saved example -
    which is exactly why this abstraction always has a fallback and a real
    content-based validator, not just a "did the HTTP call succeed" check."""

    name = "pollinations"

    def generate(self, prompt: str, output_path: Path) -> ProviderOutcome:
        global _pollinations_last_call_at
        elapsed = time.monotonic() - _pollinations_last_call_at
        if elapsed < POLLINATIONS_MIN_INTERVAL_SECONDS:
            time.sleep(POLLINATIONS_MIN_INTERVAL_SECONDS - elapsed)

        token = os.environ.get("POLLINATIONS_TOKEN", "").strip()
        seed = str(int(time.time() * 1000) % 1_000_000)
        params = {
            "width": str(SCENE_WIDTH),
            "height": str(SCENE_HEIGHT),
            "model": "flux",
            "nologo": "true" if token else "false",
            "seed": seed,
        }
        if token:
            params["token"] = token
        url = f"{POLLINATIONS_BASE}/{quote(prompt, safe='')}"
        try:
            response = requests.get(url, params=params, timeout=90)
        except requests.RequestException as exc:
            return ProviderOutcome(False, error=f"network error: {exc}")
        finally:
            _pollinations_last_call_at = time.monotonic()

        if not response.ok:
            return ProviderOutcome(False, error=f"HTTP {response.status_code}: {response.text[:300]}")
        if "image" not in response.headers.get("Content-Type", ""):
            return ProviderOutcome(False, error=f"non-image content-type: {response.headers.get('Content-Type')!r}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return ProviderOutcome(True, generation_id=f"pollinations-seed-{seed}")


class HuggingFaceFluxProvider:
    """Free-tier fallback, only reached when the primary provider fails or
    its output fails technical validation. Requires HF_TOKEN (already
    configured for the article pipeline's hero images)."""

    name = "huggingface_flux"

    def __init__(self, hf_token: str):
        self.hf_token = hf_token

    def generate(self, prompt: str, output_path: Path) -> ProviderOutcome:
        if not self.hf_token:
            return ProviderOutcome(False, error="HF_TOKEN not configured")
        try:
            from huggingface_hub import InferenceClient

            client = InferenceClient(provider="auto", api_key=self.hf_token, timeout=120)
            image = client.text_to_image(
                prompt=prompt, model="black-forest-labs/FLUX.1-schnell", width=SCENE_WIDTH, height=SCENE_HEIGHT
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path)
            return ProviderOutcome(True, generation_id=f"hf-flux-{int(time.time())}")
        except Exception as exc:
            return ProviderOutcome(False, error=str(exc)[:1200])


def default_providers(hf_token: str = "") -> list[ImageProvider]:
    return [PollinationsProvider(), HuggingFaceFluxProvider(hf_token)]


def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_scene_image(
    *,
    prompt: str,
    output_path: Path,
    providers: list[ImageProvider],
    state_path: Path,
) -> dict:
    """Runs the section-16 generation sequence for one scene image: try
    each provider in order, validate the result technically, and persist a
    state record either way. Idempotent: if state_path already records
    IMAGE_READY and the file still exists, returns that record unchanged
    without calling any provider - "never regenerate a successful image
    because a later stage failed."
    """
    existing = read_json(state_path, None)
    if isinstance(existing, dict) and existing.get("status") == "IMAGE_READY" and Path(existing.get("path", "")).exists():
        return existing

    record: dict = {"prompt": prompt, "status": "IMAGE_GENERATING", "created_at": iso_now()}
    write_json(state_path, record)

    last_error = "no providers configured"
    for provider in providers:
        outcome = provider.generate(prompt, output_path)
        if not outcome.success:
            last_error = f"{provider.name}: {outcome.error}"
            continue

        ok, reason = check_image(output_path)
        if not ok:
            last_error = f"{provider.name}: generated image failed validation: {reason}"
            if output_path.exists():
                output_path.unlink()
            continue

        with Image.open(output_path) as img:
            width, height = img.size
        record = {
            "prompt": prompt,
            "provider": provider.name,
            "generation_id": outcome.generation_id,
            "path": str(output_path),
            "checksum": _sha256_of_file(output_path),
            "width": width,
            "height": height,
            "status": "IMAGE_READY",
            "created_at": record["created_at"],
            "ready_at": iso_now(),
            "error": "",
        }
        write_json(state_path, record)
        return record

    record = {**record, "status": "IMAGE_FAILED", "error": last_error, "failed_at": iso_now()}
    write_json(state_path, record)
    return record
