# Cartoon Reel animation - prototype status

**Not wired into any scheduled/production workflow.** This is test-phase code
only, per the staged process (TEST 1 -> TEST 2 -> TEST 3 -> TEST 4 -> only
then production integration). `reel_worker.py` / `coinsignal_reels.yml` are
unaffected and unaware this package exists.

`COST_MODE=FREE_ONLY` is enforced in `config.py` - no paid backend exists or
is selectable.

## Backend selected: `FreeTestBackend` (`free_test_backend.py`)

Wan2.2 14B Image-to-Video (Lightning LoRA), via the free
`zerogpu-aoti/wan2-2-fp8da-aoti-faster` Hugging Face Space, called through
`gradio_client`. $0 cost, no login required for the base anonymous quota.

Selected after actually checking (not assuming) the two candidates from the
original brief:
- **HunyuanVideo-Avatar** - no working free public environment. All 3
  community Hugging Face Space mirrors checked were broken at evaluation
  time (build error / storage limit exceeded / scheduling failure).
- **LongCat-Video-Avatar** - live and free, but talking-head/portrait only;
  fails the full-body motion requirement.

### What it does NOT support (reported, not faked)
- No native audio-driven lip sync.
- No native multi-character scene coordination -
  `generate_multi_character_scene`/`generate_dialogue_scene` raise
  `NotImplementedError` rather than faking interaction.

### Free quota mechanics (reverse-engineered from the Space's own `app.py`)

```
frames = 1 + clip(round(duration_seconds * 16), 8, 80)
width, height = resize_image(reference_image)   # always resizes to hit 832 on
                                                 # the long side; input pixel
                                                 # size doesn't matter, aspect
                                                 # ratio does
factor = frames * width * height / (81 * 832 * 624)
requested_seconds = 10 + steps * (15 * factor ** 1.5)
```

`free_test_backend._predict_gpu_seconds()` reimplements this exactly, so
`FreeTestBackend.estimate_cost()` predicts the real ZeroGPU request before
spending an attempt on it. Anonymous users get roughly 79-180s/day
(observed, not documented precisely by HF) before a 24h reset; the error
message itself reports the exact reset countdown, which
`generate_single_character_scene()` parses into `QuotaExceeded.reset_hint`.

An extreme-portrait reference image (`test_assets/alex_reference.png`,
480x1024) resizes to the cheapest fixed target (480x832) rather than a
near-square image's more expensive 624x832 - roughly halves the predicted
cost for the same steps/duration.

## Test status

- **TEST 1 (single character, real continuous motion)**: PASSED. Verified
  via `ffprobe` (real multi-frame video, not a static loop) and by visually
  inspecting extracted frames.
- **TEST 2 (character consistency across 3 independent generations)**:
  PASSED. Same reference image, 3 separate `generate_video` calls (shock,
  walk, point) - identical head shape/proportions/line-art style across all
  three, only the prompted action changed.
- **TEST 3 (two-character dialogue)**: not yet attempted. `FreeTestBackend`
  is known in advance not to support this natively (see capabilities()) -
  will need to be built as two separate single-character generations
  composed/intercut, and reported as that limitation rather than presented
  as genuine shared-scene interaction.
- **TEST 4 (mini Reel)**: not started.

## Known operational quirks (observed, not assumed)

- The Space occasionally returns a transient `403` on downloading the
  result file for a call placed too soon after a prior one; waiting
  ~40s and retrying resolved it without any code change.
- `steps`/`duration_seconds` above the Space's own defaults (6 steps, 3.5s)
  can request more GPU-time than the declared per-call maximum entirely
  aside from the daily quota - keep both near the low end unless a specific
  test needs more.
