"""The layer composer: renders a character's pose at a specific time by
combining independent concerns, each touching only its own channels -
this is what makes "idle + blink + talk + gaze can coexist" (Phase 5 PASS
criteria) true, instead of the Phase 4 model where applying one state
fully overwrote everything.

Composition order matters and is deliberate:
    1. base STATE (pose + expression) - sets the baseline for every channel
    2. BASE procedural (breathing, weight shift) - nudges torso/pelvis
    3. GAZE - nudges head, offsets eyes (must run after the state's own
       expression call so the eye offset stacks on top of it)
    4. BLINK - overrides eyes CLOSED only when actively blinking
    5. TALK - overrides the mouth shape only while talking is active
Each later step only touches its own channel(s); it never re-runs an
earlier step's logic, so nothing here can silently undo another layer.
"""
from __future__ import annotations

from dataclasses import dataclass

from gaze import compute_gaze
from procedural import BlinkSchedule, breathing_torso_angle, weight_shift_pelvis_angle
from states import apply_state
from talk import viseme_at


@dataclass
class PerformanceInput:
    """What a scene/timeline (a later phase) would specify for one instant."""
    state: str = "idle"
    gaze_target: str | float = "camera"
    talking: bool = False
    talk_start_t: float = 0.0


class Performance:
    def __init__(self, rig, *, blink_seed: int = 0, duration_s: float = 30.0):
        self.rig = rig
        self.blink_schedule = BlinkSchedule(seed=blink_seed, duration_s=duration_s)

    def render_at(self, t: float, perf_input: PerformanceInput) -> dict:
        """Applies all layers to self.rig for time t. Returns a small report
        (which layers were actually active) useful for tests/debugging."""
        report = {"state": perf_input.state, "blinking": False, "talking": False}

        # 1. Base state - the only step allowed to touch every channel.
        apply_state(self.rig, perf_input.state)

        # 2. Base procedural motion - additive nudges only.
        c = self.rig.character
        self.rig.nudge_angle(f"{c}-torso", breathing_torso_angle(t))
        self.rig.nudge_angle(f"{c}-pelvis", weight_shift_pelvis_angle(t))

        # 3. Gaze - additive head nudge + eye offset (eyes already have a
        # transform from step 1's expression call, so this stacks correctly).
        gaze = compute_gaze(perf_input.gaze_target)
        self.rig.nudge_angle(f"{c}-head", gaze.head_extra_deg)
        self.rig.add_eye_offset(gaze.eye_offset_x)

        # 4. Blink - overrides eyes-closed only while actively blinking;
        # leaves the expression's own eye state alone otherwise.
        is_blinking = self.blink_schedule.is_blinking(t)
        if is_blinking:
            self.rig.force_eyes_closed(True)
        report["blinking"] = is_blinking

        # 5. Talk - overrides only the mouth shape.
        if perf_input.talking:
            viseme = viseme_at(t, perf_input.talk_start_t)
            self.rig.override_mouth(viseme)
            report["talking"] = True

        return report
