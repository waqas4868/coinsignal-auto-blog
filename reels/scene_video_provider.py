"""Phase 6 (Master Handoff 2026-09-23): image-to-video abstraction for
stickman Reel scenes (spec sections 16-18).

Two providers, matching section 17's explicit direction:

FlowManualProvider - Google Flow (labs.google/flow) has NO public API; it
is a UI-only creative tool. Per section 17 this pipeline does not drive it
with fragile browser-click automation. generate() never claims a finished
video on its own: it writes an entry to a JSON manifest (scene image +
motion prompt + expected output path) for a human to work through in the
Flow UI, and always returns "awaiting_manual_import". A separate function,
complete_manual_import(), is called once a human has placed the resulting
clip at that expected path - it validates the real file and finalizes the
state record. This is a real, working, zero-cost path today, not a stub.

VeoApiProvider - Vertex AI Veo has a documented, BILLED REST API. Its
request/response schema here (predictLongRunning submit,
fetchPredictOperation poll - not a bare GET on the operation, which one
third-party reproduction gets wrong) was verified this phase against
Google Cloud's public documentation and cross-checked against an
independent source rather than invented (see the Phase 6 report for
sources). This project has a standing hard zero-cost requirement
(COST_MODE=FREE_ONLY, set explicitly earlier in this project for this
exact animation-generation work), so VeoApiProvider requires BOTH real
GCP credentials AND an explicit ALLOW_PAID_VIDEO_PROVIDERS=true to ever
attempt a call - it is never included in default_providers() and never
fires silently (section 27/28: PROVIDER_REQUIRES_PAID_ACCESS).

State machine (extends section 16's PENDING/GENERATING/READY/FAILED with
one addition the manual-import workflow structurally requires):
VIDEO_PENDING -> VIDEO_GENERATING -> VIDEO_READY | VIDEO_FAILED
                                   -> VIDEO_AWAITING_MANUAL_IMPORT -> VIDEO_READY | VIDEO_FAILED
A scene already VIDEO_READY is never regenerated, same rule as Phase 5.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from common import iso_now, read_json, write_json
from scene_video_quality_check import check_video

SCENE_TARGET_DURATION_SECONDS = 6.0  # from the prompt's own "~6 sec each" scene-timing assumption


@dataclass
class VideoOutcome:
    status: str  # "ready" | "awaiting_manual_import" | "failed"
    generation_id: str = ""
    error: str = ""


class VideoProvider(Protocol):
    name: str

    def generate(
        self, *, image_path: Path, motion_prompt: str, output_path: Path, target_duration_seconds: float
    ) -> VideoOutcome: ...


class FlowManualProvider:
    name = "flow_manual"

    def __init__(self, manifest_path: Path):
        self.manifest_path = manifest_path

    def generate(
        self, *, image_path: Path, motion_prompt: str, output_path: Path, target_duration_seconds: float
    ) -> VideoOutcome:
        manifest = read_json(self.manifest_path, [])
        if not isinstance(manifest, list):
            manifest = []
        expected_output = str(output_path)
        if not any(entry.get("expected_output_path") == expected_output for entry in manifest):
            manifest.append(
                {
                    "image_path": str(image_path),
                    "motion_prompt": motion_prompt,
                    "expected_output_path": expected_output,
                    "target_duration_seconds": target_duration_seconds,
                    "requested_at": iso_now(),
                }
            )
            write_json(self.manifest_path, manifest)
        return VideoOutcome(status="awaiting_manual_import")


VEO_SUBMIT_URL = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}"
    "/publishers/google/models/{model}:predictLongRunning"
)
VEO_POLL_URL = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}"
    "/publishers/google/models/{model}:fetchPredictOperation"
)


class VeoApiProvider:
    name = "veo_api"

    def __init__(
        self,
        project_id: str,
        access_token: str,
        *,
        location: str = "us-central1",
        model: str = "veo-3.0-fast-generate-001",
    ):
        self.project_id = project_id
        self.access_token = access_token
        self.location = location
        self.model = model

    @staticmethod
    def is_enabled() -> bool:
        return os.environ.get("ALLOW_PAID_VIDEO_PROVIDERS", "").strip().lower() == "true"

    def generate(
        self, *, image_path: Path, motion_prompt: str, output_path: Path, target_duration_seconds: float
    ) -> VideoOutcome:
        if not self.is_enabled():
            return VideoOutcome(
                status="failed",
                error="PROVIDER_REQUIRES_PAID_ACCESS: Veo is a billed Google Cloud API; "
                "set ALLOW_PAID_VIDEO_PROVIDERS=true to enable it explicitly",
            )
        if not self.project_id or not self.access_token:
            return VideoOutcome(status="failed", error="PROVIDER_UNAVAILABLE: missing GCP project id or access token")

        image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        submit_url = VEO_SUBMIT_URL.format(location=self.location, project=self.project_id, model=self.model)
        body = {
            "instances": [{"prompt": motion_prompt, "image": {"bytesBase64Encoded": image_b64, "mimeType": "image/png"}}],
            "parameters": {"aspectRatio": "16:9", "sampleCount": 1},
        }
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            submit_resp = requests.post(submit_url, headers=headers, json=body, timeout=60)
        except requests.RequestException as exc:
            return VideoOutcome(status="failed", error=f"submit network error: {exc}")
        if not submit_resp.ok:
            return VideoOutcome(status="failed", error=f"submit HTTP {submit_resp.status_code}: {submit_resp.text[:600]}")

        operation_name = submit_resp.json().get("name", "")
        if not operation_name:
            return VideoOutcome(status="failed", error="submit response contained no operation name")

        poll_url = VEO_POLL_URL.format(location=self.location, project=self.project_id, model=self.model)
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(12)
            try:
                poll_resp = requests.post(poll_url, headers=headers, json={"operationName": operation_name}, timeout=30)
            except requests.RequestException as exc:
                return VideoOutcome(status="failed", error=f"poll network error: {exc}", generation_id=operation_name)
            if not poll_resp.ok:
                return VideoOutcome(
                    status="failed", error=f"poll HTTP {poll_resp.status_code}: {poll_resp.text[:600]}", generation_id=operation_name
                )
            poll_body = poll_resp.json()
            if poll_body.get("done"):
                videos = poll_body.get("response", {}).get("videos", [])
                if not videos or not videos[0].get("bytesBase64Encoded"):
                    return VideoOutcome(
                        status="failed",
                        error="operation completed but returned no inline video bytes (gcsUri-only output is not handled by this provider)",
                        generation_id=operation_name,
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(base64.b64decode(videos[0]["bytesBase64Encoded"]))
                return VideoOutcome(status="ready", generation_id=operation_name)
        return VideoOutcome(status="failed", error="operation polling timed out after 600s", generation_id=operation_name)


def default_providers(manifest_path: Path) -> list[VideoProvider]:
    """The free, always-available path only - VeoApiProvider is deliberately
    NOT included here (see module docstring); callers who have explicitly
    opted into paid access construct and pass it themselves."""
    return [FlowManualProvider(manifest_path)]


def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_scene_video(
    *,
    image_path: Path,
    motion_prompt: str,
    output_path: Path,
    target_duration_seconds: float,
    providers: list[VideoProvider],
    state_path: Path,
) -> dict:
    """Runs the image-to-video generation sequence for one scene. Idempotent:
    an already-VIDEO_READY record with an existing file is returned
    unchanged without calling any provider."""
    existing = read_json(state_path, None)
    if isinstance(existing, dict) and existing.get("status") == "VIDEO_READY" and Path(existing.get("path", "")).exists():
        return existing

    record: dict = {
        "motion_prompt": motion_prompt,
        "image_path": str(image_path),
        "path": str(output_path),
        "status": "VIDEO_GENERATING",
        "created_at": iso_now(),
    }
    write_json(state_path, record)

    last_error = "no providers configured"
    for provider in providers:
        outcome = provider.generate(
            image_path=image_path, motion_prompt=motion_prompt, output_path=output_path, target_duration_seconds=target_duration_seconds
        )

        if outcome.status == "awaiting_manual_import":
            record = {**record, "status": "VIDEO_AWAITING_MANUAL_IMPORT", "provider": provider.name, "awaiting_since": iso_now()}
            write_json(state_path, record)
            return record

        if outcome.status != "ready":
            last_error = f"{provider.name}: {outcome.error}"
            continue

        ok, reason = check_video(output_path, target_duration_seconds)
        if not ok:
            last_error = f"{provider.name}: generated video failed validation: {reason}"
            if output_path.exists():
                output_path.unlink()
            continue

        record = {
            "motion_prompt": motion_prompt,
            "image_path": str(image_path),
            "provider": provider.name,
            "generation_id": outcome.generation_id,
            "path": str(output_path),
            "checksum": _sha256_of_file(output_path),
            "status": "VIDEO_READY",
            "created_at": record["created_at"],
            "ready_at": iso_now(),
            "error": "",
        }
        write_json(state_path, record)
        return record

    record = {**record, "status": "VIDEO_FAILED", "error": last_error, "failed_at": iso_now()}
    write_json(state_path, record)
    return record


def complete_manual_import(state_path: Path, target_duration_seconds: float) -> dict:
    """Call once a human has placed the Flow-generated clip at the path a
    VIDEO_AWAITING_MANUAL_IMPORT record's `path` field names. Validates the
    real file and finalizes the record to VIDEO_READY or VIDEO_FAILED."""
    record = read_json(state_path, None)
    if not isinstance(record, dict) or record.get("status") != "VIDEO_AWAITING_MANUAL_IMPORT":
        raise ValueError(f"state at {state_path} is not VIDEO_AWAITING_MANUAL_IMPORT")

    output_path = Path(record["path"])
    ok, reason = check_video(output_path, target_duration_seconds)
    if not ok:
        record = {**record, "status": "VIDEO_FAILED", "error": f"manual import failed validation: {reason}", "failed_at": iso_now()}
        write_json(state_path, record)
        return record

    record = {
        **record,
        "checksum": _sha256_of_file(output_path),
        "status": "VIDEO_READY",
        "ready_at": iso_now(),
        "error": "",
    }
    write_json(state_path, record)
    return record
