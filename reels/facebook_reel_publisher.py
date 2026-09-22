"""Temporary GitHub Release hosting + the 4-step Facebook Reels Publishing API.

API flow verified directly against Meta's own Postman collection
(fbsamples/Facebook-Reels-Publishing-API-Postman-Collection) rather than
secondhand notes:
    1. POST /{page_id}/video_reels?upload_phase=start -> video_id
    2. POST https://rupload.facebook.com/video-upload/{ver}/{video_id}
       header Authorization: OAuth {token}, header file_url: <public MP4 URL>
    3. GET /{video_id}?fields=status  (poll)
    4. POST /{page_id}/video_reels?upload_phase=finish&video_id=...&video_state=PUBLISHED

Video hosting uses a GitHub Release asset (non-draft, so its
browser_download_url is publicly fetchable without auth - required, since
Meta's servers fetch file_url anonymously) rather than committing the MP4 to
git, per the user's spec section 23 (don't permanently keep every MP4 in
git). The release + its git tag are deleted again right after Facebook
finishes fetching it - see cleanup_temp_release().
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from common import response_text

GITHUB_API = "https://api.github.com"


def upload_temp_release_asset(repo: str, github_token: str, video_path: Path, tag: str) -> dict[str, Any]:
    """Creates a throwaway public GitHub Release and uploads video_path as its asset.

    Returns {"release_id": int, "asset_id": int, "download_url": str, "tag": str}.
    """
    headers = {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"}

    create_resp = requests.post(
        f"{GITHUB_API}/repos/{repo}/releases",
        headers=headers,
        json={
            "tag_name": tag,
            "name": f"[temp] Reel video asset {tag}",
            "body": "Temporary asset for Facebook Reels hosted-upload. Auto-deleted after publish.",
            "draft": False,
            "prerelease": True,
        },
        timeout=30,
    )
    if not create_resp.ok:
        raise RuntimeError(f"Failed to create temp release: HTTP {create_resp.status_code}: {response_text(create_resp)}")
    release = create_resp.json()
    release_id = release["id"]

    upload_url_template = release["upload_url"].split("{")[0]
    with open(video_path, "rb") as f:
        upload_resp = requests.post(
            upload_url_template,
            headers={**headers, "Content-Type": "video/mp4"},
            params={"name": video_path.name},
            data=f,
            timeout=180,
        )
    if not upload_resp.ok:
        raise RuntimeError(f"Failed to upload release asset: HTTP {upload_resp.status_code}: {response_text(upload_resp)}")
    asset = upload_resp.json()

    return {
        "release_id": release_id,
        "asset_id": asset["id"],
        "download_url": asset["browser_download_url"],
        "tag": tag,
    }


def cleanup_temp_release(repo: str, github_token: str, release_id: int, tag: str) -> None:
    headers = {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"}
    try:
        requests.delete(f"{GITHUB_API}/repos/{repo}/releases/{release_id}", headers=headers, timeout=30)
    except Exception as exc:
        print(f"Warning: failed to delete temp release {release_id}: {exc}")
    try:
        requests.delete(f"{GITHUB_API}/repos/{repo}/git/refs/tags/{tag}", headers=headers, timeout=30)
    except Exception as exc:
        print(f"Warning: failed to delete temp tag {tag}: {exc}")


def resolve_page_access_token(page_id: str, configured_token: str, graph_version: str) -> str:
    """Reels requires an actual Page access token (Meta's docs: "from a user who
    can perform the CREATE_CONTENT task on the Page"), not a raw User access
    token. Re-implemented independently here (not imported) rather than reusing
    scripts/coinsignal_runtime.py's resolve_page_access_token() - same reasoning
    as everywhere else in reels/: no import coupling to the article pipeline.

    If configured_token is a User token, exchange it for the matching Page's
    token via /me/accounts. If that call fails in a way suggesting the
    configured token is already a Page token, use it as-is.
    """
    try:
        response = requests.get(
            f"https://graph.facebook.com/{graph_version}/me/accounts",
            params={"fields": "id,name,access_token,tasks", "limit": 100, "access_token": configured_token},
            timeout=45,
        )
    except requests.RequestException as exc:
        print(f"Reels: /me/accounts network error, using configured token as-is: {exc}")
        return configured_token

    if not response.ok:
        print(f"Reels: /me/accounts failed ({response.status_code}), assuming configured token is already a Page token")
        return configured_token

    pages = response.json().get("data", [])
    target = next((p for p in pages if str(p.get("id", "")).strip() == str(page_id).strip()), None)
    if target and str(target.get("access_token", "")).strip():
        tasks = target.get("tasks") or []
        print(f"Reels: resolved Page token for {target.get('name')} (tasks: {', '.join(tasks) if tasks else 'none listed'})")
        if isinstance(tasks, list) and "CREATE_CONTENT" not in tasks:
            print("Reels WARNING: this Page role does not list CREATE_CONTENT - video publish will likely fail")
        return str(target["access_token"]).strip()

    print(f"Reels: /me/accounts succeeded but did not include page {page_id}; using configured token as-is")
    return configured_token


def _reels_url(page_id: str, graph_version: str) -> str:
    return f"https://graph.facebook.com/{graph_version}/{page_id}/video_reels"


def start_upload_session(page_id: str, access_token: str, graph_version: str) -> str:
    response = requests.post(
        _reels_url(page_id, graph_version),
        params={"access_token": access_token, "upload_phase": "start"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Reels start failed HTTP {response.status_code}: {response_text(response)}")
    video_id = response.json().get("video_id")
    if not video_id:
        raise RuntimeError(f"Reels start response contained no video_id: {response_text(response)}")
    return str(video_id)


def upload_hosted_video(video_id: str, access_token: str, graph_version: str, file_url: str) -> None:
    response = requests.post(
        f"https://rupload.facebook.com/video-upload/{graph_version}/{video_id}",
        headers={"Authorization": f"OAuth {access_token}", "file_url": file_url},
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f"Reels hosted upload failed HTTP {response.status_code}: {response_text(response)}")


def poll_status(video_id: str, access_token: str, graph_version: str) -> dict[str, Any]:
    response = requests.get(
        f"https://graph.facebook.com/{graph_version}/{video_id}",
        params={"fields": "status", "access_token": access_token},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Reels status check failed HTTP {response.status_code}: {response_text(response)}")
    return response.json()


def wait_for_upload_complete(video_id: str, access_token: str, graph_version: str, *, timeout_seconds: int = 600) -> str:
    """Polls until the upload's video_status leaves 'processing'/'in_progress'. Returns final phase string."""
    deadline = time.time() + timeout_seconds
    last_phase = "unknown"
    while time.time() < deadline:
        info = poll_status(video_id, access_token, graph_version)
        status = info.get("status", {})
        phase = str(status.get("video_status") or status.get("uploading_phase", {}).get("status") or "unknown")
        last_phase = phase
        if phase.lower() in {"ready", "complete", "completed", "finished", "error", "failed"}:
            return phase
        time.sleep(10)
    return last_phase


def finish_and_publish(
    page_id: str, video_id: str, access_token: str, graph_version: str, *, title: str, description: str
) -> dict[str, Any]:
    response = requests.post(
        _reels_url(page_id, graph_version),
        params={
            "access_token": access_token,
            "video_id": video_id,
            "upload_phase": "finish",
            "video_state": "PUBLISHED",
            "title": title[:255],
            "description": description[:2000],
        },
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Reels finish/publish failed HTTP {response.status_code}: {response_text(response)}")
    return response.json()
