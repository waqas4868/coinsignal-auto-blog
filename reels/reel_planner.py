"""Pick a source article (read-only, via the Blogger API) and turn it into a
Reel script + scene plan via Gemini.

Read-only reuse of the Blogger API is deliberate: it lets the Reel engine
source real, already-published CoinSignal stories without importing or
writing any file from the article pipeline's state (published_sources.json,
facebook_queue.json, etc.) - see common.py's docstring for why that
independence is structural here, not just convention.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import requests

from common import read_json, REEL_QUEUE_PATH

BLOGGER_API = "https://www.googleapis.com/blogger/v3"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"

NEWS_TARGET_SECONDS = (15, 30)
FUNNY_TARGET_SECONDS = (8, 18)


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


def _decode_meta(blob: str) -> dict[str, Any] | None:
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _extract_marker(content: str) -> tuple[str | None, dict[str, Any] | None]:
    sid_match = re.search(r"COINSIGNAL_SOURCE_ID:([A-Za-z0-9_-]+)", content or "")
    meta_match = re.search(r"COINSIGNAL_META_B64:([A-Za-z0-9_-]+)", content or "")
    sid = sid_match.group(1) if sid_match else None
    meta = _decode_meta(meta_match.group(1)) if meta_match else None
    return sid, meta


def pick_unreeled_article(blog_id: str, access_token: str, max_results: int = 20) -> dict[str, Any] | None:
    response = requests.get(
        f"{BLOGGER_API}/blogs/{blog_id}/posts",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"maxResults": max_results, "fetchBodies": "true"},
        timeout=60,
    )
    response.raise_for_status()
    posts = response.json().get("items", [])

    reel_queue = read_json(REEL_QUEUE_PATH, [])
    already_reeled = {
        item.get("article_source_id")
        for item in reel_queue
        if isinstance(item, dict) and item.get("state") not in {"failed"}
    }

    for post in posts:
        content = str(post.get("content", ""))
        sid, meta = _extract_marker(content)
        if not sid or sid in already_reeled:
            continue
        return {
            "source_id": sid,
            "title": post.get("title", ""),
            "url": post.get("url", ""),
            "summary": (meta or {}).get("summary", ""),
            "source_url": (meta or {}).get("source_url", ""),
        }
    return None


NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hook": {"type": "string"},
        "full_script": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image_prompt": {"type": "string"},
                    "caption": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                },
                "required": ["image_prompt", "caption", "duration_seconds"],
            },
        },
    },
    "required": ["title", "description", "hook", "full_script", "scenes"],
}


def _build_prompt(article: dict[str, Any], content_type: str) -> str:
    if content_type == "funny":
        target_lo, target_hi = FUNNY_TARGET_SECONDS
        structure = (
            "Structure: setup -> escalation -> twist -> punchline. Light, relatable "
            "crypto-community humor about market behavior, HODLing, gas fees, or "
            "trading psychology. Never mock a specific named individual or company; "
            "keep it observational, not mean-spirited. 2-4 scenes."
        )
    else:
        target_lo, target_hi = NEWS_TARGET_SECONDS
        structure = (
            "Structure: WHAT JUST HAPPENED (0-3s hook) -> what happened -> why it "
            "matters -> what to watch next. Professional newsroom tone, no "
            "clickbait, no invented facts/numbers beyond the source article. 3-5 scenes."
        )

    return f"""
You are writing a short-form vertical video (Facebook Reel) script for
CoinSignal based on an already-published article. Do not copy the article
verbatim - condense it into a punchy spoken narration plus a short on-screen
caption per scene.

ARTICLE TITLE:
{article['title']}

ARTICLE SUMMARY:
{article['summary']}

{structure}

Each scene needs:
- image_prompt: a vivid visual description for an AI image generator (no
  text/logos/watermarks in the image itself, vertical 9:16 composition).
- caption: a short (<=8 word) on-screen caption line for that scene.
- duration_seconds: how long that scene should hold on screen.

The sum of all scenes' duration_seconds should be approximately {target_lo}-{target_hi}
seconds total. full_script is the complete narration to be read aloud by
text-to-speech, matching the scene order.

Return ONLY JSON matching the requested schema.
""".strip()


def generate_reel_plan(article: dict[str, Any], content_type: str, gemini_api_key: str, gemini_model: str) -> dict[str, Any]:
    prompt = _build_prompt(article, content_type)
    url = f"{GEMINI_API}/models/{gemini_model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": NEWS_SCHEMA,
        },
    }
    response = requests.post(
        url,
        headers={"x-goog-api-key": gemini_api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    if not response.ok:
        raise RuntimeError(f"Gemini reel-plan request failed HTTP {response.status_code}: {response.text[:1000]}")

    payload_json = response.json()
    candidate = payload_json.get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = next((p.get("text") for p in parts if isinstance(p, dict) and p.get("text")), None)
    if not text:
        raise RuntimeError("Gemini returned no text candidate for the reel plan")

    plan = json.loads(text)
    scenes = plan.get("scenes", [])
    if not scenes:
        raise RuntimeError("Gemini returned a reel plan with no scenes")

    target_lo, target_hi = FUNNY_TARGET_SECONDS if content_type == "funny" else NEWS_TARGET_SECONDS
    total = sum(float(s.get("duration_seconds", 0)) for s in scenes) or 1.0
    target_mid = (target_lo + target_hi) / 2
    scale = target_mid / total
    for scene in scenes:
        scene["duration_seconds"] = round(max(2.0, float(scene.get("duration_seconds", 0)) * scale), 1)

    plan["scenes"] = scenes
    return plan
