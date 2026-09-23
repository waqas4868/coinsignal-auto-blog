"""Phase 4 (Master Handoff 2026-09-23): persistent character/reference
asset layer (spec section 15).

The Reel's visual language is fixed by the user's own immutable prompt
(reel_story.py, Phase 2): "Simple black stickman with a round head, clean
smooth lines, minimalist style, expressive face, consistent proportions,
medium line thickness, modern flat illustration, minimal white background,
soft motivational emotional tone." Every per-scene image generator call in
later phases must reuse this exact character - "Do not allow random
character redesign" - so this module:

1. Extracts the canonical Stickman Base Prompt string FROM the stored
   prompt file itself (never a second hand-typed copy) so the character
   metadata can never silently drift from what generation actually sends.
2. Draws one real, deterministic reference image (PIL, not a generative
   call) with named, numeric proportions - a diffusion/generative model has
   no guarantee of exact consistency across calls, which is precisely why
   a fixed, versioned reference exists for later phases to condition on.
3. Persists reference.png + character.json under reels/characters/<name>/,
   refusing to silently overwrite an existing version with different
   content - "every scene must use the same reference/version."
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from common import iso_now
from reel_story import load_original_prompt

CHARACTERS_DIR = Path(__file__).resolve().parent / "characters"

# Numeric proportions for the reference drawing. These are the actual
# values used to draw reference.png, not a description - any future
# consistency check compares against these numbers, not free text.
PROPORTIONS = {
    "canvas_width": 1280,
    "canvas_height": 780,
    "head_radius_px": 70,
    "head_center_y_px": 220,
    "torso_length_px": 220,
    "arm_length_px": 150,
    "leg_length_px": 200,
    "line_width_px": 6,
    "line_color": "#000000",
    "background_color": "#FFFFFF",
}

FACIAL_REFERENCE = (
    "Two small round dot eyes, no eyebrows, a single gently upward-curved "
    "mouth arc (soft neutral smile) - matches the prompt's own "
    '"soft motivational emotional tone" for the character\'s resting/neutral face. '
    "Per-scene expressions (shock, excitement, etc.) vary the mouth curve and add "
    "eyebrow marks but must keep the same head/eye proportions defined here."
)


def extract_stickman_base_prompt() -> str:
    """Pulls the exact Stickman Base Prompt quoted string out of the stored
    prompt file itself, so this module can never hold a stale/hand-typed
    copy that has drifted from what's actually sent to the image generator."""
    prompt_text = load_original_prompt()
    match = re.search(r'Stickman Base Prompt:\n"([^"]+)"', prompt_text)
    if not match:
        raise ValueError("Could not locate the canonical Stickman Base Prompt block in coinsignal_reel_prompt.txt")
    return match.group(1)


def draw_reference_stickman() -> Image.Image:
    """Deterministic reference drawing - same output every call, not a
    generative sample. Simple black line-art stickman: round head, single
    torso line, two arm lines, two leg lines, minimal dot-eyes + neutral
    mouth arc, flat white background - directly matching the prompt's own
    description line by line."""
    p = PROPORTIONS
    img = Image.new("RGB", (p["canvas_width"], p["canvas_height"]), p["background_color"])
    draw = ImageDraw.Draw(img)

    cx = p["canvas_width"] // 2
    head_cy = p["head_center_y_px"]
    r = p["head_radius_px"]
    lw = p["line_width_px"]
    color = p["line_color"]

    # Head: round, clean outline only (minimalist - no fill, no shading).
    draw.ellipse([cx - r, head_cy - r, cx + r, head_cy + r], outline=color, width=lw)

    # Face: two dot eyes + a soft neutral smile arc.
    eye_dx, eye_dy, eye_r = r * 0.35, -r * 0.1, max(3, lw)
    for sign in (-1, 1):
        ex, ey = cx + sign * eye_dx, head_cy + eye_dy
        draw.ellipse([ex - eye_r, ey - eye_r, ex + eye_r, ey + eye_r], fill=color)
    mouth_box = [cx - r * 0.4, head_cy + r * 0.05, cx + r * 0.4, head_cy + r * 0.55]
    draw.arc(mouth_box, start=20, end=160, fill=color, width=lw)

    # Torso: single vertical line from base of head to hips.
    neck_y = head_cy + r
    hip_y = neck_y + p["torso_length_px"]
    draw.line([(cx, neck_y), (cx, hip_y)], fill=color, width=lw)

    # Arms: relaxed neutral pose, slightly out and down from shoulder point.
    shoulder_y = neck_y + p["torso_length_px"] * 0.15
    arm_len = p["arm_length_px"]
    for sign in (-1, 1):
        hand_x = cx + sign * arm_len * 0.8
        hand_y = shoulder_y + arm_len * 0.6
        draw.line([(cx, shoulder_y), (hand_x, hand_y)], fill=color, width=lw)
        # Small rounded joint dot at the hand, for the "clean smooth lines" look.
        draw.ellipse([hand_x - lw, hand_y - lw, hand_x + lw, hand_y + lw], fill=color)

    # Legs: standing neutral stance, feet shoulder-width apart.
    leg_len = p["leg_length_px"]
    for sign in (-1, 1):
        foot_x = cx + sign * leg_len * 0.35
        foot_y = hip_y + leg_len
        draw.line([(cx, hip_y), (foot_x, foot_y)], fill=color, width=lw)

    return img


