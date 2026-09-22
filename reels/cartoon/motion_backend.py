"""Abstract interface so the cartoon Reel pipeline isn't tied to one backend.

Only FreeTestBackend exists right now (see free_test_backend.py). A future
paid backend (Hunyuan/other) would implement this same interface - the
pipeline code above this layer should never need to change to add one.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SceneResult:
    video_path: Path
    width: int
    height: int
    num_frames: int
    duration_seconds: float
    seed: int
    backend_name: str
    cost_usd: float  # always 0.0 for free backends; present for future paid ones


class MotionBackend(ABC):
    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Returns what this backend actually supports - never assume, always report."""

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """Returns (ok, message). Must not raise for an expected-down backend."""

    @abstractmethod
    def estimate_cost(self, *, steps: int, duration_seconds: float, reference_image: Path) -> float:
        """Backend-specific cost estimate (GPU-seconds, dollars, etc.) for preflight checks."""

    @abstractmethod
    def generate_single_character_scene(
        self,
        reference_image: Path,
        motion_prompt: str,
        *,
        steps: int,
        duration_seconds: float,
        seed: int,
        negative_prompt: str = "",
    ) -> SceneResult:
        """One character, one continuous shot. No audio, no second character."""

    def generate_multi_character_scene(self, *args, **kwargs) -> SceneResult:
        raise NotImplementedError("Not supported by the current free backend - see capabilities().")

    def generate_dialogue_scene(self, *args, **kwargs) -> SceneResult:
        raise NotImplementedError("Not supported by the current free backend - see capabilities().")

    def generate_reaction_scene(self, *args, **kwargs) -> SceneResult:
        raise NotImplementedError("Not supported by the current free backend - see capabilities().")
