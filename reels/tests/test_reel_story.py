"""Phase 2 tests for reels/reel_story.py.

Run: python reels/tests/test_reel_story.py
"""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import reel_story as rs  # noqa: E402


# ---------------------------------------------------------------------------
# Prompt integrity
# ---------------------------------------------------------------------------

def test_original_prompt_verbatim_markers_survive() -> None:
    original = rs.load_original_prompt()
    # Exact typos from the user's spec that a "cleanup" pass would remove -
    # their presence proves the text was copied verbatim, not rewritten.
    must_contain = [
        "Cryoto news (Coin signal blogs)",
        "and if 1 minute the. Genarate single part and so on",
        '"Simple black stickman with a round head, clean smooth lines, minimalist style, '
        "expressive face, consistent proportions, medium line thickness, modern flat illustration, "
        'minimal white background, soft motivational emotional tone."',
        "⚠️ DO NOT ask anything else",
        "👉 Automatically decide tone based on topic",
    ]
    for snippet in must_contain:
        assert snippet in original, f"missing verbatim snippet: {snippet!r}"
    assert original.count(rs.STORY_FLOW_ANCHOR) == 1, "STORY_FLOW_ANCHOR must appear exactly once"
    print("PASS: original prompt contains exact verbatim text (typos and all), anchor unique")


def test_funny_addition_inserted_after_story_flow_before_output_format() -> None:
    original = rs.load_original_prompt()
    combined = rs.full_prompt_text()

    anchor_pos = combined.index(rs.STORY_FLOW_ANCHOR)
    addition_pos = combined.index("😂 FUNNY / MEME STYLE RULES (CRITICAL)")
    output_format_pos = combined.index("🎬 OUTPUT FORMAT (FOR EACH PART)")
    assert anchor_pos < addition_pos < output_format_pos, (
        f"addition must land after STORY FLOW and before OUTPUT FORMAT: "
        f"anchor={anchor_pos} addition={addition_pos} output_format={output_format_pos}"
    )

    # Everything before the anchor (ROLE, STEP 1, STEP 2, STRUCTURE RULES,
    # start of STORY FLOW) must be byte-identical to the original - the
    # addition must not have altered anything upstream of the insertion point.
    prefix_len = anchor_pos + len(rs.STORY_FLOW_ANCHOR)
    assert combined[:prefix_len] == original[:prefix_len], "text before/including the anchor was altered"
    print("PASS: funny/meme addition inserted exactly after STORY FLOW, before OUTPUT FORMAT; upstream text untouched")


def test_addition_not_duplicated_exact_length_accounting() -> None:
    original = rs.load_original_prompt()
    addition = rs.load_funny_addition()
    combined = rs.full_prompt_text()
    expected_len = len(original) + len(addition) + len("\n\n") + len("\n")
    assert len(combined) == expected_len, f"expected {expected_len} chars, got {len(combined)} (possible double-insert or corruption)"
    assert combined.count("😂 FUNNY / MEME STYLE RULES (CRITICAL)") == 1
    print("PASS: addition inserted exactly once, character count accounted for exactly")


# ---------------------------------------------------------------------------
# Article-derived source data (section 11)
# ---------------------------------------------------------------------------

