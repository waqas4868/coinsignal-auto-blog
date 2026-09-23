"""Phase 1 tests for reels/article_selector.py.

Plain assert-based script (no new test-framework dependency) mirroring how
every other phase in this handoff has been verified: run it, read the
PASS/FAIL output, don't trust "it imports" as success. Exercises only the
pure selection logic (articles_for_date / select_for_date) - the live
Blogger-fetch wrapper (run_selection_for_today) needs real GitHub secrets
and is intentionally not covered here; it reuses the same
request/OAuth-refresh pattern already relied on by reel_planner.py.

Run: python reels/tests/test_article_selector.py
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
from article_selector import articles_for_date, load_selection, select_for_date  # noqa: E402


def _marker(sid: str, summary: str = "") -> str:
    meta = {"source_id": sid, "source_url": f"https://blogsdrip4u.blogspot.com/{sid}", "summary": summary, "image_url": ""}
    blob = base64.urlsafe_b64encode(json.dumps(meta).encode("utf-8")).decode("ascii")
    return f"<!-- COINSIGNAL_SOURCE_ID:{sid} --><!-- COINSIGNAL_META_B64:{blob} --><p>body</p>"


def _post(sid: str, published: str, title: str | None = None) -> dict:
    return {
        "url": f"https://blogsdrip4u.blogspot.com/{sid}.html",
        "title": title or f"Article {sid}",
        "published": published,
        "content": _marker(sid, summary=f"summary for {sid}"),
    }


def test_excludes_unmarked_and_cross_day_posts() -> None:
    posts = [
        _post("a1", "2026-09-23T02:00:00+05:00"),  # PKT day 23
        _post("a2", "2026-09-22T23:59:00+05:00"),  # PKT day 22 - different day
        {"url": "x", "title": "manual post", "published": "2026-09-23T03:00:00+05:00", "content": "<p>no marker</p>"},
    ]
    result = articles_for_date(posts, "2026-09-23")
    assert [a.source_id for a in result] == ["a1"], result
    print("PASS: excludes unmarked and cross-day posts")


def test_not_ready_below_six() -> None:
    posts = [_post(f"b{i}", f"2026-09-23T0{i}:00:00+05:00") for i in range(1, 5)]  # 4 articles
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "reel_daily_selection.json"
        result = select_for_date(posts, "2026-09-23", state_path=state_path)
        assert result["reel_status"] == "NOT_READY", result
        assert result["articles_available"] == 4, result
        assert not state_path.exists(), "NOT_READY must not persist state (so a later run can still select)"
    print("PASS: below-6 same-day articles yields NOT_READY and persists nothing")


def test_selects_earliest_as_reel_and_next_two_as_text() -> None:
    # 6 same-day articles, published out of order to prove sorting is by time, not input order.
    posts = [
        _post("c3", "2026-09-23T09:00:00+05:00"),
        _post("c1", "2026-09-23T02:00:00+05:00"),
        _post("c6", "2026-09-23T18:00:00+05:00"),
        _post("c2", "2026-09-23T06:00:00+05:00"),
        _post("c5", "2026-09-23T14:00:00+05:00"),
        _post("c4", "2026-09-23T10:00:00+05:00"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "reel_daily_selection.json"
        result = select_for_date(posts, "2026-09-23", state_path=state_path)
        assert result["reel_status"] == "SELECTED", result
        assert result["reel_source_article_id"] == "c1", result  # earliest published
        assert result["text_post_article_ids"] == ["c2", "c3"], result  # next two chronologically
        assert result["reel_duration_seconds"] == 138, result
        assert "COINSIGNAL_SOURCE_ID:c1" in result["reel_source_article_content_html"], result  # Phase 11: full content is persisted, not just id/url/title
        assert state_path.exists(), "SELECTED must persist state"
    print("PASS: selects earliest article for Reel, next two chronologically for text posts")


def test_rerun_idempotent_even_if_more_articles_appear() -> None:
    six_posts = [_post(f"d{i}", f"2026-09-23T0{i}:00:00+05:00") for i in range(1, 7)]
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "reel_daily_selection.json"
        first = select_for_date(six_posts, "2026-09-23", state_path=state_path)
        assert first["reel_source_article_id"] == "d1", first

        # Simulate a 7th (and earlier-timestamped!) article showing up before the rerun -
        # if selection were re-run naively, "d0" would now win as earliest.
        seven_posts = six_posts + [_post("d0", "2026-09-23T00:30:00+05:00")]
        second = select_for_date(seven_posts, "2026-09-23", state_path=state_path)
        assert second == first, f"rerun changed selection: {first} -> {second}"

        reloaded = load_selection("2026-09-23", state_path=state_path)
        assert reloaded == first, reloaded
    print("PASS: rerun on the same day returns the identical persisted selection")


def test_marker_extraction_survives_nested_article_html() -> None:
    posts = [_post(f"e{i}", f"2026-09-23T0{i}:00:00+05:00") for i in range(1, 7)]
    # Confirm the marker survives being embedded ahead of a realistic-length article body.
    posts[0]["content"] = posts[0]["content"] + ("<p>filler paragraph.</p>" * 50)
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "reel_daily_selection.json"
        result = select_for_date(posts, "2026-09-23", state_path=state_path)
        assert result["reel_status"] == "SELECTED", result
    print("PASS: marker extraction works with realistic surrounding article HTML")


if __name__ == "__main__":
    test_excludes_unmarked_and_cross_day_posts()
    test_not_ready_below_six()
    test_selects_earliest_as_reel_and_next_two_as_text()
    test_rerun_idempotent_even_if_more_articles_appear()
    test_marker_extraction_survives_nested_article_html()
    print("\nALL PHASE 1 TESTS PASSED")
