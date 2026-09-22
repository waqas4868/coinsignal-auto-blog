"""The Phase 6 interaction, expressed as real declarative scene data instead
of a hand-written script - this is the Phase 7 deliverable: "the timeline
becomes the source of truth for scene behavior."

Matches the spec's example event schema (time/actor/action/target/text).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/engine, for sibling imports
from scene import Actor, Scene, SceneEvent

CHARACTERS_DIR = Path(__file__).resolve().parent.parent.parent / "characters"

ACTORS = [
    Actor("alex", "alex", CHARACTERS_DIR / "alex" / "alex.svg", position=(200, 400)),
    Actor("jake", "jake", CHARACTERS_DIR / "jake" / "jake.svg", position=(600, 400)),
]

EVENTS = [
    SceneEvent(time=0.0, actor="alex", action="look_at", target="jake"),
    SceneEvent(time=0.3, actor="jake", action="look_at", target="alex"),
    SceneEvent(time=1.0, actor="alex", action="talk", text="Bro... what just happened to Bitcoin?"),
    SceneEvent(time=2.5, actor="alex", action="stop_talk"),
    SceneEvent(time=2.6, actor="jake", action="expression", value="shocked"),
    SceneEvent(time=4.0, actor="alex", action="point_to", target="btc-chart"),
    SceneEvent(time=4.2, actor="jake", action="look_at", target="btc-chart"),
    SceneEvent(time=4.8, actor="jake", action="expression", value="shocked"),
    SceneEvent(time=6.5, actor="alex", action="expression", value="idle"),
    SceneEvent(time=6.5, actor="alex", action="look_at", target="jake"),
]

DURATION = 8.0


def build_scene() -> Scene:
    scene = Scene(duration=DURATION, actors=ACTORS, events=EVENTS)
    scene.add_prop_position("btc-chart", (400, 150))
    return scene
