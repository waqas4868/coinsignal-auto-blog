"""Joint definitions: hierarchy, rotation axis, and angle limits ("the
system must prevent obviously impossible poses").

Character-agnostic (Phase 6 refactor): defined once by SUFFIX
("left-upper-arm", not "alex-left-upper-arm") and instantiated per
character via for_character(). Both Alex and Jake share this exact rig
topology - only their SVG artwork differs - which is the actual point of
"one animation engine + reusable character states" rather than a separate
copy of this file per character.

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


# (suffix, parent_suffix_or_None, min_deg, max_deg)
_JOINT_SUFFIXES: list[tuple[str, str | None, float, float]] = [
    ("pelvis", None, -10, 10),
    ("torso", "pelvis", -20, 20),
    ("neck", "torso", -25, 25),
    ("head", "neck", -45, 45),

    # Verified numerically (see conversation): for a limb hanging straight
    # down at rest, POSITIVE rotate() swings its endpoint toward
    # screen-left, NEGATIVE toward screen-right. The left shoulder sits
    # at screen-left, so POSITIVE = swinging further outward/away from
    # the body (large natural range, e.g. raising the arm up/out); NEGATIVE
    # = swinging inward across the body (small range, anatomically
    # limited). Mirrored for the right arm.
    ("left-upper-arm", "torso", -60, 150),
    ("left-forearm", "left-upper-arm", 0, 150),
    ("left-hand", "left-forearm", -40, 40),
    ("right-upper-arm", "torso", -150, 60),
    ("right-forearm", "right-upper-arm", -150, 0),
    ("right-hand", "right-forearm", -40, 40),

    ("left-thigh", "pelvis", -60, 90),
    ("left-shin", "left-thigh", 0, 140),
    ("left-foot", "left-shin", -30, 30),
    ("right-thigh", "pelvis", -90, 60),
    ("right-shin", "right-thigh", -140, 0),
    ("right-foot", "right-shin", -30, 30),
]

# (chain_key_suffix, upper_suffix, lower_suffix, hand_suffix)
_IK_CHAIN_SUFFIXES: list[tuple[str, str, str, str]] = [
    ("left-arm", "left-upper-arm", "left-forearm", "left-hand"),
    ("right-arm", "right-upper-arm", "right-forearm", "right-hand"),
]


def joint_limits_for(character: str) -> dict[str, JointLimit]:
    return {
        f"{character}-{suffix}": JointLimit(
            f"{character}-{suffix}",
            f"{character}-{parent}" if parent else None,
            min_deg, max_deg,
        )
        for suffix, parent, min_deg, max_deg in _JOINT_SUFFIXES
    }


def ik_chains_for(character: str) -> dict[str, tuple[str, str, str]]:
    return {
        chain_key: (f"{character}-{upper}", f"{character}-{lower}", f"{character}-{hand}")
        for chain_key, upper, lower, hand in _IK_CHAIN_SUFFIXES
    }


def clamp_angle(joint_limits: dict[str, JointLimit], joint_id: str, degrees: float) -> tuple[float, bool]:
    """Returns (clamped_degrees, was_clamped)."""
    limit = joint_limits.get(joint_id)
    if limit is None:
        return degrees, False
    clamped = max(limit.min_deg, min(limit.max_deg, degrees))
    return clamped, clamped != degrees


# Kept for any code that still wants "the" joint list independent of a
# specific character (e.g. iterating suffixes) - not id-prefixed.
JOINT_SUFFIXES: list[str] = [s for s, _, _, _ in _JOINT_SUFFIXES]
