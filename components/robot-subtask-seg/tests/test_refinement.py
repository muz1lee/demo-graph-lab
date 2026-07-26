import json
from pathlib import Path

from robot_subtask_seg.config import load_config
from robot_subtask_seg.providers.fake import FakeProvider
from robot_subtask_seg.refinement import refine_trace_actions
from robot_subtask_seg.schema import SegmentEvidence, Trace, TraceSegment, VideoInfo
from test_contact_sheet import write_video


def test_refine_trace_actions_uses_visual_context_and_parent_window(tmp_path: Path):
    video = tmp_path / "demo.mp4"
    write_video(video, num_frames=18, fps=6)
    trace = Trace(
        trace_id="demo__trace",
        task_id="demo",
        task_class="put_bottles_into_dustbin",
        instruction="Put the bottle in the dustbin.",
        video=VideoInfo(path=str(video), duration_sec=3.0),
        segments=[
            TraceSegment(
                index=0,
                start_sec=0.0,
                end_sec=0.5,
                label="previous context",
                actor_arm="left_arm",
                receiver_arm="none",
                eef_event="move",
                motion_type="transport",
                manipulated_object="bottle",
                target_object="dustbin",
                target_role="receptacle",
                role="core",
                confidence=0.9,
                visual_evidence="visible",
                method_note="context",
            ),
            TraceSegment(
                index=1,
                start_sec=0.5,
                end_sec=2.0,
                label="Pick up the bottle and drop it into the dustbin",
                actor_arm="left_arm",
                receiver_arm="none",
                eef_event="place",
                motion_type="place",
                manipulated_object="bottle",
                target_object="dustbin",
                target_role="receptacle",
                role="core",
                confidence=0.9,
                visual_evidence="visible",
                method_note="compound",
                evidence=SegmentEvidence(contact_sheets=["old_sheet.jpg"]),
            ),
            TraceSegment(
                index=2,
                start_sec=2.0,
                end_sec=2.5,
                label="next context",
                actor_arm="left_arm",
                receiver_arm="none",
                eef_event="move",
                motion_type="transport",
                manipulated_object="bottle",
                target_object="dustbin",
                target_role="receptacle",
                role="core",
                confidence=0.9,
                visual_evidence="visible",
                method_note="context",
            ),
        ],
        model="seed",
        provider="seed",
    )

    refined, manifest = refine_trace_actions(
        trace,
        config=load_config(),
        output_dir=tmp_path / "refined",
        provider=FakeProvider(),
    )

    assert len(refined.segments) == 4
    child = refined.segments[1]
    assert child.start_sec == 0.5
    assert child.end_sec > child.start_sec
    assert child.evidence.contact_sheets
    assert Path(child.evidence.contact_sheets[0]).exists()
    assert manifest["refined_segment_count"] == 1
    item = manifest["items"][0]
    assert len(item["image_paths"]) == 3
    assert all(Path(path).exists() for path in item["image_paths"])
    assert "previous context" in " ".join(item["image_order"])
    raw = json.loads(Path(item["raw_response_path"]).read_text(encoding="utf-8"))
    assert len(raw["segments"]) == 2
