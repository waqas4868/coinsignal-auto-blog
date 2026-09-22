"""Phase 6 visual proof script (not part of the engine API - Phase 7's
scene.py will formalize scene composition). Composites Alex (screen-left)
and Jake (screen-right) into one canvas per beat of the spec's example
interaction, to prove "characters look, speak, listen and react" - not a
frozen listener.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from motion_layers import Performance, PerformanceInput
from rig import Rig

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

OUT = Path("../../phase6_review")
ALEX_SVG = Path("../characters/alex/alex.svg")
JAKE_SVG = Path("../characters/jake/jake.svg")

# Alex is placed at canvas x=0..400 (his own viewBox), Jake at x=400..800 -
# side by side, facing each other. A look toward the other character is a
# negative angle for Alex (Jake is to his screen-right) and positive for
# Jake (Alex is to his screen-left) per the documented rotation convention.
LOOK_AT_OTHER_FROM_ALEX = -32.0
LOOK_AT_OTHER_FROM_JAKE = 32.0
LOOK_AT_CHART_FROM_ALEX = -12.0   # a chart placed between/above them
LOOK_AT_CHART_FROM_JAKE = -18.0


def compose(alex_tree: ET.ElementTree, jake_tree: ET.ElementTree, out_path: Path) -> None:
    svg = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": "0 0 800 800"})
    alex_g = ET.SubElement(svg, f"{{{SVG_NS}}}g", {"transform": "translate(0,0)"})
    alex_g.append(alex_tree.getroot())
    jake_g = ET.SubElement(svg, f"{{{SVG_NS}}}g", {"transform": "translate(400,0)"})
    jake_g.append(jake_tree.getroot())
    ET.ElementTree(svg).write(out_path, xml_declaration=True, encoding="utf-8")


def beat(name: str, t: float,
         alex_state: str, alex_gaze, alex_talking: bool,
         jake_state: str, jake_gaze, jake_talking: bool) -> None:
    alex_rig = Rig(ALEX_SVG, character="alex")
    Performance(alex_rig, blink_seed=1, duration_s=10.0).render_at(
        t, PerformanceInput(state=alex_state, gaze_target=alex_gaze, talking=alex_talking)
    )
    alex_rig.apply()

    jake_rig = Rig(JAKE_SVG, character="jake")
    Performance(jake_rig, blink_seed=2, duration_s=10.0).render_at(
        t, PerformanceInput(state=jake_state, gaze_target=jake_gaze, talking=jake_talking)
    )
    jake_rig.apply()

    compose(alex_rig.tree, jake_rig.tree, OUT / f"{name}.svg")
    print(f"{name}: alex(state={alex_state}, gaze={alex_gaze}, talk={alex_talking}) "
          f"jake(state={jake_state}, gaze={jake_gaze}, talk={jake_talking})")


# Beat 1: Alex speaks, looking at Jake. Jake looks at Alex, listening (idle,
# NOT frozen - still breathing/blinking via the Phase 5 layers).
beat("beat1_alex_speaks", 1.0,
     "idle", LOOK_AT_OTHER_FROM_ALEX, True,
     "idle", LOOK_AT_OTHER_FROM_JAKE, False)

# Beat 2: Jake reacts (shocked) to what Alex just said - still looking at Alex.
beat("beat2_jake_reacts", 2.0,
     "idle", LOOK_AT_OTHER_FROM_ALEX, False,
     "shocked", LOOK_AT_OTHER_FROM_JAKE, False)

# Beat 3: Alex points toward the chart; Jake turns to look at the chart too.
beat("beat3_point_and_turn", 3.0,
     "point", LOOK_AT_CHART_FROM_ALEX, True,
     "idle", LOOK_AT_CHART_FROM_JAKE, False)

# Beat 4: Jake reacts to the chart.
beat("beat4_jake_reacts_to_chart", 4.0,
     "idle", LOOK_AT_CHART_FROM_ALEX, False,
     "shocked", LOOK_AT_CHART_FROM_JAKE, False)
