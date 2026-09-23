"""Phase 7 (Master Handoff 2026-09-23): voiceover generation for stickman
Reel scenes (spec section 19).

The spec names ElevenLabs as primary and Kokoro-82M as fallback. ElevenLabs
is implemented for real (request schema verified this phase against
ElevenLabs' own official API docs - see the Phase 7 report for the source)
but, like Veo in Phase 6, it is a metered/paid API beyond its limited free
tier, and this project has a standing hard zero-cost requirement
(COST_MODE=FREE_ONLY) set explicitly earlier for this exact
animation-generation work. So ElevenLabsProvider requires BOTH a real API
key/voice id AND an explicit ALLOW_PAID_VOICE_PROVIDERS=true to ever fire,
and is never included in default_providers().

The free default here is PIPER, not literally Kokoro-82M - a judgment
call, not a silent substitution: Piper (en_US-ljspeech-medium, MIT
licensed, trained on the public-domain LJSpeech dataset) is already a
proven, pinned dependency of this exact repo (requirements.txt,
reels/voice_generator.py, cached in coinsignal_reels.yml) serving the
identical need, and its commercial-use licensing was already vetted
earlier in this project - see the Phase 7 report for the full reasoning
and how to swap in literal Kokoro-82M instead if you'd rather match the
spec's naming exactly. Never logs or echoes API keys.

State machine (section 19): VOICE_PENDING -> VOICE_GENERATING ->
VOICE_READY | VOICE_FAILED, persisted per scene. An already-VOICE_READY
scene is never regenerated, same rule as Phases 5-6.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from common import iso_now, read_json, write_json
from scene_voice_quality_check import check_voice_audio

MAX_SCENE_VOICE_DURATION_SECONDS = 30.0


@dataclass
class VoiceOutcome:
    success: bool
    generation_id: str = ""
    error: str = ""


class VoiceProvider(Protocol):
    name: str

    def generate(self, text: str, output_path: Path) -> VoiceOutcome: ...


class ElevenLabsProvider:
    """https://elevenlabs.io/docs/api-reference/text-to-speech/convert -
    POST /v1/text-to-speech/{voice_id}, header xi-api-key, JSON body
    {text, model_id}, raw audio (mp3) response body. Verified against
    ElevenLabs' own official docs, not invented."""

    name = "elevenlabs"

    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_flash_v2_5"):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id

    @staticmethod
    def is_enabled() -> bool:
        return os.environ.get("ALLOW_PAID_VOICE_PROVIDERS", "").strip().lower() == "true"

    def generate(self, text: str, output_path: Path) -> VoiceOutcome:
        if not self.is_enabled():
            return VoiceOutcome(
                False,
                error="PROVIDER_REQUIRES_PAID_ACCESS: ElevenLabs is metered/paid beyond its limited free tier; "
                "set ALLOW_PAID_VOICE_PROVIDERS=true to enable it explicitly",
            )
        if not self.api_key or not self.voice_id:
            return VoiceOutcome(False, error="PROVIDER_UNAVAILABLE: missing ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID")

        try:
            response = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": self.model_id},
                timeout=60,
            )
        except requests.RequestException as exc:
            return VoiceOutcome(False, error=f"network error: {exc}")

        if not response.ok:
            # ElevenLabs error bodies do not echo the API key back - safe to include, per "do not log credentials".
            return VoiceOutcome(False, error=f"HTTP {response.status_code}: {response.text[:400]}")
        if "audio" not in response.headers.get("Content-Type", ""):
            return VoiceOutcome(False, error=f"non-audio content-type: {response.headers.get('Content-Type')!r}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return VoiceOutcome(True, generation_id=f"elevenlabs-{self.voice_id}")


class PiperProvider:
    """Free, fully offline TTS via the `piper` CLI (already a pinned repo
    dependency). Text is piped via stdin - verified against piper's own
    __main__.py source this phase (no -i/--input-file given -> reads
    sys.stdin, one line per synthesized+concatenated chunk into the single
    -f output file), not guessed from a stale memory of the CLI."""

    name = "piper"

    def __init__(self, model_path: Path, config_path: Path | None = None):
        self.model_path = model_path
        self.config_path = config_path

    def generate(self, text: str, output_path: Path) -> VoiceOutcome:
        if not self.model_path.exists():
            return VoiceOutcome(False, error=f"PROVIDER_UNAVAILABLE: Piper voice model not found at {self.model_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["python", "-m", "piper", "-m", str(self.model_path), "-f", str(output_path)]
        if self.config_path and self.config_path.exists():
            cmd += ["-c", str(self.config_path)]

        try:
            result = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return VoiceOutcome(False, error="piper synthesis timed out")
        if result.returncode != 0:
            return VoiceOutcome(False, error=f"piper exited {result.returncode}: {result.stderr[:600]}")
        return VoiceOutcome(True, generation_id=f"piper-{self.model_path.stem}")


def default_providers(model_path: Path, config_path: Path | None = None) -> list[VoiceProvider]:
    """The free path only - ElevenLabsProvider is deliberately NOT included
    here (see module docstring); callers who have explicitly opted into
    paid access construct and pass it themselves."""
    return [PiperProvider(model_path, config_path)]


def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_scene_voice(
    *,
    text: str,
    output_path: Path,
    providers: list[VoiceProvider],
    state_path: Path,
) -> dict:
    """Runs the section-19 voice generation sequence for one scene's
    voiceover line: try each provider in order, validate the result
    technically, persist a state record either way. Idempotent: an
    already-VOICE_READY record whose file still exists is returned
    unchanged, no provider called."""
    existing = read_json(state_path, None)
    if isinstance(existing, dict) and existing.get("status") == "VOICE_READY" and Path(existing.get("path", "")).exists():
        return existing

    record: dict = {"text": text, "path": str(output_path), "status": "VOICE_GENERATING", "created_at": iso_now()}
    write_json(state_path, record)

    last_error = "no providers configured"
    for provider in providers:
        outcome = provider.generate(text, output_path)
        if not outcome.success:
            last_error = f"{provider.name}: {outcome.error}"
            continue

        ok, reason = check_voice_audio(output_path, max_duration=MAX_SCENE_VOICE_DURATION_SECONDS)
        if not ok:
            last_error = f"{provider.name}: generated audio failed validation: {reason}"
            if output_path.exists():
                output_path.unlink()
            continue

        record = {
            "text": text,
            "provider": provider.name,
            "generation_id": outcome.generation_id,
            "path": str(output_path),
            "checksum": _sha256_of_file(output_path),
            "status": "VOICE_READY",
            "created_at": record["created_at"],
            "ready_at": iso_now(),
            "error": "",
        }
        write_json(state_path, record)
        return record

    record = {**record, "status": "VOICE_FAILED", "error": last_error, "failed_at": iso_now()}
    write_json(state_path, record)
    return record
