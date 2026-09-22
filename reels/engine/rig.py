"""Applies pose data to a character SVG by writing transform="translate(..)
rotate(..)" onto each named joint <g>, using SVG's native nested-transform
composition as the forward-kinematics engine (see alex.svg's docstring).

Uses the standard library's xml.etree.ElementTree rather than adding a new
dependency (lxml) for what's still prototype-phase code - see STATUS.md.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from joints import IK_CHAINS, JOINT_LIMITS, clamp_angle

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

_TRANSLATE_RE = re.compile(r"translate\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)")


class Rig:
    def __init__(self, svg_path: Path):
        self.svg_path = Path(svg_path)
        self.tree = ET.parse(self.svg_path)
        self.root = self.tree.getroot()
        self._elements: dict[str, ET.Element] = {
            el.get("id"): el for el in self.root.iter() if el.get("id")
        }
        # Rest-pose local (tx, ty) per joint, read once from the SVG itself -
        # the SVG file stays the single source of truth for rest geometry.
        self._rest_offset: dict[str, tuple[float, float]] = {}
        for joint_id in JOINT_LIMITS:
            el = self._elements.get(joint_id)
            if el is None:
                raise ValueError(f"Joint {joint_id!r} not found in {self.svg_path}")
            match = _TRANSLATE_RE.search(el.get("transform", ""))
            self._rest_offset[joint_id] = (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)

        self.pose: dict[str, float] = {joint_id: 0.0 for joint_id in JOINT_LIMITS}
        self.clamped_this_pose: list[str] = []

    def set_angle(self, joint_id: str, degrees: float) -> float:
        if joint_id not in JOINT_LIMITS:
            raise ValueError(f"Unknown joint {joint_id!r}")
        clamped, was_clamped = clamp_angle(joint_id, degrees)
        if was_clamped:
            self.clamped_this_pose.append(
                f"{joint_id}: requested {degrees:.1f} deg, clamped to {clamped:.1f} deg"
            )
        self.pose[joint_id] = clamped
        return clamped

    def reset_pose(self) -> None:
        self.pose = {joint_id: 0.0 for joint_id in JOINT_LIMITS}
        self.clamped_this_pose = []

    def point_to(self, side: str, target_local: tuple[float, float], upper_len: float, lower_len: float) -> None:
        """2-bone IK (law of cosines) for an arm chain reaching target_local -
        a point in the shoulder's PARENT (torso) local coordinate space.
        Clamped to joint limits like any other set_angle() call, so an
        unreachable target still yields a valid (not impossible) pose.
        """
        chain_key = f"{side}-arm"
        if chain_key not in IK_CHAINS:
            raise ValueError(f"No IK chain defined for {chain_key!r}")
        upper_id, lower_id, _hand_id = IK_CHAINS[chain_key]
        shoulder_x, shoulder_y = self._rest_offset[upper_id]

        dx = target_local[0] - shoulder_x
        dy = target_local[1] - shoulder_y
        dist = math.hypot(dx, dy)
        dist = min(dist, upper_len + lower_len - 1e-6)  # clamp to reachable radius
        dist = max(dist, abs(upper_len - lower_len) + 1e-6)

        # Law of cosines for the elbow magnitude, then the shoulder offset
        # needed to aim the 2-bone chain at the target. Both have two valid
        # solutions (elbow-up / elbow-down) - verified numerically via
        # forward-kinematics (see conversation) that side_sign=+1 (left) /
        # -1 (right), applied to BOTH terms together, is the solution
        # consistent with each side's forearm bend direction in JOINT_LIMITS
        # (left forearm bends positive, right bends negative). Using the
        # wrong pairing still finds *a* pose but the hand misses the target
        # by roughly double the reach distance - confirmed by the same check.
        side_sign = 1.0 if side == "left" else -1.0

        cos_elbow = (upper_len**2 + lower_len**2 - dist**2) / (2 * upper_len * lower_len)
        cos_elbow = max(-1.0, min(1.0, cos_elbow))
        elbow_bend = side_sign * math.degrees(math.pi - math.acos(cos_elbow))

        angle_to_target = math.degrees(math.atan2(dy, dx))
        cos_shoulder_offset = (upper_len**2 + dist**2 - lower_len**2) / (2 * upper_len * dist)
        cos_shoulder_offset = max(-1.0, min(1.0, cos_shoulder_offset))
        shoulder_offset = side_sign * math.degrees(math.acos(cos_shoulder_offset))

        # Rest pose points "down" (90 deg in atan2 terms); rig angle is
        # measured from that rest direction, matching set_angle()'s convention.
        rest_direction_deg = 90.0
        shoulder_angle = (angle_to_target - shoulder_offset) - rest_direction_deg

        self.set_angle(upper_id, shoulder_angle)
        self.set_angle(lower_id, elbow_bend)

    def apply(self) -> ET.ElementTree:
        for joint_id, angle in self.pose.items():
            el = self._elements[joint_id]
            tx, ty = self._rest_offset[joint_id]
            if abs(angle) < 1e-6:
                el.set("transform", f"translate({tx},{ty})")
            else:
                el.set("transform", f"translate({tx},{ty}) rotate({angle:.3f})")
        return self.tree

    def save(self, output_path: Path) -> Path:
        self.apply()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.tree.write(output_path, xml_declaration=True, encoding="utf-8")
        return output_path
