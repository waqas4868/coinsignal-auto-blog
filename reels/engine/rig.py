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

from expressions import apply_expression, MOUTH_SHAPES
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

    def set_expression(self, expression_name: str) -> None:
        apply_expression(self._elements, expression_name)

    # --- Layering primitives (Phase 5): each ADDS to or OVERRIDES one
    # specific channel without touching the others, so multiple concerns
    # (base pose, breathing, gaze, blink, talk) can compose in one frame
    # instead of each fully overwriting the last. ---

    def nudge_angle(self, joint_id: str, delta_degrees: float) -> float:
        """Adds delta to whatever angle is already set (e.g. by a pose or an
        earlier layer) rather than replacing it - still clamped."""
        current = self.pose.get(joint_id, 0.0)
        return self.set_angle(joint_id, current + delta_degrees)

    def add_eye_offset(self, dx: float) -> None:
        """Appends a small extra translate to each eye's CURRENT transform
        (whatever set_expression already produced) - must be called after
        set_expression() in the composition order, not before."""
        for eye_id in ("alex-left-eye", "alex-right-eye"):
            el = self._elements[eye_id]
            current = el.get("transform", "")
            el.set("transform", f"{current} translate({dx:.2f},0)".strip())

    def force_eyes_closed(self, closed: bool) -> None:
        """Blink override: only forces CLOSED when closed=True. Does nothing
        when False, so an expression that already wants closed eyes (e.g.
        "laughing") isn't fought by an inactive blink."""
        if not closed:
            return
        for eye_id in ("alex-left-eye", "alex-right-eye"):
            el = self._elements[eye_id]
            open_el = el.find(f"{{{SVG_NS}}}circle[@class='eye-open']")
            closed_el = el.find(f"{{{SVG_NS}}}path[@class='eye-closed']")
            if open_el is not None and closed_el is not None:
                open_el.set("display", "none")
                closed_el.set("display", "inline")

    def override_mouth(self, shape_key: str) -> None:
        """Talk override: replaces whatever set_expression put in the mouth
        path with a viseme shape, independent of eyebrows/eyes."""
        mouth_group = self._elements["alex-mouth"]
        mouth_path = mouth_group.find(f"{{{SVG_NS}}}path")
        mouth_path.set("d", MOUTH_SHAPES[shape_key])

    def save(self, output_path: Path) -> Path:
        self.apply()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.tree.write(output_path, xml_declaration=True, encoding="utf-8")
        return output_path
