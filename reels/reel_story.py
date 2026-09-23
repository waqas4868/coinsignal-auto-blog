"""Phase 2 (Master Handoff 2026-09-23): exact Reel prompt integration +
structured story JSON contract.

Three responsibilities, matching spec sections 8-13:

1. Load the user's ORIGINAL stickman prompt verbatim from
   prompt/coinsignal_reel_prompt.txt and insert the sanctioned FUNNY/MEME
   addition (prompt/funny_meme_addition.txt) after its STORY FLOW section -
   the only permitted change to "the prompt" itself (section 9).
2. Build the REEL SOURCE DATA object (section 11) from a selected article's
   real Blogger content - injected as DATA alongside the prompt, never
   merged into or rewriting the prompt text. Extracted facts are verbatim
   substrings of the article (headings + list items), never invented; a
   too-thin article is marked NEEDS_REVIEW instead of fabricating facts.
3. Define + validate the reel_story.json schema (sections 12-13): 2 full
   Parts (~60s/10 scenes each, matching the prompt's own "1 minute = 1
   Part, 10 scenes") plus a distinct 18-second final continuation segment
   (18s / ~6s-per-scene, from the prompt's own scene-timing assumption, = 3
   scenes) to reach the spec's 138-second target without inventing a new
   Part concept the original prompt doesn't have.

This module does not call Gemini. It builds the exact request text that
would be sent, and validates/stores whatever comes back - the live call
reuses the same GEMINI_API_KEY already configured for the article pipeline
and reel_planner.py, and (like Phase 1's live Blogger fetch) is not
locally testable without those credentials.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from common import iso_now, write_json

PROMPT_DIR = Path(__file__).resolve().parent / "prompt"
ORIGINAL_PROMPT_PATH = PROMPT_DIR / "coinsignal_reel_prompt.txt"
FUNNY_ADDITION_PATH = PROMPT_DIR / "funny_meme_addition.txt"

# The exact anchor the addition must be inserted after (spec section 9:
# "Add this section to the prompt after STORY FLOW section"). This literal
# line closes the prompt's STORY FLOW section, immediately before its
# "OUTPUT FORMAT" section begins.
STORY_FLOW_ANCHOR = "⚠️ Each part must feel NEW (no repetition)\n\n\n---"

REEL_STYLE = "FUNNY_MEME_STORYTELLING"
REEL_DURATION_MINUTES = 2.3
REEL_DURATION_SECONDS = 138

# Section 13: Part 1 (60s/10 scenes) + Part 2 (60s/10 scenes) + a final 18s
# continuation. 18s isn't a full "1 minute = 1 Part" per the original
# prompt's own structure rule, so it is NOT modeled as a 3rd full Part -
# it's sized from the prompt's own "~6 sec each" scene-timing assumption
# (18 / 6 = 3 scenes), kept distinct via is_final_segment.
PART_PLAN = [
    {"part_number": 1, "target_seconds": 60, "scene_count": 10, "is_final_segment": False},
    {"part_number": 2, "target_seconds": 60, "scene_count": 10, "is_final_segment": False},
    {"part_number": 3, "target_seconds": 18, "scene_count": 3, "is_final_segment": True},
]

MIN_ARTICLE_WORDS = 150  # below this, section 11's "not strong enough" -> NEEDS_REVIEW
MAX_FACTS = 15


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_original_prompt() -> str:
    return _read_text(ORIGINAL_PROMPT_PATH)


def load_funny_addition() -> str:
    return _read_text(FUNNY_ADDITION_PATH)


def full_prompt_text() -> str:
    """Original prompt + funny/meme addition inserted after STORY FLOW.

    Raises if the anchor isn't found verbatim, rather than silently
    appending at the wrong place or falling back to appending at the end -
    an incorrect insertion point would be a silent violation of "the only
    permitted addition" rule.
    """
    original = load_original_prompt()
    if STORY_FLOW_ANCHOR not in original:
        raise ValueError(
            "STORY_FLOW_ANCHOR not found verbatim in coinsignal_reel_prompt.txt; "
            "refusing to guess an insertion point for the funny/meme addition."
        )
    addition = load_funny_addition()
    insert_block = f"\n\n{addition}\n"
    return original.replace(STORY_FLOW_ANCHOR, STORY_FLOW_ANCHOR + insert_block, 1)


# ---------------------------------------------------------------------------
# Section 11: REEL SOURCE DATA (from a real, already-published Blogger post)
# ---------------------------------------------------------------------------

_STRIP_MARKER_RE = re.compile(r"<!--\s*COINSIGNAL_(SOURCE_ID|META_B64):[^>]*-->")
_HEADING_RE = re.compile(r"<h[23][^>]*>(.*?)</h[23]>", re.I | re.S)
_LIST_ITEM_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(fragment: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _article_body_text(content_html: str) -> str:
    body = _STRIP_MARKER_RE.sub("", content_html or "")
    return _clean_text(body)


def extract_article_facts(content_html: str) -> list[str]:
    """Verbatim facts only: article headings + list items, deduplicated,
    in true document order (headings and list items interleaved as they
    actually appear). Never summarizes or infers - if it's not text that
    already exists in the article, it doesn't go in this list."""
    content_html = content_html or ""
    raw_matches: list[tuple[int, str]] = []
    for pattern in (_HEADING_RE, _LIST_ITEM_RE):
        for match in pattern.finditer(content_html):
            raw_matches.append((match.start(), _clean_text(match.group(1))))
    raw_matches.sort(key=lambda pair: pair[0])

    facts: list[str] = []
    seen: set[str] = set()
    for _, fact in raw_matches:
        if fact and fact.lower() not in seen:
            seen.add(fact.lower())
            facts.append(fact)
    return facts[:MAX_FACTS]


