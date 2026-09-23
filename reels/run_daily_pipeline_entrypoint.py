"""Phase 13 CI entrypoint: `python reels/run_daily_pipeline_entrypoint.py`.

Calls the real reel_pipeline_worker.run_daily_pipeline() with real
providers (no fakes - those only exist in tests) and persists the small,
cross-workflow-visible state files to git afterward.

Deliberately does NOT commit the large per-scene media (images/videos/
voice clips) or the final rendered MP4 to git - that would grow this
repo's history without bound, the same reasoning that's why the existing
Reels pipeline hosts its video as a temporary GitHub Release asset instead
of a git commit (see facebook_reel_publisher.py). Those files live in
reels/jobs/<date>/, which the workflow persists across runs via
actions/cache (not git) - see .github/workflows/coinsignal_stickman_reel.yml.

Only two files are committed to git, and only because something outside
this workflow needs to read them from a fresh checkout:
  - state/reel_daily_selection.json - scripts/coinsignal_runtime.py's
    facebook_main() reads this (read-only) to skip the Reel's source
    article for text posts and to enforce the daily mix.
  - reels/queue/facebook_reel_queue.json - the durable publish queue.

REEL_PUBLISH_LIVE is read from the environment exactly as
publish_facebook_reel.py already defines it - this script does not
override or default it, so the workflow's own configuration (or lack of
it) is the only thing that can ever turn on a live Facebook publish.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # reels/, for sibling imports

from common import REEL_DAILY_SELECTION_PATH, git_commit_push_state
import facebook_reel_queue as queue_mod
import reel_pipeline_worker as worker


def main() -> None:
    branch = os.environ.get("GITHUB_REF_NAME", "main").strip() or "main"

    result = worker.run_daily_pipeline(
        publish_kwargs={
            "page_id": os.environ.get("FACEBOOK_PAGE_ID", ""),
            "access_token": os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", ""),
            "graph_version": os.environ.get("FACEBOOK_GRAPH_VERSION", "v26.0"),
            "github_repo": os.environ.get("GITHUB_REPOSITORY", "waqas4868/coinsignal-auto-blog"),
            "github_token": os.environ.get("GITHUB_TOKEN", ""),
        },
    )

    print("Reel pipeline stage this run:", result.get("stage"))
    print("Detail:", result)

    paths_to_persist = [p for p in [REEL_DAILY_SELECTION_PATH, queue_mod.QUEUE_PATH] if p.exists()]
    if paths_to_persist:
        git_commit_push_state(
            paths_to_persist,
            f"CoinSignal stickman Reel pipeline: {result.get('stage')} ({result.get('job_id', result.get('date', ''))})",
            branch,
        )
    else:
        print("Nothing to persist yet (no selection/queue file exists).")


if __name__ == "__main__":
    main()
