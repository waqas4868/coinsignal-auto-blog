"""Basic viseme-like mouth cycling while talking is active.

Deliberately a simple deterministic time-based cycle for Phase 5 - the spec
asks the system to "synchronize speaking state with dialogue/audio timing",
which properly means driving this from real phoneme/audio timing once audio
exists in the pipeline. That's later (voice_manager.py territory); this
gives the mechanism something correct to plug into rather than a fixed
open/close loop, and is itself reproducible (same t always gives the same
viseme) rather than random mouth flapping.
"""
from __future__ import annotations

VISEME_SEQUENCE = [
    "viseme_closed", "viseme_small", "viseme_open",
    "viseme_small", "viseme_wide", "viseme_small",
]
STEP_SECONDS = 0.12


def viseme_at(t: float, talk_start_t: float = 0.0) -> str:
    if t < talk_start_t:
        return "viseme_closed"
    elapsed = t - talk_start_t
    idx = int(elapsed / STEP_SECONDS) % len(VISEME_SEQUENCE)
    return VISEME_SEQUENCE[idx]