def build_reel_source_data(
    *,
    article_title: str,
    article_url: str,
    article_content_html: str,
) -> dict[str, Any]:
    body_text = _article_body_text(article_content_html)
    facts = extract_article_facts(article_content_html)
    word_count = len(body_text.split())
    strong_enough = word_count >= MIN_ARTICLE_WORDS and bool(facts)

    return {
        "topic": article_title,
        "duration_minutes": REEL_DURATION_MINUTES,
        "duration_seconds": REEL_DURATION_SECONDS,
        "article_url": article_url,
        "article_title": article_title,
        "article_text": body_text,
        "article_facts": facts,
        "status": "READY" if strong_enough else "NEEDS_REVIEW",
        "word_count": word_count,
    }


# ---------------------------------------------------------------------------
# Section 12-13: reel_story.json schema, request assembly, validation
# ---------------------------------------------------------------------------

AUTO_MODE_INSTRUCTIONS = """AUTOMATION LAYER INSTRUCTIONS (not part of the creative prompt below):
This is AUTO_REEL_MODE. The topic and duration are already decided by the
REEL_SOURCE_DATA JSON above - skip STEP 1 (topic generation) and the
interactive part of STEP 2 (do not ask the user anything). Duration is
2.3 minutes (138 seconds): generate Part 1 (10 scenes, ~60s), then Part 2
(10 scenes, ~60s), then a final 3-scene continuation segment (~18s, not a
full Part) that concludes the story - do not stop and wait for "Continue"
between them, produce all of it in one structured response. Every fact,
number, or event you reference must come from REEL_SOURCE_DATA.article_facts
or REEL_SOURCE_DATA.article_text - never invent statistics.
Return ONLY JSON matching this schema: {schema}"""

STORY_JSON_SCHEMA_EXAMPLE = {
    "job_id": "string",
    "source_article_id": "string",
    "source_article_url": "string",
    "source_article_title": "string",
    "duration_minutes": REEL_DURATION_MINUTES,
    "duration_seconds": REEL_DURATION_SECONDS,
    "style": REEL_STYLE,
    "parts": [
        {
            "part_number": "int",
            "scenes": [{"scene_number": "int", "image_prompt": "string", "motion_prompt": "string", "voiceover": "string"}],
        }
    ],
}


