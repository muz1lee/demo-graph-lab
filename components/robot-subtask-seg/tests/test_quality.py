from pathlib import Path

from robot_subtask_seg.archive import archive_run
from robot_subtask_seg.quality import audit_run, audit_trace, compound_segment_reason
from robot_subtask_seg.schema import Trace, TraceSegment, VideoInfo


def _trace(segments: list[TraceSegment]) -> Trace:
    return Trace(
        trace_id="demo__trace",
        task_id="demo",
        task_class="put_bottles_into_dustbin",
        video=VideoInfo(path="demo.mp4", duration_sec=5.0),
        segments=segments,
        model="test",
        provider="test",
    )


def _generic_trace(segments: list[TraceSegment]) -> Trace:
    return Trace(
        trace_id="demo__trace",
        task_id="demo",
        task_class="generic_task",
        video=VideoInfo(path="demo.mp4", duration_sec=5.0),
        segments=segments,
        model="test",
        provider="test",
    )


def test_compound_segment_detection():
    segment = TraceSegment(
        index=0,
        start_sec=0.0,
        end_sec=1.0,
        label="Pick up the green bottle and drop it into the dustbin",
        actor_arm="left_arm",
        eef_event="place",
        motion_type="place",
        manipulated_object="green bottle",
        target_object="dustbin",
        target_role="receptacle",
        role="core",
        confidence=0.9,
        visual_evidence="visible",
        method_note="pick then place",
    )

    assert compound_segment_reason(segment)
    issues = audit_trace(_generic_trace([segment]))
    assert any(item["category"] == "compound_segment" for item in issues)
    assert not any(item["severity"] == "error" for item in issues)


def test_missing_action_fields_are_structural_errors():
    segment = TraceSegment(
        index=0,
        start_sec=0.0,
        end_sec=1.0,
        label="move object",
    )

    issues = audit_trace(_generic_trace([segment]))

    assert any(item["category"] == "missing_action_field" for item in issues)
    assert any(item["severity"] == "error" for item in issues)


def test_audit_run_statuses_compound_as_refinement_needed(tmp_path: Path):
    run_dir = tmp_path / "run"
    trace_dir = run_dir / "put_bottles_into_dustbin" / "demo"
    trace_dir.mkdir(parents=True)
    segment = TraceSegment(
        index=0,
        start_sec=0.0,
        end_sec=1.0,
        label="Pick up the green bottle and drop it into the dustbin",
        actor_arm="left_arm",
        eef_event="place",
        motion_type="place",
        manipulated_object="green bottle",
        target_object="dustbin",
        target_role="receptacle",
        role="core",
        confidence=0.9,
        visual_evidence="visible",
        method_note="pick then place",
    )
    (trace_dir / "trace.json").write_text(_trace([segment]).model_dump_json(), encoding="utf-8")
    (run_dir / "summary.json").write_text("[]\n", encoding="utf-8")

    report = audit_run(run_dir, base_dir=tmp_path)

    assert report["status"] == "needs_action_refinement"
    assert report["video_trace_ready"] is True
    assert report["execution_ready"] is False
    assert report["archive_recommended"] is False


def test_cleanup_segment_is_retained_info_not_refinement_failure():
    segment = TraceSegment(
        index=0,
        start_sec=0.0,
        end_sec=1.0,
        label="go home",
        actor_arm="left_arm",
        eef_event="release",
        motion_type="cleanup",
        manipulated_object="none",
        target_object="none",
        target_role="none",
        role="cleanup",
        confidence=0.9,
        visual_evidence="arm retracts",
        method_note="return arm home",
    )

    issues = audit_trace(_generic_trace([segment]))

    cleanup_issues = [item for item in issues if item["category"] == "cleanup_segment"]
    assert cleanup_issues
    assert all(item["severity"] == "info" for item in cleanup_issues)


def test_archive_run_moves_directory_and_writes_reason(tmp_path: Path):
    run_dir = tmp_path / "outputs" / "bad_run"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("[]\n", encoding="utf-8")

    dest = archive_run(run_dir, archive_root=tmp_path / "outputs" / "_bad_runs", reason="bad")

    assert not run_dir.exists()
    assert dest.exists()
    assert (dest / "ARCHIVE_REASON.txt").read_text(encoding="utf-8") == "bad\n"
