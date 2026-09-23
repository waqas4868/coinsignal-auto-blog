"""Phase 1 (Master Handoff 2026-09-23): deterministic 1-of-6 Reel article
selection, plus up to 2 other same-day articles reserved for Facebook text
posts (spec section 10).

Read-only against Blogger content already published by
scripts/coinsignal_runtime.py - this module never calls Blogger's write
endpoints and never regenerates article content, only reads the
COINSIGNAL_SOURCE_ID / COINSIGNAL_META_B64 marker each published post
already carries (see coinsignal_runtime.py's marker_html()/extract_marker()).

Selection is made once per PKT calendar day and persisted to
state/reel_daily_selection.json so a rerun on the same day returns the
identical selection instead of re-picking (spec rule: "do not change
selection on rerun") - critical because a later run may see MORE than 6
same-day articles (e.g. a manual workflow_dispatch), and re-selecting then
would silently change which article gets turned into today's Reel.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from common import (
    PKT,
    REEL_DAILY_SELECTION_PATH,
    iso_now,
    read_json,
    require_env,
    write_json,
)

BLOGGER_API = "https://www.googleapis.com/blogger/v3"
REQUIRED_ARTICLES_PER_DAY = 6
TEXT_POST_SLOTS = 2
REEL_DURATION_SECONDS = 138  # spec section 5: 2 min 18 sec


@dataclass(frozen=True)
class BloggerArticle:
    source_id: str
    url: str
    title: str
    summary: str
    published_at: str  # raw Blogger ISO 8601 timestamp


def _decode_meta(blob: str) -> dict[str, Any] | None:
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def extract_marker(content: str) -> tuple[str | None, dict[str, Any] | None]:
    sid_match = re.search(r"COINSIGNAL_SOURCE_ID:([A-Za-z0-9_-]+)", content or "")
    meta_match = re.search(r"COINSIGNAL_META_B64:([A-Za-z0-9_-]+)", content or "")
    sid = sid_match.group(1) if sid_match else None
    meta = _decode_meta(meta_match.group(1)) if meta_match else None
    return sid, meta


def articles_for_date(posts: list[dict[str, Any]], target_date: str, tz: ZoneInfo = PKT) -> list[BloggerArticle]:
    """Filters raw Blogger API post dicts to ones published on target_date
    (a "YYYY-MM-DD" string in tz's calendar), extracting each post's
    CoinSignal marker. Posts without a marker (e.g. manual/legacy posts)
    are excluded - they weren't produced by this pipeline and have no
    source_id to key a Reel job on.

    Returns in deterministic order: ascending publish time, tie-broken by
    source_id, so selection never depends on Blogger API result ordering.
    """
    out: list[BloggerArticle] = []
    for post in posts:
        published = str(post.get("published", "")).strip()
        if not published:
            continue
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(tz)
        except Exception:
            continue
        if dt.strftime("%Y-%m-%d") != target_date:
            continue
        sid, meta = extract_marker(str(post.get("content", "")))
        if not sid:
            continue
        out.append(
            BloggerArticle(
                source_id=sid,
                url=str(post.get("url", "")).strip(),
                title=str(post.get("title", "")).strip(),
                summary=str((meta or {}).get("summary", "")),
                published_at=published,
            )
        )
    out.sort(key=lambda a: (a.published_at, a.source_id))
    return out


def load_selection(target_date: str, state_path: Path = REEL_DAILY_SELECTION_PATH) -> dict[str, Any] | None:
    state = read_json(state_path, {})
    if isinstance(state, dict) and state.get("reel_date") == target_date and state.get("reel_status") == "SELECTED":
        return state
    return None


def select_for_date(
    posts: list[dict[str, Any]],
    target_date: str,
    *,
    tz: ZoneInfo = PKT,
    state_path: Path = REEL_DAILY_SELECTION_PATH,
) -> dict[str, Any]:
    """Idempotent selection entrypoint (spec section 10).

    If a SELECTED result for target_date is already persisted, returns it
    unchanged without looking at `posts` again. Otherwise requires at least
    REQUIRED_ARTICLES_PER_DAY same-day marked articles; below that it
    returns a NOT_READY result and persists nothing, so the next run (once
    more articles exist) can still select.
    """
    existing = load_selection(target_date, state_path)
    if existing is not None:
        return existing

    todays = articles_for_date(posts, target_date, tz)
    if len(todays) < REQUIRED_ARTICLES_PER_DAY:
        return {
            "reel_date": target_date,
            "reel_status": "NOT_READY",
            "articles_available": len(todays),
            "articles_required": REQUIRED_ARTICLES_PER_DAY,
        }

    reel_article = todays[0]
    text_articles = todays[1 : 1 + TEXT_POST_SLOTS]

    selection = {
        "reel_date": target_date,
        "reel_source_article_id": reel_article.source_id,
        "reel_source_article_url": reel_article.url,
        "reel_source_article_title": reel_article.title,
        "reel_status": "SELECTED",
        "reel_job_id": "",
        "reel_part": 0,
        "reel_duration_seconds": REEL_DURATION_SECONDS,
        "reel_created_at": iso_now(),
        "reel_completed_at": "",
        "reel_error": "",
        "text_post_article_ids": [a.source_id for a in text_articles],
        "text_post_article_urls": [a.url for a in text_articles],
    }
    write_json(state_path, selection)
    return selection


def refresh_google_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=45,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Google OAuth refresh response contained no access_token")
    return token


def fetch_recent_blogger_posts(blog_id: str, access_token: str, max_results: int = 20) -> list[dict[str, Any]]:
    response = requests.get(
        f"{BLOGGER_API}/blogs/{blog_id}/posts",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"maxResults": max_results, "fetchBodies": "true"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json().get("items", [])


def run_selection_for_today() -> dict[str, Any]:
    """Live entrypoint: fetches today's PKT-date Blogger posts and selects.

    Uses the same GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN + BLOGGER_BLOG_ID
    secrets already configured for the article pipeline and the existing
    Reels pipeline - read-only, no new secrets required for this phase.
    """
    from common import pkt_date

    env = require_env("BLOGGER_BLOG_ID", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    token = refresh_google_token(env["GOOGLE_CLIENT_ID"], env["GOOGLE_CLIENT_SECRET"], env["GOOGLE_REFRESH_TOKEN"])
    posts = fetch_recent_blogger_posts(env["BLOGGER_BLOG_ID"], token)
    return select_for_date(posts, pkt_date())


if __name__ == "__main__":
    result = run_selection_for_today()
    print(json.dumps(result, indent=2))
