import json
from pathlib import Path

from robot_subtask_seg.operation_structure import build_operation_structure
from robot_subtask_seg.providers.fake import FakeProvider
from robot_subtask_seg.schema import (
    ObservedOperationPhase,
    OperationPhaseTemplate,
    OperationStructureResponse,
    Trace,
    TraceSegment,
    VideoInfo,
)
from test_contact_sheet import write_video


def _trace(video: Path) -> Trace:
    return Trace(
        trace_id="repeat_demo__refined",
        task_id="repeat_demo",
        task_class="generic",
        instruction="Move two objects to the same target.",
        video=VideoInfo(path=str(video), duration_sec=4.0),
        segments=[
            TraceSegment(
                index=0,
                start_sec=0.0,
                end_sec=2.0,
                label="move first object",
                actor_arm="left_arm",
                eef_event="place",
                motion_type="place",
            ),
            TraceSegment(
                index=1,
                start_sec=2.0,
                end_sec=4.0,
                label="move second object",
                actor_arm="right_arm",
                eef_event="place",
                motion_type="place",
            ),
        ],
        model="seed",
        provider="seed",
    )


def test_operation_structure_reuses_one_procedure_across_instances(tmp_path: Path) -> None:
    video = tmp_path / "demo.mp4"
    write_video(video, num_frames=24, fps=6)
    result = build_operation_structure(
        _trace(video),
        provider=FakeProvider(),
        output_dir=tmp_path / "visual",
        mode="visual_only",
        sample_sec=0.5,
    )

    assert result["schema"] == "robot_subtask_seg.operation_structure.v1"
    assert result["summary"] == {
        "procedure_count": 1,
        "instance_count": 2,
        "phase_count": 4,
        "reused_procedure_count": 1,
    }
    assert {item["procedure_ref"] for item in result["instances"]} == {"relocate_object"}
    assert (tmp_path / "visual" / "operation_structure.json").exists()
    assert len(result["provenance"]["image_paths"]) == 1


def test_evidence_guided_prompt_includes_bundle_without_claiming_skill_graph(
    tmp_path: Path,
) -> None:
    video = tmp_path / "demo.mp4"
    write_video(video, num_frames=24, fps=6)
    bundle = {
        "objects": [
            {
                "object_id": "object_000",
                "prompt": "movable object",
                "reliable_frame_fraction": 0.8,
                "path_length_px": 42.0,
            }
        ],
        "segment_evidence": [
            {
                "segment_index": 0,
                "object_observations": [
                    {
                        "object_id": "object_000",
                        "evidence_ref": "dense_track:object_000:segment:0",
                    }
                ],
            }
        ],
        "evidence_gaps": [{"capability": "metric_depth", "reason": "RGB only"}],
    }
    result = build_operation_structure(
        _trace(video),
        provider=FakeProvider(),
        output_dir=tmp_path / "guided",
        mode="evidence_guided",
        demonstration_bundle=bundle,
        sample_sec=0.5,
    )

    prompt = Path(result["provenance"]["prompt_path"]).read_text(encoding="utf-8")
    assert "dense_track:object_000:segment:0" in prompt
    assert "Repeated episodes" in prompt
    assert result["provenance"]["claims_final_skill_graph"] is False
    persisted = json.loads(
        (tmp_path / "guided" / "operation_structure.json").read_text(encoding="utf-8")
    )
    assert persisted["mode"] == "evidence_guided"


def test_operation_structure_accepts_relocated_video(tmp_path: Path) -> None:
    video = tmp_path / "relocated.mp4"
    write_video(video, num_frames=24, fps=6)
    trace = _trace(video)
    trace.video.path = "/remote/machine/demo.mp4"

    result = build_operation_structure(
        trace,
        provider=FakeProvider(),
        output_dir=tmp_path / "relocated",
        video_path=video,
        mode="visual_only",
        sample_sec=0.5,
    )

    assert result["provenance"]["source_video"] == str(video)


def test_open_evidence_fields_accept_single_strings() -> None:
    template = OperationPhaseTemplate.model_validate(
        {
            "phase_id": "align",
            "intent": "align object",
            "entry_state": "object is held",
            "observable_evidence": "object axis changes",
        }
    )
    phase = ObservedOperationPhase.model_validate(
        {
            "start_sec": 0.0,
            "end_sec": 1.0,
            "description": "align object",
            "evidence_basis": "timestamped image",
            "evidence_refs": "sheet_001:0.5s",
        }
    )

    assert template.entry_state == ["object is held"]
    assert phase.evidence_basis == ["timestamped image"]


def test_operation_structure_preserves_structured_evidence_gaps() -> None:
    parsed = OperationStructureResponse.model_validate(
        {
            "canonical_procedures": [
                {
                    "procedure_id": "demo",
                    "name": "demo",
                    "reusable_intent": "demo",
                }
            ],
            "instances": [
                {
                    "instance_id": "demo_0",
                    "procedure_ref": "demo",
                    "start_sec": 0.0,
                    "end_sec": 1.0,
                }
            ],
            "evidence_gaps": [
                {"capability": "metric_depth", "reason": "RGB only"}
            ],
        }
    )

    assert parsed.evidence_gaps[0] == {
        "capability": "metric_depth",
        "reason": "RGB only",
    }
