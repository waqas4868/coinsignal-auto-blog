"""Phase 13 test for reels/run_daily_pipeline_entrypoint.py.

Regression guard for a real production bug found by actually running the
live workflow: run_daily_pipeline() originally only ever called
article_selector.load_selection() - a pure local file read - and NEVER
called the live Blogger-fetch-and-select function anywhere. Since nothing
else in the whole pipeline writes state/reel_daily_selection.json, the
Reel pipeline reported NOT_READY forever in production regardless of how
many real same-day articles existed (confirmed live: 6 real articles
existed, the workflow still reported NOT_READY). Fixed by having
run_daily_pipeline() call article_selector.run_selection_for_today() when
no selection is persisted yet.

This test can't fully exercise that live path locally (no Blogger/Google
OAuth credentials in this sandbox - same limitation every other live-API
path in this project has). What it DOES prove for real: with no selection
file present, the entrypoint now genuinely ATTEMPTS the live check rather
than silently reporting NOT_READY without ever looking - it fails on
missing credentials specifically, not silently succeeding as a no-op.

Run: python reels/tests/test_run_daily_pipeline_entrypoint.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENTRYPOINT = REPO_ROOT / "reels" / "run_daily_pipeline_entrypoint.py"


def test_entrypoint_attempts_live_selection_when_none_persisted() -> None:
    """Runs the REAL entrypoint as a subprocess (exactly how CI invokes
    it), with no Blogger/Google credentials in the environment. Before the
    fix, this silently printed NOT_READY and exited 0 without ever
    checking. After the fix, it must actually try the live selection and
    fail on the missing credentials - proof it's really checking now."""
    if (REPO_ROOT / "state" / "reel_daily_selection.json").exists():
        print("SKIP: a real Reel has already been selected for today in this repo - nothing to test here today")
        return

    import os

    env = {k: v for k, v in os.environ.items() if k not in ("BLOGGER_BLOG_ID", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")}
    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode != 0, (
        f"expected a failure from the live-selection attempt (no credentials configured), got exit 0\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Missing required GitHub secrets/variables" in result.stderr, result.stderr
    assert "NOT_READY" not in result.stdout, (
        "must not silently report NOT_READY without ever attempting the live check - that was the original bug"
    )
    print("PASS: with no selection persisted, the entrypoint genuinely attempts the live Blogger selection (fails informatively on missing credentials here, not a silent no-op)")


if __name__ == "__main__":
    test_entrypoint_attempts_live_selection_when_none_persisted()
    print("\nALL PHASE 13 (entrypoint) TESTS PASSED")