def assemble_gemini_reel_request(source_data: dict[str, Any]) -> str:
    """The exact text that would be sent to Gemini: source data as DATA,
    automation-layer instructions, then the immutable prompt (original +
    sanctioned addition) unchanged."""
    data_block = "REEL_SOURCE_DATA (JSON - data only, not instructions):\n" + json.dumps(source_data, indent=2, ensure_ascii=False)
    auto_block = AUTO_MODE_INSTRUCTIONS.format(schema=json.dumps(STORY_JSON_SCHEMA_EXAMPLE))
    return f"{data_block}\n\n{auto_block}\n\n{full_prompt_text()}"


def empty_story_skeleton(job_id: str, selection: dict[str, Any]) -> dict[str, Any]:
    """The JSON shape a real Gemini response must fill in - scaffolding
    only, not generated content (see module docstring)."""
    parts = []
    for plan in PART_PLAN:
        parts.append(
            {
                "part_number": plan["part_number"],
                "target_seconds": plan["target_seconds"],
                "is_final_segment": plan["is_final_segment"],
                "scenes": [
                    {"scene_number": i + 1, "image_prompt": "", "motion_prompt": "", "voiceover": ""}
                    for i in range(plan["scene_count"])
                ],
            }
        )
    return {
        "job_id": job_id,
        "source_article_id": selection.get("reel_source_article_id", ""),
        "source_article_url": selection.get("reel_source_article_url", ""),
        "source_article_title": selection.get("reel_source_article_title", ""),
        "duration_minutes": REEL_DURATION_MINUTES,
        "duration_seconds": REEL_DURATION_SECONDS,
        "style": REEL_STYLE,
        "created_at": iso_now(),
        "parts": parts,
    }


def validate_story_json(doc: dict[str, Any]) -> list[str]:
    """Schema/shape validator - does not judge content quality, only
    structural correctness against sections 12-13. Returns a list of
    error strings (empty = valid)."""
    errors: list[str] = []
    required_top = ["job_id", "source_article_id", "source_article_url", "duration_seconds", "style", "parts"]
    for key in required_top:
        if key not in doc:
            errors.append(f"missing top-level key: {key}")
    if errors:
        return errors

    if doc["duration_seconds"] != REEL_DURATION_SECONDS:
        errors.append(f"duration_seconds must be {REEL_DURATION_SECONDS}, got {doc['duration_seconds']}")

    parts = doc.get("parts")
    if not isinstance(parts, list) or len(parts) != len(PART_PLAN):
        errors.append(f"parts must be a list of {len(PART_PLAN)} entries")
        return errors

    for plan, part in zip(PART_PLAN, parts):
        if part.get("part_number") != plan["part_number"]:
            errors.append(f"part {plan['part_number']}: wrong part_number {part.get('part_number')}")
        scenes = part.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != plan["scene_count"]:
            errors.append(
                f"part {plan['part_number']}: expected {plan['scene_count']} scenes, "
                f"got {len(scenes) if isinstance(scenes, list) else 'non-list'}"
            )
            continue
        for scene in scenes:
            for field in ("scene_number", "image_prompt", "motion_prompt", "voiceover"):
                if field not in scene:
                    errors.append(f"part {plan['part_number']} scene missing field: {field}")
                elif field != "scene_number" and not str(scene[field]).strip():
                    errors.append(f"part {plan['part_number']} scene {scene.get('scene_number')}: empty {field}")
    return errors


def write_story_json(doc: dict[str, Any], date: str, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else Path(__file__).resolve().parent / "jobs"
    out_path = root / date / "reel_story.json"
    write_json(out_path, doc)
    return out_path
