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

## Facebook Reels (`reels/`, independent system)

A second, deliberately separate pipeline that turns already-published
CoinSignal articles into short vertical Reels and posts them to the same
Facebook Page. Runs on its own workflow
([`.github/workflows/coinsignal_reels.yml`](.github/workflows/coinsignal_reels.yml)),
own schedule (news Reel 17:17 Asia/Karachi, funny Reel 23:23 Asia/Karachi),
own concurrency group, and own state files under `state/`.

**Core rule: a Reel failure must never stop or damage Blogger publishing or
the article-pipeline Facebook worker above.** `reels/reel_worker.py` never
imports `scripts/coinsignal_runtime.py` and never writes any file outside
`state/reel_*.json` and its own temp work directory — see
[`reels/common.py`](reels/common.py)'s docstring for why that's structural,
not just a convention to remember.

Pipeline: pick an unreeled published article (read-only via the Blogger
API) → Gemini writes a script + 3-5 scenes → each scene's image comes from
[Pollinations](reels/image_generator_a.py) (free, watermarked unless
`POLLINATIONS_TOKEN` is set) with a [Hugging Face](reels/image_generator_b.py)
fallback → [Pillow/OpenCV](reels/image_quality_check.py) validates each
image → [Piper](reels/voice_generator.py) synthesizes local narration
(voice `en_US-ljspeech-medium` — deliberately not `lessac`, which is
restricted for commercial use) → [ffmpeg](reels/video_generator.py) builds
the vertical MP4 (zoom/pan + captions) → [ffprobe/OpenCV](reels/video_quality_check.py)
validates it against Facebook's Reel requirements → uploaded as a
throwaway public GitHub Release asset so Facebook's Reels API can fetch it
by URL (deleted again immediately after) →
[the 4-step Reels Publishing API](reels/facebook_reel_publisher.py) uploads
and publishes it.

Any technical failure (image generation exhausted, video fails validation)
stops that Reel job only — `state/reel_queue.json` records it as `failed`
and the workflow still exits successfully. An ambiguous outcome (e.g. a
timeout right after upload) is recorded as `unknown`; nothing auto-retries
an `unknown`/`failed` job, since retrying without first verifying against
Facebook's own status risks a duplicate post — same philosophy as the
article pipeline's Facebook `uncertain` state.

No new required secrets — it reuses the existing Facebook, Blogger, Google
OAuth, Gemini, and Hugging Face secrets. `POLLINATIONS_TOKEN` and
`NTFY_URL` are both optional.

## Local development

```bash
pip install -r requirements.txt
python -m py_compile scripts/coinsignal_runtime.py
python -m py_compile reels/*.py
```

Running `scripts/coinsignal_runtime.py` for real requires the same
environment variables the workflow sets from secrets (`GEMINI_API_KEY`,
`BLOGGER_BLOG_ID`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REFRESH_TOKEN`, `HF_TOKEN`, `FACEBOOK_PAGE_ID`,
`FACEBOOK_PAGE_ACCESS_TOKEN`) plus `RUN_MODE=publish|facebook|both`.
Running `reels/reel_worker.py` needs the same Blogger/Google/Gemini/HF/
Facebook variables plus `GITHUB_TOKEN` (a token with `repo` scope on this
repository) and `REEL_TYPE=news|funny`. `ffmpeg`/`ffprobe` must be on
`PATH`; Piper downloads its voice model on first run into
`reels/.piper_voices/`.