REALISTIC_ARTICLE_HTML = """
<!-- COINSIGNAL_SOURCE_ID:abc123 --><!-- COINSIGNAL_META_B64:eyJmb28iOiJiYXIifQ== -->
<p style="text-align:center;"><img src="https://example.com/hero.png" alt="hero"/></p>
<p>Bitcoin surged past a key resistance level on Tuesday, catching traders off guard
as volume spiked across major exchanges in the span of a single hour.</p>
<h2>What happened</h2>
<p>The rally began shortly after a large options expiry, with open interest data
showing an unusually heavy concentration of near-the-money call contracts.
Analysts pointed to a combination of short covering and fresh spot demand as
the price broke through the level that had capped gains for the past three weeks.</p>
<h2>Key facts and context</h2>
<ul>
<li>Bitcoin gained 7.4% in the 24 hours following the breakout.</li>
<li>Exchange-tracked spot volume rose to its highest daily total in two months.</li>
<li>Open interest in BTC options increased by roughly 12% over the same window.</li>
</ul>
<h3>Why it matters</h3>
<p>The move reverses a multi-week consolidation pattern that had left traders
divided on near-term direction, and it comes as broader risk appetite across
equities has also been improving following a softer-than-expected inflation print.</p>
<h2>Key takeaways</h2>
<ul>
<li>The breakout was accompanied by real volume, not a thin-liquidity spike.</li>
<li>Options positioning suggests dealers may have been forced to hedge into the move.</li>
</ul>
<p><strong>Original source:</strong> <a href="https://cointelegraph.com/example">Example Source</a></p>
"""

THIN_ARTICLE_HTML = """
<!-- COINSIGNAL_SOURCE_ID:xyz789 -->
<p>Bitcoin went up a little today.</p>
<p><strong>Original source:</strong> <a href="https://cointelegraph.com/x">X</a></p>
"""


def test_article_facts_extracted_verbatim_in_order() -> None:
    facts = rs.extract_article_facts(REALISTIC_ARTICLE_HTML)
    expected = [
        "What happened",
        "Key facts and context",
        "Bitcoin gained 7.4% in the 24 hours following the breakout.",
        "Exchange-tracked spot volume rose to its highest daily total in two months.",
        "Open interest in BTC options increased by roughly 12% over the same window.",
        "Why it matters",
        "Key takeaways",
        "The breakout was accompanied by real volume, not a thin-liquidity spike.",
        "Options positioning suggests dealers may have been forced to hedge into the move.",
    ]
    assert facts == expected, facts
    print("PASS: article facts extracted verbatim (headings + list items), in document order, no invention")


def test_ready_status_for_realistic_article() -> None:
    data = rs.build_reel_source_data(
        article_title="Bitcoin Breaks Key Resistance",
        article_url="https://blogsdrip4u.blogspot.com/abc123.html",
        article_content_html=REALISTIC_ARTICLE_HTML,
    )
    assert data["status"] == "READY", data
    assert data["duration_seconds"] == 138, data
    assert data["duration_minutes"] == 2.3, data
    assert data["article_facts"], "facts must be non-empty for a structured article"
    assert "<" not in data["article_text"], "article_text must be plain text, no leftover HTML tags"
    assert "COINSIGNAL_SOURCE_ID" not in data["article_text"], "marker must be stripped from article_text"
    print("PASS: realistic structured article yields READY status with clean text + facts")


def test_needs_review_for_thin_article() -> None:
    data = rs.build_reel_source_data(
        article_title="Bitcoin Went Up",
        article_url="https://blogsdrip4u.blogspot.com/xyz789.html",
        article_content_html=THIN_ARTICLE_HTML,
    )
    assert data["status"] == "NEEDS_REVIEW", data
    print("PASS: thin/unstructured article correctly flagged NEEDS_REVIEW instead of proceeding")


# ---------------------------------------------------------------------------
# Story JSON schema (sections 12-13)
# ---------------------------------------------------------------------------

def _fake_selection() -> dict:
    return {
        "reel_source_article_id": "abc123",
        "reel_source_article_url": "https://blogsdrip4u.blogspot.com/abc123.html",
        "reel_source_article_title": "Bitcoin Breaks Key Resistance",
    }


def test_assemble_gemini_reel_request_contains_data_and_unmodified_prompt() -> None:
    source_data = rs.build_reel_source_data(
        article_title="Bitcoin Breaks Key Resistance",
        article_url="https://blogsdrip4u.blogspot.com/abc123.html",
        article_content_html=REALISTIC_ARTICLE_HTML,
    )
    request_text = rs.assemble_gemini_reel_request(source_data)
    assert '"topic": "Bitcoin Breaks Key Resistance"' in request_text
    assert "AUTOMATION LAYER INSTRUCTIONS" in request_text
    assert request_text.endswith(rs.full_prompt_text()), "the immutable prompt must appear unmodified at the end of the request"
    print("PASS: assembled Gemini request carries source data + auto-mode instructions, prompt itself unmodified")


