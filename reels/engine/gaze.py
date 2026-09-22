"""Target-based gaze: head rotation and eye offset move together but are
computed as separate, coordinated channels (per the spec: "Head gaze and
eye gaze can be separate but coordinated" / "Create a reusable gaze
controller" rather than hand-coding eye rotations per scene).

2D-only for now (no real depth/3D scene graph yet): a gaze target is just an
angle in the same degrees-from-rest convention as rig joints (positive =
toward screen-left, matching alex.svg's documented rotation convention).
A later scene/timeline phase can compute this angle from actual character/
prop positions; this module only owns "given an angle, how much head vs eye
movement produces it."
"""
from __future__ import annotations

from dataclasses import dataclass

# Head carries most of a look; eyes contribute a smaller, proportional offset
# on top - mimics how people mostly turn their head with a small eye lead/lag,
# rather than swivelling eyes alone for any real angle.
HEAD_SHARE = 0.75
EYE_OFFSET_PER_DEGREE = 0.22  # px of pupil-dot shift per degree of remaining gaze

GAZE_TARGETS: dict[str, float] = {
    "camera": 0.0,
}


@dataclass(frozen=True)
class GazeResult:
    head_extra_deg: float
    eye_offset_x: float


def resolve_target_angle(target) -> float:
    """target can be a named target (str) or a raw angle in degrees."""
    if isinstance(target, str):
        if target not in GAZE_TARGETS:
            raise ValueError(f"Unknown gaze target {target!r}. Known: {sorted(GAZE_TARGETS)}")
        return GAZE_TARGETS[target]
    return float(target)


def compute_gaze(target) -> GazeResult:
    angle = resolve_target_angle(target)
    head_extra = angle * HEAD_SHARE
    eye_remainder = angle - head_extra
    eye_offset_x = eye_remainder * EYE_OFFSET_PER_DEGREE
    return GazeResult(head_extra_deg=head_extra, eye_offset_x=eye_offset_x)
