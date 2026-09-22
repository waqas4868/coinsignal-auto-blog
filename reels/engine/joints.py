"""Joint definitions for the Alex/Jake rig: hierarchy, rotation axis, and
angle limits ("the system must prevent obviously impossible poses").

Parent-child structure matches the SVG nesting exactly (see
reels/characters/alex/alex.svg) - this module doesn't redefine the
hierarchy, it annotates it with what a rig actually needs beyond raw SVG
geometry: which joints rotate, and how far.

Angle sign convention: 0 degrees = rest pose (as drawn in the SVG).
Positive degrees = clockwise on screen (standard SVG rotate() direction).
Elbows/knees only bend one way in real anatomy, so their range is
asymmetric (0..max) rather than symmetric like shoulders/hips.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JointLimit:
    joint_id: str
    parent_id: str | None
    min_deg: float
    max_deg: float


# Angle ranges are deliberately conservative for a simple stick/cutout
# character - the goal is "prevents obviously impossible poses", not
# biomechanically precise human range of motion.
JOINT_LIMITS: dict[str, JointLimit] = {
    j.joint_id: j
    for j in [
        JointLimit("alex-pelvis", None, -10, 10),
        JointLimit("alex-torso", "alex-pelvis", -20, 20),
        JointLimit("alex-neck", "alex-torso", -25, 25),
        JointLimit("alex-head", "alex-neck", -45, 45),

        # Verified numerically (see conversation): for a limb hanging straight
        # down at rest, POSITIVE rotate() swings its endpoint toward
        # screen-left, NEGATIVE toward screen-right. The left shoulder sits
        # at screen-left, so POSITIVE = swinging further outward/away from
        # the body (large natural range, e.g. raising the arm up/out); NEGATIVE
        # = swinging inward across the body (small range, anatomically
        # limited). Mirrored for the right arm.
        JointLimit("alex-left-upper-arm", "alex-torso", -60, 150),
        JointLimit("alex-left-forearm", "alex-left-upper-arm", 0, 150),
        JointLimit("alex-left-hand", "alex-left-forearm", -40, 40),
        JointLimit("alex-right-upper-arm", "alex-torso", -150, 60),
        JointLimit("alex-right-forearm", "alex-right-upper-arm", -150, 0),
        JointLimit("alex-right-hand", "alex-right-forearm", -40, 40),

        JointLimit("alex-left-thigh", "alex-pelvis", -60, 90),
        JointLimit("alex-left-shin", "alex-left-thigh", 0, 140),
        JointLimit("alex-left-foot", "alex-left-shin", -30, 30),
        JointLimit("alex-right-thigh", "alex-pelvis", -90, 60),
        JointLimit("alex-right-shin", "alex-right-thigh", -140, 0),
        JointLimit("alex-right-foot", "alex-right-shin", -30, 30),
    ]
}

# Two-segment chains eligible for IK (proximal, distal, end-effector) -
# used for point_to()/reach-style poses. Not every joint chain needs IK
# (per the spec: "Do not use IK everywhere").
IK_CHAINS: dict[str, tuple[str, str, str]] = {
    "left-arm": ("alex-left-upper-arm", "alex-left-forearm", "alex-left-hand"),
    "right-arm": ("alex-right-upper-arm", "alex-right-forearm", "alex-right-hand"),
}


def clamp_angle(joint_id: str, degrees: float) -> tuple[float, bool]:
    """Returns (clamped_degrees, was_clamped)."""
    limit = JOINT_LIMITS.get(joint_id)
    if limit is None:
        return degrees, False
    clamped = max(limit.min_deg, min(limit.max_deg, degrees))
    return clamped, clamped != degrees
