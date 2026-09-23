"""Phase 11 test for the small, surgical fix to scripts/coinsignal_runtime.py:
the Facebook text-post worker's daily budget must be 2 (not the old 3),
and it must skip whichever article the Reel pipeline selected for today -
see reel_daily_selection.json (Phase 1) and facebook_reel_queue.py
(Phase 9). This only exercises the new read-only lookup function; it does
NOT invoke publisher_main()/facebook_main() themselves, which require live
GitHub/Facebook secrets and network access.

Run: python scripts/tests/test_facebook_daily_mix.py
"""
from __future__ import annotations

import tempfile
import types
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "coinsignal_runtime.py"


def _load_module_without_executing_it() -> types.ModuleType:
    """coinsignal_runtime.py calls run_selected_mode() unconditionally at
    the bottom of the file (no `if __name__ == "__main__":` guard - by
    design, since this file is only ever meant to be run directly in CI,
    never imported). A plain `import` would therefore trigger the whole
    live pipeline. Instead, exec everything EXCEPT that trailing
    try/run_selected_mode() block in an isolated module namespace, so
    every function/constant is defined (including the two things this test
    actually needs: FACEBOOK_TEXT_DAILY_LIMIT and
    _todays_reel_source_article_id) without ever calling it."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    marker = "\ntry:\n    run_selected_mode()\n"
    if marker not in source:
        raise RuntimeError("expected trailing auto-run block not found - coinsignal_runtime.py's structure changed")
    safe_source = source.split(marker, 1)[0]

    module = types.ModuleType("coinsignal_runtime_under_test")
    module.__file__ = str(SCRIPT_PATH)
    exec(compile(safe_source, str(SCRIPT_PATH), "exec"), module.__dict__)
    return module


cr = _load_module_without_executing_it()


def _with_selection_file(content) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    if content is not None:
        import json

        tmp_path.write_text(json.dumps(content), encoding="utf-8")
    else:
        tmp_path.unlink()  # simulate "file does not exist"
    return tmp_path


def test_daily_text_limit_is_two_not_three() -> None:
    assert cr.FACEBOOK_TEXT_DAILY_LIMIT == 2, cr.FACEBOOK_TEXT_DAILY_LIMIT
    print("PASS: the text-post worker's own daily budget is 2 (its share of the 1 Reel + 2 text = 3 total daily mix)")


def test_returns_empty_when_selection_file_missing() -> None:
    path = _with_selection_file(None)
    original = cr.REEL_DAILY_SELECTION_FILE
    cr.REEL_DAILY_SELECTION_FILE = path
    try:
        assert cr._todays_reel_source_article_id() == ""
    finally:
        cr.REEL_DAILY_SELECTION_FILE = original
    print("PASS: no Reel-selection file yet -> no article is skipped (text posts proceed normally)")


def test_returns_id_when_selected_for_today() -> None:
    today = cr.pkt_date()
    path = _with_selection_file({"reel_date": today, "reel_status": "SELECTED", "reel_source_article_id": "abc123"})
    original = cr.REEL_DAILY_SELECTION_FILE
    cr.REEL_DAILY_SELECTION_FILE = path
    try:
        assert cr._todays_reel_source_article_id() == "abc123"
    finally:
        cr.REEL_DAILY_SELECTION_FILE = original
        path.unlink(missing_ok=True)
    print("PASS: today's real Reel selection is correctly surfaced for the worker to skip")


def test_ignores_selection_from_a_different_day() -> None:
    path = _with_selection_file({"reel_date": "2020-01-01", "reel_status": "SELECTED", "reel_source_article_id": "stale-id"})
    original = cr.REEL_DAILY_SELECTION_FILE
    cr.REEL_DAILY_SELECTION_FILE = path
    try:
        assert cr._todays_reel_source_article_id() == ""
    finally:
        cr.REEL_DAILY_SELECTION_FILE = original
        path.unlink(missing_ok=True)
    print("PASS: a stale selection from a different day is ignored, not treated as today's")


def test_ignores_not_yet_selected_status() -> None:
    today = cr.pkt_date()
    path = _with_selection_file({"reel_date": today, "reel_status": "NOT_READY"})
    original = cr.REEL_DAILY_SELECTION_FILE
    cr.REEL_DAILY_SELECTION_FILE = path
    try:
        assert cr._todays_reel_source_article_id() == ""
    finally:
        cr.REEL_DAILY_SELECTION_FILE = original
        path.unlink(missing_ok=True)
    print("PASS: a NOT_READY (fewer than 6 articles so far) record is correctly ignored")


if __name__ == "__main__":
    test_daily_text_limit_is_two_not_three()
    test_returns_empty_when_selection_file_missing()
    test_returns_id_when_selected_for_today()
    test_ignores_selection_from_a_different_day()
    test_ignores_not_yet_selected_status()
    print("\nALL PHASE 11 (daily-mix fix) TESTS PASSED")
