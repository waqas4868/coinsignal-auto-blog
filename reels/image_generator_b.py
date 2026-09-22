"""Provider B: Hugging Face FLUX.1-schnell (fallback Reel scene image generator).

Only called when Pollinations fails or its output fails quality validation.
Mirrors the InferenceClient pattern already used for Blogger hero images in
scripts/coinsignal_runtime.py, but not imported from there (see common.py
docstring) and adapted to a vertical 9:16 composition for Reels.
"""
from __future__ import annotations

from pathlib import Path

from huggingface_hub import InferenceClient

IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"


def generate_image(prompt: str, output_path: Path, hf_token: str, *, width: int = 1080, height: int = 1920) -> bool:
    if not hf_token:
        print("HF_TOKEN not configured; image fallback unavailable")
        return False
    try:
        client = InferenceClient(provider="auto", api_key=hf_token, timeout=120)
        full_prompt = (
            "Professional editorial cryptocurrency news visual, realistic financial "
            "newsroom aesthetic, modern blockchain/markets atmosphere, cinematic but "
            "credible lighting, vertical 9:16 composition, no text, no logos, no "
            "watermark. Scene: " + prompt
        )
        image = client.text_to_image(prompt=full_prompt, model=IMAGE_MODEL, width=width, height=height)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception as exc:
        print("HF fallback image generation failed:", exc)
        return False