def build_character_metadata(name: str, version: int) -> dict[str, Any]:
    return {
        "name": name,
        "version": version,
        "style_reference": extract_stickman_base_prompt(),
        "proportions": PROPORTIONS,
        "facial_reference": FACIAL_REFERENCE,
        "reference_image": "reference.png",
        "created_at": iso_now(),
    }


class CharacterRedesignError(RuntimeError):
    """Raised when saving a character reference would silently change an
    already-persisted version's content - "do not allow random character
    redesign." Bump the version number for an intentional redesign."""


def character_dir(name: str, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else CHARACTERS_DIR
    return root / name


def load_character_reference(name: str, base_dir: Path | None = None) -> dict[str, Any] | None:
    meta_path = character_dir(name, base_dir) / "character.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def save_character_reference(name: str, version: int, base_dir: Path | None = None) -> Path:
    """Writes reference.png + character.json for `name`/`version`.

    Idempotent and redesign-safe: if this exact version already exists on
    disk, the newly-built metadata (everything except created_at/timestamp
    fields) must match byte-for-byte, or this raises CharacterRedesignError
    instead of silently overwriting a reference every later scene already
    depends on.
    """
    target_dir = character_dir(name, base_dir)
    meta_path = target_dir / "character.json"
    new_meta = build_character_metadata(name, version)

    if meta_path.exists():
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
        if existing.get("version") == version:
            comparable_existing = {k: v for k, v in existing.items() if k != "created_at"}
            comparable_new = {k: v for k, v in new_meta.items() if k != "created_at"}
            if comparable_existing != comparable_new:
                raise CharacterRedesignError(
                    f"character '{name}' version {version} already exists with different content; "
                    "bump the version number for an intentional redesign instead of overwriting it"
                )
            return meta_path  # identical - nothing to do, existing reference stays authoritative

    target_dir.mkdir(parents=True, exist_ok=True)
    image = draw_reference_stickman()
    image.save(target_dir / "reference.png")
    meta_path.write_text(json.dumps(new_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta_path


def verify_reference_matches_prompt(name: str, base_dir: Path | None = None) -> list[str]:
    """Consistency guard: the persisted style_reference must still match
    the live prompt file's Stickman Base Prompt exactly, and the reference
    image file must actually exist. Run this before any scene-generation
    call that will cite this character."""
    errors: list[str] = []
    meta = load_character_reference(name, base_dir)
    if meta is None:
        return [f"no character.json found for '{name}'"]

    live_prompt = extract_stickman_base_prompt()
    if meta.get("style_reference") != live_prompt:
        errors.append("character.json style_reference no longer matches the live Stickman Base Prompt")

    image_path = character_dir(name, base_dir) / str(meta.get("reference_image", "reference.png"))
    if not image_path.exists():
        errors.append(f"reference image missing: {image_path}")
    return errors


CHARACTER_NAME = "stickman"
# Deliberately NOT "alex"/"jake": those names are already used by the
# unrelated deterministic 2D rig engine's own characters at
# reels/characters/{alex,jake}/*.svg (a different, dormant system per this
# handoff's section 4 - "future/local fallback", not the active Reel
# pipeline). Reusing the name or the directory risked exactly what
# happened during this phase's own testing: deleting reels/characters/alex
# while iterating on a reference image collided with and clobbered the rig
# engine's alex.svg. This character therefore gets its own name and its
# own subdirectory so the two systems can never collide again.


if __name__ == "__main__":
    path = save_character_reference(CHARACTER_NAME, version=1)
    print("Saved character reference:", path)
    print("Verification errors:", verify_reference_matches_prompt(CHARACTER_NAME))
