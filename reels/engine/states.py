"""A STATE is what a scene actually references (per the spec's runtime-
character idea, e.g. Alex.expression("shocked")): a named combination of a
body pose + a face expression, applied together. This is the layer above
poses.py/expressions.py that keeps those two reusable independently while
giving scene authors one name per meaningful acting beat.

"Expressions must use MULTIPLE channels" (body + face here, not just mouth)
is enforced structurally: every state pairs a pose with an expression.
"""
from __future__ import annotations

from poses import apply_pose

STATES: dict[str, tuple[str, str]] = {  # name -> (pose_name, expression_name)
    "idle": ("idle", "neutral"),
    "point": ("point_right", "neutral"),
    "wave": ("wave", "happy"),
    "shrug": ("shrug", "confused"),
    "shocked": ("shock_recoil", "shocked"),
    "angry": ("angry_lean", "angry"),
    "laugh": ("idle", "laughing"),
    "excited": ("wave", "excited"),
}


def apply_state(rig, state_name: str) -> list[str]:
    if state_name not in STATES:
        raise ValueError(f"Unknown state {state_name!r}. Known: {sorted(STATES)}")
    pose_name, expression_name = STATES[state_name]
    clamped = apply_pose(rig, pose_name)
    rig.set_expression(expression_name)
    return clamped
