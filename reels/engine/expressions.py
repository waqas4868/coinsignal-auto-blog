"""Reusable FACE expressions. Per the spec: "Expressions must use MULTIPLE
channels" - every expression here touches eyebrows AND eyes AND mouth, never
just the mouth alone.

Mechanism per channel (picking the simplest correct one, per the Phase 1
Transform-vs-Morph guidance):
- Eyebrows: TRANSFORM (rotate/translate) layered on top of their rest
  position - the geometry doesn't fundamentally change shape.
- Eyes: TRANSFORM (scale) for widen/narrow, but SHAPE MORPH (swap the open
  dot for a closed-arc "eye-closed" child, toggled via display) for a
  genuine happy-squint - a scaled-down filled dot doesn't read as "squinting"
  in this line-art style, confirmed by an actual visual test (see
  conversation) that a plain scale(0.55) was too subtle to be legible as
  "laughing" versus "neutral" side by side.
- Mouth: SHAPE MORPH (swap the child <path>'s "d" attribute) - a shocked-open
  mouth is genuinely different geometry from a closed smile, not a transform
  of the same shape.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"

MOUTH_SHAPES = {
    "neutral": "M -15,0 L 15,0",
    "smile": "M -18,0 Q 0,14 18,0",
    "big_smile": "M -24,-4 Q 0,26 24,-4 Q 0,14 -24,-4",
    "frown": "M -16,4 Q 0,-8 16,4",
    "open_shock": "M -13,-2 Q -13,16 0,16 Q 13,16 13,-2 Q 13,-10 0,-10 Q -13,-10 -13,-2 Z",
    "smirk": "M -16,2 Q 4,12 18,-2",
    # Visemes (talking mouth-openness states, Phase 5) - independent of the
    # emotional shapes above; deliberately distinct geometry per openness
    # level, not a scale of one shape, so they stay readable at this line
    # weight (same lesson as the "laughing" eyes fix - see conversation).
    "viseme_closed": "M -14,0 L 14,0",
    "viseme_small": "M -12,2 Q 0,8 12,2 Q 0,4 -12,2",
    "viseme_open": "M -14,-2 Q 0,14 14,-2 Q 0,6 -14,-2",
    "viseme_wide": "M -18,-2 Q 0,18 18,-2 Q 0,8 -18,-2",
}


class Expression:
    def __init__(
        self,
        *,
        left_eyebrow: str = "",
        right_eyebrow: str = "",
        eye_scale: float = 1.0,
        eyes_closed: bool = False,
        mouth: str = "neutral",
    ):
        # eyebrow extra transform (appended after the rest translate, e.g.
        # "rotate(-15)" or "translate(0,-6) rotate(10)") - "" means no change.
        self.left_eyebrow = left_eyebrow
        self.right_eyebrow = right_eyebrow
        self.eye_scale = eye_scale
        self.eyes_closed = eyes_closed
        self.mouth = mouth


EXPRESSIONS: dict[str, Expression] = {
    "neutral": Expression(mouth="smile"),
    "happy": Expression(left_eyebrow="translate(0,-7)", right_eyebrow="translate(0,-7)", mouth="smile"),
    "excited": Expression(
        left_eyebrow="translate(0,-9)", right_eyebrow="translate(0,-9)",
        eye_scale=1.5, mouth="big_smile",
    ),
    "laughing": Expression(
        left_eyebrow="translate(0,-8)", right_eyebrow="translate(0,-8)",
        eyes_closed=True, mouth="big_smile",
    ),
    "shocked": Expression(
        left_eyebrow="translate(0,-12)", right_eyebrow="translate(0,-12)",
        eye_scale=1.6, mouth="open_shock",
    ),
    "angry": Expression(
        left_eyebrow="translate(3,3) rotate(25)", right_eyebrow="translate(-3,3) rotate(-25)",
        eye_scale=0.7, mouth="frown",
    ),
    "confused": Expression(left_eyebrow="translate(0,-10) rotate(-12)", right_eyebrow="", mouth="smirk"),
    "sarcastic": Expression(left_eyebrow="translate(0,-7)", right_eyebrow="", mouth="smirk"),
}


def apply_expression(elements_by_id: dict[str, ET.Element], expression_name: str, character: str = "alex") -> None:
    if expression_name not in EXPRESSIONS:
        raise ValueError(f"Unknown expression {expression_name!r}. Known: {sorted(EXPRESSIONS)}")
    expr = EXPRESSIONS[expression_name]

    for eyebrow_suffix, extra_transform in (
        ("left-eyebrow", expr.left_eyebrow),
        ("right-eyebrow", expr.right_eyebrow),
    ):
        el = elements_by_id[f"{character}-{eyebrow_suffix}"]
        rest = el.get("data-rest-transform") or el.get("transform", "")
        el.set("data-rest-transform", rest)
        el.set("transform", f"{rest} {extra_transform}".strip())

    for eye_suffix in ("left-eye", "right-eye"):
        el = elements_by_id[f"{character}-{eye_suffix}"]
        rest = el.get("data-rest-transform") or el.get("transform", "")
        el.set("data-rest-transform", rest)
        scale_part = "" if expr.eye_scale == 1.0 else f" scale({expr.eye_scale})"
        el.set("transform", f"{rest}{scale_part}")

        eye_open_el = el.find(f'{{{SVG_NS}}}circle[@class="eye-open"]')
        eye_closed_el = el.find(f'{{{SVG_NS}}}path[@class="eye-closed"]')
        if eye_open_el is not None and eye_closed_el is not None:
            eye_open_el.set("display", "none" if expr.eyes_closed else "inline")
            eye_closed_el.set("display", "inline" if expr.eyes_closed else "none")

    mouth_group = elements_by_id[f"{character}-mouth"]
    mouth_path = mouth_group.find(f"{{{SVG_NS}}}path")
    if mouth_path is None:
        raise ValueError(f"{character}-mouth group has no child <path> to morph")
    mouth_path.set("d", MOUTH_SHAPES[expr.mouth])
