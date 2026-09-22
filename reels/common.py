"""Shared helpers for the Reels pipeline.

Deliberately independent from scripts/coinsignal_runtime.py: no imports from
that module, no shared mutable state. The Reel engine must never be able to
affect Blogger publishing or the existing Facebook image-post worker, even
by accident through a shared helper - this file intentionally re-implements
the small set of primitives it needs instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat(timespec="seconds")


def read_json(path: Path | str, default: Any) -> Any:
    path = Path(path)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"STATE READ WARNING {path}: {exc}")
    return default


def write_json(path: Path | str, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def backoff_seconds(attempt: int) -> int:
    ladder = [3, 6, 12, 20, 30]
    return ladder[min(max(attempt - 1, 0), len(ladder) - 1)]


def require_env(*names: str) -> dict[str, str]:
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            missing.append(name)
        else:
            values[name] = value
    if missing:
        raise RuntimeError("Missing required GitHub secrets/variables: " + ", ".join(missing))
    return values


def response_text(response, limit: int = 2000) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return ""


class PipelineStop(RuntimeError):
    """Raised to stop the current Reel job only. Never propagates outside reel_worker."""


def run_subprocess(args: list[str], *, timeout: int, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
REEL_QUEUE_PATH = STATE_DIR / "reel_queue.json"
REEL_STATE_PATH = STATE_DIR / "reel_state.json"
WORK_DIR = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "coinsignal_reel_work"


def configure_git() -> None:
    subprocess.run(["git", "config", "user.name", "CoinSignal Reel Bot"], check=True, cwd=REPO_ROOT)
    subprocess.run(
        ["git", "config", "user.email", "coinsignal-reel-bot@users.noreply.github.com"],
        check=True,
        cwd=REPO_ROOT,
    )


def git_commit_push_state(paths: list[Path], message: str, branch: str, attempts: int = 5) -> None:
    """Commits/pushes ONLY the given paths (expected: files under state/).

    Deliberately scoped to explicit paths (never `git add -A`) so a bug here
    cannot accidentally stage or push unrelated article-pipeline files.
    """
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            configure_git()
            rel_paths = [str(p.resolve().relative_to(REPO_ROOT)) for p in paths]
            subprocess.run(["git", "add", "--", *rel_paths], check=True, cwd=REPO_ROOT)
            staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
            if staged.returncode == 0:
                print("Git (reels): no staged changes.")
                return
            subprocess.run(["git", "commit", "-m", message], check=True, cwd=REPO_ROOT)
            subprocess.run(["git", "fetch", "origin", branch], check=True, cwd=REPO_ROOT)
            rebase = subprocess.run(["git", "rebase", f"origin/{branch}"], cwd=REPO_ROOT, capture_output=True, text=True)
            if rebase.returncode != 0:
                subprocess.run(["git", "rebase", "--abort"], cwd=REPO_ROOT, capture_output=True, text=True)
                raise RuntimeError(f"git rebase failed: {(rebase.stderr or rebase.stdout)[:2000]}")
            subprocess.run(["git", "push", "origin", f"HEAD:{branch}"], check=True, cwd=REPO_ROOT)
            print("Git (reels) push succeeded:", message)
            return
        except Exception as exc:
            last_error = str(exc)
            print(f"Git (reels) push attempt {attempt}/{attempts} failed: {last_error}")
            if attempt < attempts:
                time.sleep(backoff_seconds(attempt))
    raise RuntimeError(f"Git (reels) push failed after {attempts} attempts: {last_error}")
