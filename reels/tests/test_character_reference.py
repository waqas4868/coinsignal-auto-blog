"""Phase 4 tests for reels/character_reference.py.

Run: python reels/tests/test_character_reference.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import character_reference as cr  # noqa: E402
from reel_story import load_original_prompt  # noqa: E402


def test_extract_stickman_base_prompt_matches_prompt_file() -> None:
    extracted = cr.extract_stickman_base_prompt()
    expected = (
        "Simple black stickman with a round head, clean smooth lines, minimalist style, "
        "expressive face, consistent proportions, medium line thickness, modern flat illustration, "
        "minimal white background, soft motivational emotional tone."
    )
    assert extracted == expected, extracted
    assert extracted in load_original_prompt(), "extracted text must be a real substring of the stored prompt, not a separate copy"
    print("PASS: Stickman Base Prompt extracted exactly, sourced from the live prompt file (not a hand-typed duplicate)")


def test_draw_reference_produces_correct_canvas_with_real_content() -> None:
    image = cr.draw_reference_stickman()
    assert image.size == (cr.PROPORTIONS["canvas_width"], cr.PROPORTIONS["canvas_height"])
    pixels = image.load()
    black_pixel_count = sum(
        1
        for x in range(0, image.width, 4)
        for y in range(0, image.height, 4)
        if pixels[x, y] != (255, 255, 255)
    )
    assert black_pixel_count > 200, f"expected substantial drawn content, found only {black_pixel_count} non-white sample pixels"
    print(f"PASS: reference image is the correct canvas size with real drawn content ({black_pixel_count} non-white sample pixels)")


def test_all_figure_geometry_stays_within_canvas_bounds() -> None:
    """Regression guard for the real bug this phase found visually: v1's
    first draw had the feet running off the bottom edge (canvas_height=720
    was too short for head+torso+leg length). Recomputes the same geometry
    draw_reference_stickman() uses and asserts every extremity is inside
    the canvas with margin, rather than trusting a "looks fine" review."""
    p = cr.PROPORTIONS
    head_top = p["head_center_y_px"] - p["head_radius_px"]
    hip_y = p["head_center_y_px"] + p["head_radius_px"] + p["torso_length_px"]
    foot_y = hip_y + p["leg_length_px"]
    assert head_top > 0, f"head top ({head_top}) runs off the top edge"
    assert foot_y < p["canvas_height"], f"feet ({foot_y}) run off the bottom edge (canvas_height={p['canvas_height']})"
    margin = p["canvas_height"] - foot_y
    assert margin >= 30, f"only {margin}px of bottom margin - too tight, matches the original bug's failure mode"
    print(f"PASS: figure geometry stays within canvas bounds with {margin}px bottom margin (regression guard for the v1 cut-off-feet bug)")


def test_save_load_round_trip_and_redesign_guard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp)
        meta_path = cr.save_character_reference("testchar", version=1, base_dir=base_dir)
        assert meta_path.exists()
        assert (base_dir / "testchar" / "reference.png").exists()

        loaded = cr.load_character_reference("testchar", base_dir=base_dir)
        assert loaded["name"] == "testchar"
        assert loaded["version"] == 1
        assert loaded["proportions"] == cr.PROPORTIONS
        assert loaded["style_reference"] == cr.extract_stickman_base_prompt()

        # Re-saving the identical version must be a no-op, not an error.
        cr.save_character_reference("testchar", version=1, base_dir=base_dir)

        errors = cr.verify_reference_matches_prompt("testchar", base_dir=base_dir)
        assert errors == [], errors
        print("PASS: save/load round-trips correctly; re-saving the identical version is a safe no-op")

        # Now simulate an accidental redesign under the SAME version number.
        original_proportions = cr.PROPORTIONS.copy()
        cr.PROPORTIONS["head_radius_px"] = 999
        try:
            raised = False
            try:
                cr.save_character_reference("testchar", version=1, base_dir=base_dir)
            except cr.CharacterRedesignError:
                raised = True
            assert raised, "saving different content under an existing version must raise CharacterRedesignError"
        finally:
            cr.PROPORTIONS["head_radius_px"] = original_proportions["head_radius_px"]
        print("PASS: attempting to redesign an existing version in place is rejected (CharacterRedesignError)")


def test_verify_reports_missing_reference() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        errors = cr.verify_reference_matches_prompt("nonexistent", base_dir=Path(tmp))
        assert errors and "no character.json" in errors[0]
    print("PASS: verify_reference_matches_prompt correctly reports a missing character")


if __name__ == "__main__":
    test_extract_stickman_base_prompt_matches_prompt_file()
    test_draw_reference_produces_correct_canvas_with_real_content()
    test_all_figure_geometry_stays_within_canvas_bounds()
    test_save_load_round_trip_and_redesign_guard()
    test_verify_reports_missing_reference()
    print("\nALL PHASE 4 TESTS PASSED")
