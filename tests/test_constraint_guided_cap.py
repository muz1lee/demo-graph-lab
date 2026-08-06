"""The CaP program must expose demo and downstream constraint use explicitly."""

from copy import deepcopy
import json
from pathlib import Path

from demo_graph_lab.policy import ablation
from demo_graph_lab.execution.planning_runtime import PlanningOnlyRuntime
from demo_graph_lab.perception import ObjectObservation, ObservationPacket, Proprioception
from demo_graph_lab.policy.backchain import selection_context
from demo_graph_lab.policy.program import compile_program, validate_program
from demo_graph_lab.selection.candidates import (
    CandidateBundle,
    CheckCertificate,
    CheckStatus,
    HardCheck,
)


def _graph() -> dict:
    return {
        "task": "tube_chain",
        "stages": [
            {
                "index": 0,
                "name": "pick",
                "stage_objects": {"manipulated": "tube", "target": None},
                "constraints": [
                    {"name": "region_grasp", "args": {
                        "obj": "tube", "region": "upper_body"}},
                    {"name": "approach_direction", "args": {
                        "target": "tube", "cone": "top_down"}},
                ],
                "acceptance": [{"name": "carry", "args": {}}],
                "holes": [{
                    "name": "tube_grasp_pose",
                    "type": "pose_se3",
                    "frame": "robot_base",
                    "resolver": "grasp_candidate",
                    "anchor": {"object_id": "tube", "part": "whole"},
                }],
            },
            {
                "index": 1,
                "name": "insert",
                "stage_objects": {"manipulated": "tube", "target": "rack"},
                "constraints": [
                    {"name": "axis_parallel", "args": {
                        "axis_a": "tube.long_axis", "axis_b": "rack.hole_axis"}},
                    {"name": "inside", "args": {
                        "obj_a": "tube", "obj_b": "rack.hole"}},
                    {"name": "approach_direction", "args": {
                        "target": "rack.hole", "cone": "top_down"}},
                ],
                "acceptance": [{"name": "carry", "args": {}}],
                "holes": [{
                    "name": "rack_hole_center",
                    "type": "point_3d",
                    "frame": "robot_base",
                }],
            },
            {
                "index": 2,
                "name": "pick_other",
                "stage_objects": {"manipulated": "other", "target": None},
                "constraints": [{"name": "clearance", "args": {
                    "obj_a": "other", "obj_b": "table"}}],
                "acceptance": [{"name": "carry", "args": {}}],
                "holes": [],
            },
        ],
    }


def _program() -> dict:
    return {
        "stages": [
            {
                "index": 0,
                "name": "pick",
                "selection": {
                    "grasp_hole": "tube_grasp_pose",
                    "current_constraints": [
                        "s0:c0:region_grasp",
                        "s0:c1:approach_direction",
                    ],
                    "downstream_constraints": [
                        "s1:c0:axis_parallel",
                        "s1:c1:inside",
                    ],
                },
                "actions": [{
                    "op": "grasp_at",
                    "args": {"grasp_pose": {"hole": "tube_grasp_pose"}},
                }],
            },
            {
                "index": 1,
                "name": "insert",
                "actions": [{
                    "op": "align",
                    "args": {
                        "obj": {"object": "tube"},
                        "target": {"hole": "rack_hole_center"},
                    },
                }],
            },
            {
                "index": 2,
                "name": "pick_other",
                "actions": [{"op": "release", "args": {}}],
            },
        ],
    }


def test_backchain_context_keeps_only_future_constraints_about_current_object() -> None:
    context = selection_context(_graph(), mode="backchain")[0]

    assert context["current_constraints"] == [
        "s0:c0:region_grasp",
        "s0:c1:approach_direction",
    ]
    assert context["downstream_constraints"] == [
        "s1:c0:axis_parallel",
        "s1:c1:inside",
    ]


def test_stage_program_validates_and_compiles_explicit_constraint_calls() -> None:
    graph, program = _graph(), _program()

    assert validate_program(
        program, graph, selection_mode="backchain",
    ) == []
    code = compile_program(program, graph, selection_mode="backchain")

    assert "rt.begin_candidates('tube_grasp_pose')" in code
    assert "rt.rank_by('s0:c0:region_grasp')" in code
    assert "rt.require_future('s1:c0:axis_parallel')" in code
    assert "h0 = rt.choose('tube_grasp_pose')" in code
    assert "rt.solve('tube_grasp_pose')" not in code


def test_local_arm_rejects_downstream_constraint_calls() -> None:
    errors = validate_program(
        _program(), _graph(), selection_mode="local",
    )

    assert any("downstream_constraints" in error for error in errors)


def test_selection_mode_is_inferred_when_loading_a_published_program() -> None:
    program = _program()
    context = selection_context(_graph(), mode="local")[0]
    program["stages"][0]["selection"]["downstream_constraints"] = []

    assert program["stages"][0]["selection"]["current_constraints"] == (
        context["current_constraints"]
    )
    assert validate_program(program, _graph()) == []


def test_selected_hole_must_feed_the_grasp_primitive() -> None:
    program = _program()
    program["stages"][0]["actions"] = [{
        "op": "approach",
        "args": {"target": {"hole": "tube_grasp_pose"}},
    }]

    errors = validate_program(program, _graph(), selection_mode="backchain")

    assert any("must feed grasp_at.grasp_pose" in error for error in errors)


