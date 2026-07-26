from robot_subtask_seg.action_trace import materialize_action_trace
from robot_subtask_seg.schema import Trace, TraceSegment, VideoInfo


def test_materialize_action_trace_keeps_cleanup_by_default_and_splits_compound():
    trace = Trace(
        trace_id="demo__trace",
        task_id="demo",
        task_class="put_bottles_into_dustbin",
        video=VideoInfo(path="demo.mp4", duration_sec=3.0),
        segments=[
            TraceSegment(
                index=0,
                start_sec=0.0,
                end_sec=2.0,
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
            ),
            TraceSegment(
                index=1,
                start_sec=2.0,
                end_sec=3.0,
                label="retract arm",
                actor_arm="left_arm",
                eef_event="release",
                motion_type="cleanup",
                manipulated_object="none",
                target_object="none",
                target_role="none",
                role="cleanup",
                confidence=0.9,
                visual_evidence="visible",
                method_note="cleanup",
            ),
        ],
        model="test",
        provider="test",
    )

    action_trace = materialize_action_trace(trace)

    assert action_trace["filtered_segment_count"] == 0
    assert action_trace["heuristic_split_count"] == 2
    assert [step["eef_event"] for step in action_trace["steps"]] == ["grasp", "place", "release"]
    assert action_trace["steps"][0]["boundary_source"] == "heuristic_split"


def test_materialize_action_trace_can_filter_cleanup():
    trace = Trace(
        trace_id="demo__trace",
        task_id="demo",
        task_class="push_T",
        video=VideoInfo(path="demo.mp4", duration_sec=2.0),
        segments=[
            TraceSegment(
                index=0,
                start_sec=0.0,
                end_sec=1.0,
                label="push block",
                actor_arm="left_arm",
                eef_event="push",
                motion_type="push",
                manipulated_object="block",
                target_object="pad",
                target_role="pad",
                role="core",
            ),
            TraceSegment(
                index=1,
                start_sec=1.0,
                end_sec=2.0,
                label="go home",
                actor_arm="left_arm",
                eef_event="release",
                motion_type="cleanup",
                manipulated_object="none",
                target_object="none",
                target_role="none",
                role="cleanup",
            ),
        ],
        model="test",
        provider="test",
    )

    action_trace = materialize_action_trace(trace, include_cleanup=False)

    assert action_trace["filtered_segment_count"] == 1
    assert [step["label"] for step in action_trace["steps"]] == ["push block"]
