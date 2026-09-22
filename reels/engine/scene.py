"""Declarative scene timeline - the format from the spec:

    scene: {duration: 8}
    events: [{time, actor, action, target/text/value}, ...]

"The timeline becomes the source of truth for scene behavior." Replaces
Phase 6's hand-written beat-by-beat script: a Scene holds events + actor/prop
WORLD POSITIONS, and computes each actor's live state (gaze angle, talking,
pose/expression) at any time t by folding all events with time <= t in
order - each event sets a persistent channel value that holds until a later
event for that actor+channel changes it, not just an instantaneous snapshot.

Gaze angles are now DERIVED from real positions (fixing the Phase 6 "Known
Problem": hand-tuned angle constants) via a simple horizontal-offset model -
these are flat, front-facing 2D cutout characters, not 3D actors, so "look
toward x" is a head-turn proportional to horizontal offset, clamped well
inside the head joint's own limit (see joints.py), not true 3D gaze geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from motion_layers import Performance, PerformanceInput
from rig import Rig
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

GAZE_DISTANCE_SCALE = 12.0  # world-units of horizontal offset per degree
GAZE_MAX_ANGLE = 40.0       # stays inside the head joint's own +-45 limit


@dataclass
class Actor:
    name: str
    character: str
    svg_path: Path
    position: tuple[float, float]  # world (x, y); also the canvas placement offset


@dataclass
class SceneEvent:
    time: float
    actor: str
    action: str  # look_at | talk | stop_talk | expression | point_to | wait
    target: str | None = None
    text: str | None = None
    value: str | None = None


@dataclass
class _ActorChannelState:
    gaze_target: str | float = "camera"
    talking: bool = False
    talk_start_t: float = 0.0
    state: str = "idle"


def gaze_angle_between(from_pos: tuple[float, float], to_pos: tuple[float, float]) -> float:
    dx = to_pos[0] - from_pos[0]
    angle = -dx / GAZE_DISTANCE_SCALE
    return max(-GAZE_MAX_ANGLE, min(GAZE_MAX_ANGLE, angle))


class Scene:
    def __init__(self, duration: float, actors: list[Actor], events: list[SceneEvent], blink_seed_start: int = 1):
        self.duration = duration
        self.actors = {a.name: a for a in actors}
        self.positions = {a.name: a.position for a in actors}
        self.events = sorted(events, key=lambda e: e.time)
        self._performances = {
            a.name: Performance(
                Rig(a.svg_path, character=a.character),
                blink_seed=blink_seed_start + i,
                duration_s=duration,
            )
            for i, a in enumerate(actors)
        }

    def add_prop_position(self, name: str, position: tuple[float, float]) -> None:
        self.positions[name] = position

    def state_at(self, t: float) -> dict[str, _ActorChannelState]:
        states = {name: _ActorChannelState() for name in self.actors}
        for ev in self.events:
            if ev.time > t:
                break
            st = states[ev.actor]
            if ev.action == "look_at":
                st.gaze_target = ev.target
            elif ev.action == "talk":
                st.talking = True
                st.talk_start_t = ev.time
            elif ev.action == "stop_talk":
                st.talking = False
            elif ev.action in ("expression", "action", "state"):
                st.state = ev.value
            elif ev.action == "point_to":
                st.state = "point"
                st.gaze_target = ev.target
            elif ev.action == "wait":
                pass
            else:
                raise ValueError(f"Unknown scene action {ev.action!r} for actor {ev.actor!r}")
        return states

    def _gaze_value(self, actor_name: str, gaze_target) -> float | str:
        if gaze_target is None or gaze_target == "camera":
            return "camera"
        if gaze_target not in self.positions:
            raise ValueError(f"Unknown gaze target {gaze_target!r} - not an actor or registered prop position")
        return gaze_angle_between(self.positions[actor_name], self.positions[gaze_target])

    def render_frame(self, t: float, out_path: Path) -> dict:
        if not (0.0 <= t <= self.duration):
            raise ValueError(f"t={t} outside scene duration [0, {self.duration}]")
        states = self.state_at(t)
        report = {}
        svg = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": "0 0 800 800"})

        for name, actor in self.actors.items():
            st = states[name]
            perf = self._performances[name]
            gaze_value = self._gaze_value(name, st.gaze_target)
            frame_report = perf.render_at(
                t, PerformanceInput(state=st.state, gaze_target=gaze_value, talking=st.talking, talk_start_t=st.talk_start_t)
            )
            perf.rig.apply()
            g = ET.SubElement(svg, f"{{{SVG_NS}}}g", {"transform": f"translate({actor.position[0] - 200},0)"})
            # Append the character SVG's CHILDREN, not the nested <svg> root
            # itself - a nested <svg> with no explicit width/height falls back
            # to ambiguous default-viewport sizing that doesn't reliably
            # render (confirmed: produced a blank page). Flat <g> nesting has
            # no such ambiguity.
            for child in perf.rig.tree.getroot():
                g.append(child)
            report[name] = {**frame_report, "gaze": st.gaze_target}

        ET.ElementTree(svg).write(out_path, xml_declaration=True, encoding="utf-8")
        return report
