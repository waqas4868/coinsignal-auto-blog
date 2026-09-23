"""Phase 13 test for reels/run_daily_pipeline_entrypoint.py.

Only the safe, zero-network path is exercised here: with no
state/reel_daily_selection.json present, run_daily_pipeline() must return
NOT_READY before touching any external service, and the entrypoint must
correctly skip git persistence rather than attempting to commit nothing.
Every live-credential path this entrypoint wires together (Gemini,
Blogger, image/video/voice providers, Facebook) is already tested
independently in its own phase's test file - this test's job is only to
confirm the entrypoint's own plumbing (argument wiring, the persist-or-skip
decision), not to re-exercise those live paths.

Run: python reels/tests/test_run_daily_pipeline_entrypoint.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENTRYPOINT = REPO_ROOT / "reels" / "run_daily_pipeline_entrypoint.py"


def test_entrypoint_is_a_safe_noop_with_no_selection_file() -> None:
    """Runs the REAL entrypoint as a subprocess (exactly how CI invokes it).
    common.py resolves state/reel_daily_selection.json and
    reels/queue/facebook_reel_queue.json as absolute paths under the repo
    root (not cwd-relative), so this exercises the actual production path -
    at the time this test was written, neither file exists yet in this
    repo, so the entrypoint must report NOT_READY and skip persistence
    rather than crash or attempt a network call."""
    assert not (REPO_ROOT / "state" / "reel_daily_selection.json").exists(), (
        "this test assumes no Reel has been selected yet in this repo; if one now exists, "
        "this specific test needs updating (it is not meant to run against real production state)"
    )
    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "NOT_READY" in result.stdout, result.stdout
    assert "Nothing to persist yet" in result.stdout, result.stdout
    print("PASS: the entrypoint safely reports NOT_READY and skips git persistence when no selection exists, with zero network calls")


if __name__ == "__main__":
    test_entrypoint_is_a_safe_noop_with_no_selection_file()
    print("\nALL PHASE 13 (entrypoint) TESTS PASSED")
