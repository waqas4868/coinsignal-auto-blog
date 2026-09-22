# CoinSignal Auto Blog

Automated pipeline that reads the Cointelegraph RSS feed, writes an original crypto
news article with Gemini, generates a hero image with Hugging Face
(`black-forest-labs/FLUX.1-schnell`), publishes the article to Blogger, and
cross-posts a link to a Facebook Page. It runs unattended on a GitHub Actions
schedule; there is no server or database outside this repo.

## How it runs

All logic lives in [`scripts/coinsignal_runtime.py`](scripts/coinsignal_runtime.py).
[`.github/workflows/blogger.yml`](.github/workflows/blogger.yml) only handles CI
plumbing (checkout, pinned dependency install, mode selection, and invoking the
script) — it does not contain application logic.

Two modes, selected by `RUN_MODE`:

- **`publish`** — picks the next un-published RSS story, generates the article +
  image, and posts it to Blogger. Runs 6x/day (02:00/06:00/10:00/14:00/18:00/22:00
  PKT).
- **`facebook`** — posts up to 3 queued articles/day to the configured Facebook
  Page. Runs 3x/day (03:00/11:00/19:00 PKT).

Both can also be triggered manually via `workflow_dispatch` (including a `both`
mode for manual runs only).

## State files (committed to the repo root)

The pipeline uses git as its database — these JSON files are read and rewritten by
every run and committed back:

- `published_sources.json` — normalized source URLs + title fingerprints already
  published, the primary de-duplication defense (Blogger's own post list is only
  checked as a secondary safety net, paginated up to ~300 recent posts).
- `facebook_queue.json` — per-article Facebook posting queue and status
  (`pending`/`posting`/`posted`/`retry`/`uncertain`/`failed`).
- `facebook_state.json` — daily Facebook post counter/history (resets per PKT day).
- `coinsignal_quota_state.json` — per-external-service pause state (Gemini,
  Blogger, RSS, image generation, Facebook), so a quota/rate-limit event on one
  service pauses only that service on later runs instead of retrying blindly.
- `publisher_inflight.json` — **not** committed to git (see `.gitignore`). It's a
  crash-recovery checkpoint for an in-progress publish (article generated but not
  yet posted to Blogger), persisted across runs via a GitHub Actions cache
  (`actions/cache`) instead of git, so a Blogger failure doesn't force
  regenerating the article (and re-spending Gemini/image quota) on the next run.

If a Facebook queue item ends up in `uncertain` or `failed` status, nothing
retries it automatically (to avoid risking a duplicate Page post) — it needs a
human to look at `last_error` and requeue it. The end-of-run job summary in the
Actions UI lists any such items, along with any currently paused services.

## Known limitation: `generated_images/` only grows

Hero images are committed to `generated_images/` and served to Blogger via
`raw.githubusercontent.com` URLs, because already-published Blogger posts embed
that URL directly — **deleting an old image would break a live, already-published
article**. There's currently no cleanup path for this; the repo (and `.git`
history) will keep growing indefinitely. If that becomes a problem, the fix is
migrating image hosting to an external object store with stable URLs (e.g. S3,
Cloudflare R2), decoupled from git history size — that's a separate infra change,
not something this pipeline does today.

## Local development

```bash
pip install -r requirements.txt
python -m py_compile scripts/coinsignal_runtime.py
```

Running the script for real requires the same environment variables the workflow
sets from secrets (`GEMINI_API_KEY`, `BLOGGER_BLOG_ID`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `HF_TOKEN`, `FACEBOOK_PAGE_ID`,
`FACEBOOK_PAGE_ACCESS_TOKEN`) plus `RUN_MODE=publish|facebook|both`.
