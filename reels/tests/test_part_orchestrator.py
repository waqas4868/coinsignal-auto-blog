"""Phase 3 tests for reels/part_orchestrator.py and reel_story.assemble_part_request.

Run: python reels/tests/test_part_orchestrator.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import part_orchestrator as po  # noqa: E402
import reel_story as rs  # noqa: E402


def _selection() -> dict:
    return {
        "reel_source_article_id": "abc123",
        "reel_source_article_url": "https://blogsdrip4u.blogspot.com/abc123.html",
        "reel_source_article_title": "Bitcoin Breaks Key Resistance",
    }


def _source_data() -> dict:
    return {
        "topic": "Bitcoin Breaks Key Resistance",
        "duration_minutes": 2.3,
        "duration_seconds": 138,
        "article_url": "https://blogsdrip4u.blogspot.com/abc123.html",
        "article_title": "Bitcoin Breaks Key Resistance",
        "article_text": "Bitcoin surged past resistance today.",
        "article_facts": ["Bitcoin gained 7.4% in 24 hours."],
        "status": "READY",
        "word_count": 200,
    }


def _fake_part(part_number: int, scene_count: int, *, voiceover_prefix: str = "vo") -> dict:
    return {
        "part_number": part_number,
        "scenes": [
            {
                "scene_number": i + 1,
                "image_prompt": f"Use the same stickman character as before... part{part_number} scene{i + 1}",
                "motion_prompt": "Animate arms and head only, minimal smooth motion.",
                "voiceover": f"{voiceover_prefix}-p{part_number}-s{i + 1}",
            }
            for i in range(scene_count)
        ],
    }


# ---------------------------------------------------------------------------
# assemble_part_request (pure)
# ---------------------------------------------------------------------------

def test_assemble_part_request_shapes_per_part() -> None:
    source_data = _source_data()
    for plan in rs.PART_PLAN:
        text = rs.assemble_part_request(source_data, plan, prior_summary="")
        assert f"Part {plan['part_number']}" in text
        assert text.endswith(rs.full_prompt_text())
        assert "CONTINUITY SO FAR" not in text, "no continuity block should appear when prior_summary is empty"

    with_context = rs.assemble_part_request(source_data, rs.PART_PLAN[1], "Part 1 recap:\n- Scene 1: hook")
    assert "CONTINUITY SO FAR" in with_context
    assert "Part 1 recap" in with_context
    print("PASS: assemble_part_request scopes to exactly one Part, includes continuity only when present")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def test_happy_path_sequential_generation_with_growing_context() -> None:
    calls: list[tuple[int, str]] = []

    def fake_generate(plan, source_data, prior_summary):
        calls.append((plan["part_number"], prior_summary))
        return _fake_part(plan["part_number"], plan["scene_count"])

    with tempfile.TemporaryDirectory() as tmp:
        doc = po.run_part_orchestration(
            job_id="REEL-TEST-1", source_data=_source_data(), selection=_selection(),
            generate_part=fake_generate, job_dir=Path(tmp),
        )
        assert [c[0] for c in calls] == [1, 2, 3], "parts must be generated strictly in order"
        assert calls[0][1] == "", "Part 1 must be called with no prior context"
        assert "Part 1 recap" in calls[1][1] and "Part 2 recap" not in calls[1][1]
        assert "Part 1 recap" in calls[2][1] and "Part 2 recap" in calls[2][1]
        assert rs.validate_story_json(doc) == []
        progress = po.load_progress(Path(tmp))
        assert progress["status"] == "COMPLETE"
    print("PASS: Parts generated strictly in order, each given a growing (not full-transcript) continuity summary")


def test_resume_does_not_regenerate_completed_parts() -> None:
    calls: list[int] = []

    def spy_generate(plan, source_data, prior_summary):
        calls.append(plan["part_number"])
        if plan["part_number"] == 1:
            raise AssertionError("Part 1 was already completed and must not be regenerated")
        return _fake_part(plan["part_number"], plan["scene_count"])

    with tempfile.TemporaryDirectory() as tmp:
        job_dir = Path(tmp)
        # Pre-seed job.json as if Part 1 already completed on a prior run.
        from common import write_json
        write_json(job_dir / "job.json", {
            "job_id": "REEL-TEST-2",
            "source_article_id": "abc123",
            "source_article_url": "https://blogsdrip4u.blogspot.com/abc123.html",
            "source_article_title": "Bitcoin Breaks Key Resistance",
            "duration_minutes": 2.3,
            "duration_seconds": 138,
            "style": rs.REEL_STYLE,
            "created_at": "2026-09-23T00:00:00+00:00",
            "parts": [_fake_part(1, 10, voiceover_prefix="preexisting")],
            "status": "IN_PROGRESS",
        })

        doc = po.run_part_orchestration(
            job_id="REEL-TEST-2", source_data=_source_data(), selection=_selection(),
            generate_part=spy_generate, job_dir=job_dir,
        )
        assert calls == [2, 3], f"generator should only be called for the missing parts, got {calls}"
        part1_voiceovers = [s["voiceover"] for s in doc["parts"][0]["scenes"]]
        assert part1_voiceovers[0] == "preexisting-p1-s1", "resumed Part 1 content must be reused unchanged"
    print("PASS: resuming a job reuses already-completed parts and never regenerates them")


def test_generation_failure_stops_job_before_later_parts() -> None:
    calls: list[int] = []

    def failing_generate(plan, source_data, prior_summary):
        calls.append(plan["part_number"])
        if plan["part_number"] == 2:
            raise RuntimeError("Gemini hard quota: simulated failure")
        return _fake_part(plan["part_number"], plan["scene_count"])

    with tempfile.TemporaryDirectory() as tmp:
        job_dir = Path(tmp)
        raised = False
        try:
            po.run_part_orchestration(
                job_id="REEL-TEST-3", source_data=_source_data(), selection=_selection(),
                generate_part=failing_generate, job_dir=job_dir,
            )
        except po.PartGenerationError as exc:
            raised = True
            assert "Part 2" in str(exc)
        assert raised, "a Part failure must raise PartGenerationError"
        assert calls == [1, 2], "Part 3 must never be attempted after Part 2 fails"
        progress = po.load_progress(job_dir)
        assert progress["status"] == "FAILED"
        assert len(progress["parts"]) == 1, "only the successfully-completed Part 1 should be persisted"
    print("PASS: a mid-job generation failure stops before later parts, persists FAILED status, keeps completed work")


def test_invalid_part_shape_is_rejected_and_not_persisted() -> None:
    def bad_generate(plan, source_data, prior_summary):
        part = _fake_part(plan["part_number"], plan["scene_count"])
        if plan["part_number"] == 1:
            part["scenes"] = part["scenes"][:3]  # wrong scene count for Part 1 (needs 10)
        return part

    with tempfile.TemporaryDirectory() as tmp:
        job_dir = Path(tmp)
        raised = False
        try:
            po.run_part_orchestration(
                job_id="REEL-TEST-4", source_data=_source_data(), selection=_selection(),
                generate_part=bad_generate, job_dir=job_dir,
            )
        except po.PartGenerationError as exc:
            raised = True
            assert "expected 10 scenes" in str(exc)
        assert raised
        progress = po.load_progress(job_dir)
        assert progress["parts"] == [], "a shape-invalid part must never be persisted into job.json"
    print("PASS: a part with the wrong scene count is rejected before being persisted")


def test_duplicate_voiceover_across_parts_is_rejected() -> None:
    def duplicating_generate(plan, source_data, prior_summary):
        part = _fake_part(plan["part_number"], plan["scene_count"])
        if plan["part_number"] == 2:
            part["scenes"][0]["voiceover"] = "vo-p1-s1"  # exact duplicate of Part 1 scene 1
        return part

    with tempfile.TemporaryDirectory() as tmp:
        job_dir = Path(tmp)
        raised = False
        try:
            po.run_part_orchestration(
                job_id="REEL-TEST-5", source_data=_source_data(), selection=_selection(),
                generate_part=duplicating_generate, job_dir=job_dir,
            )
        except po.PartGenerationError as exc:
            raised = True
            assert "repeated voiceover" in str(exc)
        assert raised
        progress = po.load_progress(job_dir)
        assert progress["status"] == "FAILED", "duplicate-voiceover rejection must be reflected in job status, not left as COMPLETE"
    print("PASS: an exact-duplicate voiceover line across Parts is caught and marks the job FAILED, not COMPLETE")


if __name__ == "__main__":
    test_assemble_part_request_shapes_per_part()
    test_happy_path_sequential_generation_with_growing_context()
    test_resume_does_not_regenerate_completed_parts()
    test_generation_failure_stops_job_before_later_parts()
    test_invalid_part_shape_is_rejected_and_not_persisted()
    test_duplicate_voiceover_across_parts_is_rejected()
    print("\nALL PHASE 3 TESTS PASSED")
