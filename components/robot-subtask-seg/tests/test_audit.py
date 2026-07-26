from robot_subtask_seg.audit import apply_trace_audit
from robot_subtask_seg.schema import Trace, TraceSegment, VideoInfo


def test_audit_flags_static_receptacle_as_manipulated_object():
    trace = Trace(
        trace_id="deposit_coin__demo",
        task_id="demo",
        task_class="deposit_coin",
        video=VideoInfo(path="demo.mp4", duration_sec=7.0),
        segments=[
            TraceSegment(
                index=0,
                start_sec=0.0,
                end_sec=1.0,
                label="move the coin bank closer",
                manipulated_object="coin bank",
                target_object="coin",
            )
        ],
        model="test",
        provider="test",
    )

    audited = apply_trace_audit(trace)

    assert "object_role_inversion" in audited.segments[0].risk_flags
    assert any("object-role inversion" in warning for warning in audited.quality_warnings)
    assert "expected_bimanual_or_handover_not_detected" in audited.quality_warnings
    assert "expected_fine_alignment_not_detected" in audited.quality_warnings
