"""Time-driven procedural motion: breathing, weight shift, blink scheduling.

"Use seeded randomness where randomness is needed... the same render input
should remain reproducible. Do not create visible random shaking." - so
nothing here calls random.random() per-frame; a seed produces a fixed blink
SCHEDULE once, and breathing/weight-shift are smooth deterministic sine
waves, not per-frame noise.
"""
from __future__ import annotations

import math
import random


def breathing_torso_angle(t: float) -> float:
    """Subtle torso rotation standing in for chest rise/fall (see rig.py -
    the transform format doesn't carry a separate scale channel, and a small
    rotation reads as believable subtle life in this line-art style)."""
    period_s = 3.3
    amplitude_deg = 1.4
    return amplitude_deg * math.sin(2 * math.pi * t / period_s)


def weight_shift_pelvis_angle(t: float) -> float:
    """Slow, gentle idle sway - deliberately a different period than
    breathing so the two never lock into a mechanical-looking combined cycle."""
    period_s = 5.7
    amplitude_deg = 1.8
    return amplitude_deg * math.sin(2 * math.pi * t / period_s + 0.8)


class BlinkSchedule:
    """A fixed, seeded sequence of blink times - computed once, not re-rolled
    per frame, so the same (seed, t) always gives the same answer."""

    def __init__(self, seed: int, duration_s: float, avg_interval_s: float = 3.5, blink_len_s: float = 0.12):
        rng = random.Random(seed)
        self.blink_len_s = blink_len_s
        self._times: list[float] = []
        t = rng.uniform(1.0, avg_interval_s)
        while t < duration_s:
            self._times.append(t)
            # Natural blinking isn't metronomic - jitter the interval, not the
            # per-frame state, so it stays reproducible and non-mechanical.
            t += rng.uniform(avg_interval_s * 0.6, avg_interval_s * 1.6)

    def is_blinking(self, t: float) -> bool:
        return any(bt <= t <= bt + self.blink_len_s for bt in self._times)