def test_empty_story_skeleton_shape() -> None:
    doc = rs.empty_story_skeleton("REEL-2026-09-23-FUNNY-test", _fake_selection())
    assert len(doc["parts"]) == 3
    scene_counts = [len(p["scenes"]) for p in doc["parts"]]
    assert scene_counts == [10, 10, 3], scene_counts
    assert doc["parts"][2]["is_final_segment"] is True
    assert doc["parts"][0]["is_final_segment"] is False
    total_scenes = sum(scene_counts)
    assert total_scenes == 23, total_scenes
    for part in doc["parts"]:
        for scene in part["scenes"]:
            assert set(scene) == {"scene_number", "image_prompt", "motion_prompt", "voiceover"}
    print("PASS: story skeleton has 10+10+3=23 scene stubs across 3 parts, final segment flagged correctly")


def _filled_valid_doc() -> dict:
    doc = rs.empty_story_skeleton("REEL-2026-09-23-FUNNY-test", _fake_selection())
    for part in doc["parts"]:
        for scene in part["scenes"]:
            scene["image_prompt"] = "Use the same stickman character as before... [placeholder]"
            scene["motion_prompt"] = "Animate arms and head only, minimal smooth motion."
            scene["voiceover"] = "Bitcoin just broke through resistance - here's what that means."
    return doc


def test_validate_story_json_accepts_valid_and_rejects_broken() -> None:
    valid = _filled_valid_doc()
    errors = rs.validate_story_json(valid)
    assert errors == [], errors

    missing_key = copy.deepcopy(valid)
    del missing_key["style"]
    assert rs.validate_story_json(missing_key), "missing top-level key must produce errors"

    wrong_duration = copy.deepcopy(valid)
    wrong_duration["duration_seconds"] = 90
    assert any("duration_seconds" in e for e in rs.validate_story_json(wrong_duration))

    wrong_scene_count = copy.deepcopy(valid)
    wrong_scene_count["parts"][0]["scenes"] = wrong_scene_count["parts"][0]["scenes"][:5]
    assert any("expected 10 scenes" in e for e in rs.validate_story_json(wrong_scene_count))

    empty_voiceover = copy.deepcopy(valid)
    empty_voiceover["parts"][0]["scenes"][0]["voiceover"] = ""
    assert any("empty voiceover" in e for e in rs.validate_story_json(empty_voiceover))
    print("PASS: validator accepts a well-formed doc and catches 4 distinct kinds of schema violations")


def test_write_story_json_round_trip() -> None:
    doc = _filled_valid_doc()
    with tempfile.TemporaryDirectory() as tmp:
        out_path = rs.write_story_json(doc, "2026-09-23", base_dir=Path(tmp))
        assert out_path.exists()
        import json
        reloaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert reloaded == doc
    print("PASS: story JSON writes to reels/jobs/<date>/reel_story.json and round-trips exactly")


if __name__ == "__main__":
    test_original_prompt_verbatim_markers_survive()
    test_funny_addition_inserted_after_story_flow_before_output_format()
    test_addition_not_duplicated_exact_length_accounting()
    test_article_facts_extracted_verbatim_in_order()
    test_ready_status_for_realistic_article()
    test_needs_review_for_thin_article()
    test_assemble_gemini_reel_request_contains_data_and_unmodified_prompt()
    test_empty_story_skeleton_shape()
    test_validate_story_json_accepts_valid_and_rejects_broken()
    test_write_story_json_round_trip()
    print("\nALL PHASE 2 TESTS PASSED")