def test_explicit_cap_rejects_grasp_from_a_non_candidate_pose() -> None:
    graph = _graph()
    graph["stages"][0]["holes"][0]["resolver"] = "motion_derived"

    errors = validate_program(
        _program(), graph, selection_mode="backchain",
    )

    assert any("resolver='grasp_candidate'" in error for error in errors)


def test_reviewed_insert_tubes_routes_all_three_grasps_through_selection() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures" / "graphs" / "insert_tubes.graph.json"
    )
    graph = json.loads(fixture.read_text())
    context = selection_context(graph, mode="backchain")

    assert set(context) == {0, 2, 4}
    assert context[4]["current_constraints"] == ["s4:c3:region_grasp"]
    assert context[4]["downstream_constraints"] == [
        "s5:c0:inside",
        "s5:c1:axis_vertical",
        "s5:c2:axis_parallel",
        "s5:c3:center_align",
    ]


def test_three_arm_ablation_changes_generated_selection_code(
    tmp_path: Path, monkeypatch,
) -> None:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(_graph()))

    def fake_chat(messages, output_dir, *, tag, **kwargs):
        mode = tag.split("_", 1)[0]
        context = selection_context(_graph(), mode=mode)[0]
        program = deepcopy(_program())
        selection = program["stages"][0]["selection"]
        selection["current_constraints"] = context["current_constraints"]
        selection["downstream_constraints"] = context["downstream_constraints"]
        return json.dumps(program)

    monkeypatch.setattr(ablation.llm, "chat", fake_chat)
    monkeypatch.setattr(ablation.llm, "record_result", lambda *args, **kwargs: None)

    summary_path = ablation.run(graph_path, tmp_path / "experiment", repeats=1)
    summary = json.loads(summary_path.read_text())

    assert all(item["valid"] == 1 for item in summary["modes"].values())
    assert summary["paired"] == [{
        "repeat": 0,
        "all_valid": True,
        "action_ops_equal": True,
        "selection_code_differs": True,
    }]


def _observation() -> ObservationPacket:
    return ObservationPacket(
        observation_id="obs-1",
        captured_at_s=1.0,
        frame="robot_base",
        calibration_ref="calibration/test.json",
        sensor_refs=("rgb/test.png",),
        robot_state=Proprioception(
            joint_positions=(0.0,) * 7,
            gripper_positions=(0.0,),
            end_effector_frame="robot_base",
            end_effector_poses={},
            evidence_ref="proprioception/test.json",
        ),
        objects=(ObjectObservation(
            object_id="tube",
            frame="robot_base",
            evidence_refs=("perception/tube.json",),
        ),),
    )


def _candidate(candidate_id: str, height: float, future: dict[str, str]) -> CandidateBundle:
    return CandidateBundle(
        candidate_id=candidate_id,
        observation_id="obs-1",
        hole_values={
            "tube_grasp_pose": {
                "value": [0.4, 0.0, 0.8, 0.0, 0.0, 0.0, 1.0],
                "frame": "robot_base",
                "calibration_ref": "calibration/test.json",
                "object_id": "tube",
            },
        },
        features={
            "height_fraction": height,
            "approach_tilt_deg": 0.0,
            "future_constraints": future,
        },
        evidence_refs=(f"candidates/{candidate_id}.json",),
    )


def _pass_check(name: str) -> HardCheck:
    return HardCheck(
        name=name,
        evaluate=lambda candidate, observation: CheckCertificate(
            check=name,
            status=CheckStatus.PASS,
            reason="test_pass",
            evidence_refs=(f"checks/{candidate.candidate_id}-{name}.json",),
        ),
    )


def test_generated_constraint_pipeline_rejects_local_best_that_kills_insertion(
    tmp_path: Path,
) -> None:
    future_refs = ("s1:c0:axis_parallel", "s1:c1:inside")
    candidates = (
        _candidate("local-best", 0.95, {
            future_refs[0]: "FAIL", future_refs[1]: "PASS",
        }),
        _candidate("future-viable", 0.75, {
            future_refs[0]: "PASS", future_refs[1]: "PASS",
        }),
    )
    runtime = PlanningOnlyRuntime(
        _graph(),
        observation_provider=lambda stage: _observation(),
        candidate_provider=lambda stage, observation: candidates,
        hard_checks=tuple(_pass_check(name) for name in (
            "reachability", "collision_free", "gripper_width",
        )),
        decision_log_path=tmp_path / "decisions.jsonl",
        stage_program=_program(),
    )
    runtime.begin_stage(_graph()["stages"][0])

    runtime.begin_candidates("tube_grasp_pose")
    runtime.rank_by("s0:c0:region_grasp")
    runtime.rank_by("s0:c1:approach_direction")
    runtime.require_future(future_refs[0])
    runtime.require_future(future_refs[1])
    runtime.choose("tube_grasp_pose")

    assert runtime.selected_candidate_id == "future-viable"
    assert len(runtime.decisions) == 1
    assert runtime.decisions[0].checks
    assert runtime.decisions[0].ranking_meta["cap_program"][
        "downstream_constraints"
    ] == list(future_refs)
