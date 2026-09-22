"""Reusable BODY poses: named dicts of {joint_id: degrees}, applied through
Rig.set_angle() so every pose automatically respects the Phase 3 angle
limits - a pose can request an unreasonable angle and it will clamp rather
than break, same as any manual set_angle() call.

Only joints that differ from rest (0 deg) need to be listed - anything
omitted stays at rest.
"""
from __future__ import annotations

POSES: dict[str, dict[str, float]] = {
    "idle": {},

    "point_right": {
        # Matches the IK-verified point_to() result for a chest-height
        # forward-right target (reels/engine/rig.py Rig.point_to) baked in
        # as a fixed reusable pose - avoids recomputing IK for the common case.
        "alex-right-upper-arm": -5.8,
        "alex-right-forearm": -91.5,
        "alex-head": -8,
    },
    "point_left": {
        "alex-left-upper-arm": 5.8,
        "alex-left-forearm": 91.5,
        "alex-head": 8,
    },

    "wave": {
        "alex-left-upper-arm": 110,
        "alex-left-forearm": 60,
        "alex-head": 10,
    },

    "shrug": {
        "alex-left-upper-arm": 70,
        "alex-left-forearm": 100,
        "alex-right-upper-arm": -70,
        "alex-right-forearm": -100,
        "alex-torso": 4,
    },

    "shock_recoil": {
        "alex-head": -12,
        "alex-torso": -8,
        "alex-left-upper-arm": 55,
        "alex-left-forearm": 40,
        "alex-right-upper-arm": -55,
        "alex-right-forearm": -40,
    },

    "angry_lean": {
        "alex-torso": 10,
        "alex-head": -6,
        "alex-left-upper-arm": 20,
        "alex-right-upper-arm": -20,
    },

    "turn_toward_left": {
        "alex-head": 30,
        "alex-torso": 12,
    },
    "turn_toward_right": {
        "alex-head": -30,
        "alex-torso": -12,
    },

    "walk_contact": {
        # One leg forward planted, one back - a single "contact" key pose
        # from the WALK cycle (contact -> down -> passing -> up -> contact)
        # described in the spec; the full cycle is a Phase 5/7 concern once
        # timing/interpolation exists, this is just the reusable key pose.
        "alex-left-thigh": -35,
        "alex-right-thigh": 35,
        "alex-left-upper-arm": -25,
        "alex-right-upper-arm": 25,
    },
}


def apply_pose(rig, pose_name: str) -> list[str]:
    """Applies a named pose to rig (a reels.engine.rig.Rig). Returns any
    clamp warnings so callers can decide whether to log/report them."""
    if pose_name not in POSES:
        raise ValueError(f"Unknown pose {pose_name!r}. Known poses: {sorted(POSES)}")
    rig.clamped_this_pose = []
    for joint_id, angle in POSES[pose_name].items():
        rig.set_angle(joint_id, angle)
    return list(rig.clamped_this_pose)
