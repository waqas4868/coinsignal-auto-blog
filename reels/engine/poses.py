"""Reusable BODY poses: named dicts of {joint_suffix: degrees}, applied
through Rig.set_angle() so every pose automatically respects the Phase 3
angle limits - a pose can request an unreasonable angle and it will clamp
rather than break, same as any manual set_angle() call.

Character-agnostic (Phase 6 refactor): keyed by joint SUFFIX ("head", not
"alex-head"), prefixed with rig.character at apply time - the same pose
library works for Alex or Jake since they share rig topology.

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
        "right-upper-arm": -5.8,
        "right-forearm": -91.5,
        "head": -8,
    },
    "point_left": {
        "left-upper-arm": 5.8,
        "left-forearm": 91.5,
        "head": 8,
    },

    "wave": {
        "left-upper-arm": 110,
        "left-forearm": 60,
        "head": 10,
    },

    "shrug": {
        "left-upper-arm": 70,
        "left-forearm": 100,
        "right-upper-arm": -70,
        "right-forearm": -100,
        "torso": 4,
    },

    "shock_recoil": {
        "head": -12,
        "torso": -8,
        "left-upper-arm": 55,
        "left-forearm": 40,
        "right-upper-arm": -55,
        "right-forearm": -40,
    },

    "angry_lean": {
        "torso": 10,
        "head": -6,
        "left-upper-arm": 20,
        "right-upper-arm": -20,
    },

    "turn_toward_left": {
        "head": 30,
        "torso": 12,
    },
    "turn_toward_right": {
        "head": -30,
        "torso": -12,
    },

    "walk_contact": {
        # One leg forward planted, one back - a single "contact" key pose
        # from the WALK cycle (contact -> down -> passing -> up -> contact)
        # described in the spec; the full cycle is a Phase 5/7 concern once
        # timing/interpolation exists, this is just the reusable key pose.
        "left-thigh": -35,
        "right-thigh": 35,
        "left-upper-arm": -25,
        "right-upper-arm": 25,
    },
}


def apply_pose(rig, pose_name: str) -> list[str]:
    """Applies a named pose to rig (a reels.engine.rig.Rig). Returns any
    clamp warnings so callers can decide whether to log/report them.

    Always resets every joint to rest (0 deg) first, THEN applies the named
    pose's overrides - a pose like "idle" that lists no joints must mean
    "rest pose", not "whatever the rig happened to be posed as before this
    call". Without this reset, reusing one Rig/Performance across multiple
    render_at(t) calls (as Scene.render_frame does, once per actor) leaks
    stale angles from an earlier frame's pose into a later frame that never
    asked for them - confirmed by an actual scene render where "idle" after
    "point" kept the arm extended (see conversation).
    """
    if pose_name not in POSES:
        raise ValueError(f"Unknown pose {pose_name!r}. Known poses: {sorted(POSES)}")
    rig.reset_pose()
    for suffix, angle in POSES[pose_name].items():
        rig.set_angle(f"{rig.character}-{suffix}", angle)
    return list(rig.clamped_this_pose)
