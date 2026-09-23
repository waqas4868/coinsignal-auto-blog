"""Phase 11 tests for reels/reel_pipeline_worker.py's pure decision core.

The full run_daily_pipeline() wiring (with every external-service boundary
faked) is exercised end-to-end in Phase 12's dry-run test - this file
covers determine_next_stage() and make_job_id() exhaustively on their own,
since those are the parts that can be tested with zero I/O.

Run: python reels/tests/test_reel_pipeline_worker.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # reels/, for sibling import
import reel_pipeline_worker as worker  # noqa: E402


def _story_doc(scene_counts=(10, 10, 3)) -> dict:
    return {"parts": [{"scenes": [{"scene_number": i + 1} for i in range(n)]} for n in scene_counts]}


def test_not_ready_when_no_selection() -> None:
    stage = worker.determine_next_stage(selection=None, story_doc=None, scene_video_states=[], scene_voice_states=[], render_state=None, queue_job=None)
    assert stage == "NOT_READY"
    print("PASS: no selection yet -> NOT_READY")


def test_not_ready_when_selection_not_selected() -> None:
    stage = worker.determine_next_stage(
        selection={"reel_status": "NOT_READY"}, story_doc=None, scene_video_states=[], scene_voice_states=[], render_state=None, queue_job=None
    )
    assert stage == "NOT_READY"
    print("PASS: a selection record that isn't SELECTED yet -> NOT_READY")


def test_generate_story_when_selected_but_no_story() -> None:
    stage = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=None, scene_video_states=[], scene_voice_states=[], render_state=None, queue_job=None
    )
    assert stage == "GENERATE_STORY"
    print("PASS: selected article but no story yet -> GENERATE_STORY")


def test_generate_scene_assets_when_scenes_incomplete() -> None:
    doc = _story_doc()  # 23 scenes total
    stage = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=doc,
        scene_video_states=[{"status": "VIDEO_GENERATING"}], scene_voice_states=[{"status": "VOICE_READY"}],
        render_state=None, queue_job=None,
    )
    assert stage == "GENERATE_SCENE_ASSETS"
    print("PASS: story ready but scene assets incomplete (no manual-import pending) -> GENERATE_SCENE_ASSETS")


def test_await_manual_video_import_when_any_scene_is_awaiting() -> None:
    doc = _story_doc()
    stage = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=doc,
        scene_video_states=[{"status": "VIDEO_AWAITING_MANUAL_IMPORT"}], scene_voice_states=[{"status": "VOICE_READY"}],
        render_state=None, queue_job=None,
    )
    assert stage == "AWAIT_MANUAL_VIDEO_IMPORT"
    print("PASS: at least one scene awaiting a human Flow import -> AWAIT_MANUAL_VIDEO_IMPORT, distinct from a generic incomplete state")


def test_render_final_when_all_scenes_ready_but_no_render() -> None:
    doc = _story_doc(scene_counts=(1,))  # keep it small: 1 scene for this test
    ready = [{"status": "VIDEO_READY"}]
    voices = [{"status": "VOICE_READY"}]
    stage = worker.determine_next_stage(selection={"reel_status": "SELECTED"}, story_doc=doc, scene_video_states=ready, scene_voice_states=voices, render_state=None, queue_job=None)
    assert stage == "RENDER_FINAL"
    print("PASS: every scene's video+voice ready, no render yet -> RENDER_FINAL")


def test_render_final_when_render_state_is_not_ready() -> None:
    doc = _story_doc(scene_counts=(1,))
    stage = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=doc, scene_video_states=[{"status": "VIDEO_READY"}],
        scene_voice_states=[{"status": "VOICE_READY"}], render_state={"status": "RENDER_FAILED"}, queue_job=None,
    )
    assert stage == "RENDER_FINAL"
    print("PASS: a previously-failed render is retried (RENDER_FINAL), not treated as done")


def test_enqueue_when_render_ready_but_no_queue_job() -> None:
    doc = _story_doc(scene_counts=(1,))
    stage = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=doc, scene_video_states=[{"status": "VIDEO_READY"}],
        scene_voice_states=[{"status": "VOICE_READY"}], render_state={"status": "RENDER_READY"}, queue_job=None,
    )
    assert stage == "ENQUEUE"
    print("PASS: render ready, not yet in the Facebook queue -> ENQUEUE")


def test_publish_when_queued_but_not_published() -> None:
    doc = _story_doc(scene_counts=(1,))
    stage = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=doc, scene_video_states=[{"status": "VIDEO_READY"}],
        scene_voice_states=[{"status": "VOICE_READY"}], render_state={"status": "RENDER_READY"}, queue_job={"status": "READY"},
    )
    assert stage == "PUBLISH"
    stage_retry = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=doc, scene_video_states=[{"status": "VIDEO_READY"}],
        scene_voice_states=[{"status": "VOICE_READY"}], render_state={"status": "RENDER_READY"}, queue_job={"status": "RETRY"},
    )
    assert stage_retry == "PUBLISH"
    print("PASS: queued (READY or RETRY) but not yet PUBLISHED -> PUBLISH")


def test_done_when_published() -> None:
    doc = _story_doc(scene_counts=(1,))
    stage = worker.determine_next_stage(
        selection={"reel_status": "SELECTED"}, story_doc=doc, scene_video_states=[{"status": "VIDEO_READY"}],
        scene_voice_states=[{"status": "VOICE_READY"}], render_state={"status": "RENDER_READY"}, queue_job={"status": "PUBLISHED"},
    )
    assert stage == "DONE"
    print("PASS: PUBLISHED -> DONE")


def test_make_job_id_is_deterministic_per_article_and_date() -> None:
    a = worker.make_job_id("2026-09-23", "abc123")
    b = worker.make_job_id("2026-09-23", "abc123")
    c = worker.make_job_id("2026-09-23", "different-article")
    assert a == b, (a, b)
    assert a != c
    assert a.startswith("REEL-2026-09-23-FUNNY-")
    print("PASS: make_job_id is deterministic for the same date+article and differs for a different article")


if __name__ == "__main__":
    test_not_ready_when_no_selection()
    test_not_ready_when_selection_not_selected()
    test_generate_story_when_selected_but_no_story()
    test_generate_scene_assets_when_scenes_incomplete()
    test_await_manual_video_import_when_any_scene_is_awaiting()
    test_render_final_when_all_scenes_ready_but_no_render()
    test_render_final_when_render_state_is_not_ready()
    test_enqueue_when_render_ready_but_no_queue_job()
    test_publish_when_queued_but_not_published()
    test_done_when_published()
    test_make_job_id_is_deterministic_per_article_and_date()
    print("\nALL PHASE 11 (orchestrator decision core) TESTS PASSED")
