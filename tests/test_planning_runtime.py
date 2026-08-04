"""Offline checks for the non-privileged planning-only online scaffold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo_graph_lab.execution.planning_runtime import (
    ExecutionDisabled,
    NoFeasibleCandidate,
    OpaqueHandle,
    PlanningOnlyRuntime,
)
from demo_graph_lab.perception import ObjectObservation, ObservationPacket, Proprioception
from demo_graph_lab.selection.candidates import (
    CandidateBundle,
    CheckCertificate,
    CheckStatus,
    HardCheck,
    deterministic_select,
    hard_filter,
)


def _observation() -> ObservationPacket:
    return ObservationPacket(
        observation_id="obs-001",
        captured_at_s=10.0,
        frame="robot_base",
        calibration_ref="calibration/run-1.json",
        sensor_refs=("rgb/frame-10.png", "depth/frame-10.npy"),
        robot_state=Proprioception(
            joint_positions=(0.0,) * 7,
            end_effector_frame="robot_base",
            gripper_positions=(0.0,),
            evidence_ref="proprioception/frame-10.json",
        ),
        objects=(
            ObjectObservation(
                object_id="object",
                frame="robot_base",
                pose=(0.4, 0.1, 0.8, 0.0, 0.0, 0.0, 1.0),
                axis=(0.0, 0.0, 1.0),
                evidence_refs=("objects/object.json",),
            ),
        ),
    )


def _candidate(
    candidate_id: str,
    *,
    height: float = 0.5,
    approach_tilt_deg: float = 0.0,
) -> CandidateBundle:
    return CandidateBundle(
        candidate_id=candidate_id,
        observation_id="obs-001",
        hole_values={
            "grasp_pose": {
                "value": [0.4, 0.1, 0.8, 0.0, 0.0, 0.0, 1.0],
                "frame": "robot_base",
                "calibration_ref": "calibration/run-1.json",
                "object_id": "object",
            },
            "object_axis": {
                "value": [0.0, 0.0, 1.0],
                "frame": "robot_base",
                "calibration_ref": "calibration/run-1.json",
                "object_id": "object",
            },
        },
        features={
            "height_fraction": height,
            "approach_tilt_deg": approach_tilt_deg,
        },
        evidence_refs=(f"candidate_cards/{candidate_id}.json",),
    )


def _check(name: str, statuses: dict[str, CheckStatus]) -> HardCheck:
    def evaluate(candidate, observation):
        status = statuses.get(candidate.candidate_id, CheckStatus.PASS)
        reasons = {
            CheckStatus.PASS: "checked",
            CheckStatus.FAIL: f"{name}_failed",
            CheckStatus.UNKNOWN: f"{name}_unavailable",
        }
        return CheckCertificate(
            check=name,
            status=status,
            reason=reasons[status],
            evidence_refs=(observation.observation_id,),
        )

    return HardCheck(name=name, evaluate=evaluate)


def _all_pass_checks() -> tuple[HardCheck, ...]:
    return tuple(
        _check(name, {})
        for name in ("reachability", "collision_free", "gripper_width")
    )


def test_hard_filter_records_fail_and_unknown_reasons() -> None:
    candidates = [_candidate("ok"), _candidate("blocked"), _candidate("unknown")]
    checks = (
        _check("reachability", {}),
        _check("collision_free", {"blocked": CheckStatus.FAIL}),
        _check("gripper_width", {"unknown": CheckStatus.UNKNOWN}),
    )

    result = hard_filter(candidates, _observation(), checks)

    assert [item.candidate_id for item in result.accepted] == ["ok"]
    traces = {item.candidate.candidate_id: item for item in result.traces}
    blocked = {item.check: item for item in traces["blocked"].certificates}
    unknown = {item.check: item for item in traces["unknown"].certificates}
    assert blocked["collision_free"].status is CheckStatus.FAIL
    assert blocked["collision_free"].reason == "collision_free_failed"
    assert unknown["gripper_width"].status is CheckStatus.UNKNOWN
    assert unknown["gripper_width"].reason == "gripper_width_unavailable"
    assert traces["blocked"].accepted is False
    assert traces["unknown"].accepted is False


def test_missing_required_check_is_unknown_and_fail_closed() -> None:
    result = hard_filter(
        [_candidate("candidate")],
        _observation(),
        (_check("reachability", {}), _check("collision_free", {})),
    )

    assert result.accepted == ()
    certificates = {item.check: item for item in result.traces[0].certificates}
    assert certificates["gripper_width"].status is CheckStatus.UNKNOWN
    assert certificates["gripper_width"].reason == "check_not_configured"


def test_deterministic_selection_uses_region_then_cone_then_id() -> None:
    candidates = [
        _candidate("a-side", height=0.8, approach_tilt_deg=90.0),
        _candidate("z-down", height=0.8, approach_tilt_deg=0.0),
        _candidate("b-low", height=0.2, approach_tilt_deg=0.0),
    ]

    result = deterministic_select(candidates, region="upper_body", cone="top_down")

    assert [item.candidate_id for item in result.ranked] == [
        "z-down",
        "a-side",
        "b-low",
    ]
    assert result.selected is not None
    assert result.selected.candidate_id == "z-down"


def _graph() -> dict:
    return {
        "stages": [
            {
                "index": 0,
                "name": "grasp",
                "stage_objects": {"manipulated": "object", "target": None},
                "holes": [
                    {
                        "name": "grasp_pose",
                        "type": "pose_se3",
                        "frame": "robot_base",
                    },
                    {
                        "name": "object_axis",
                        "type": "axis_3d",
                        "frame": "robot_base",
                    },
                ],
                "constraints": [
                    {
                        "name": "region_grasp",
                        "args": {"obj": "object", "region": "upper_body"},
                    },
                    {"name": "approach_direction", "args": {"cone": "top_down"}},
                ],
            }
        ]
    }


def _runtime(tmp_path: Path, candidates) -> PlanningOnlyRuntime:
    return PlanningOnlyRuntime(
        _graph(),
        observation_provider=lambda stage: _observation(),
        candidate_provider=lambda stage, observation: candidates,
        hard_checks=_all_pass_checks(),
        decision_log_path=tmp_path / "online_decisions.jsonl",
    )


def test_planning_runtime_returns_opaque_handle_and_jsonl_trace(tmp_path: Path) -> None:
    candidates = [
        _candidate("a-side", height=0.8, approach_tilt_deg=90.0),
        _candidate("z-down", height=0.8, approach_tilt_deg=0.0),
    ]
    runtime = _runtime(tmp_path, candidates)

    runtime.begin_stage(_graph()["stages"][0])
    handle = runtime.solve("grasp_pose")

    assert isinstance(handle, OpaqueHandle)
    assert runtime.solve("grasp_pose") is handle
    assert repr(handle) == "<opaque-handle>"
    assert not hasattr(handle, "xyz")
    assert not hasattr(handle, "candidate_id")
    assert not hasattr(handle, "__dict__")
    assert runtime.backend_model_enabled is False
    assert runtime.execution_enabled is False

    records = [
        json.loads(line)
        for line in (tmp_path / "online_decisions.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["observation"]["observation_id"] == "obs-001"
    assert records[0]["observation"]["calibration_ref"] == "calibration/run-1.json"
    assert records[0]["ranking_meta"]["region"]["status"] == "ranked"
    assert records[0]["ranking"] == ["z-down", "a-side"]
    assert records[0]["selected_candidate_id"] == "z-down"
    assert records[0]["candidates"][0]["certificates"]


def test_stage_program_wiring_defines_required_candidate_holes(tmp_path: Path) -> None:
    full = _candidate("pose-only")
    pose_only = CandidateBundle(
        candidate_id=full.candidate_id,
        observation_id=full.observation_id,
        hole_values={"grasp_pose": full.hole_values["grasp_pose"]},
        features=full.features,
        evidence_refs=full.evidence_refs,
    )
    program = {
        "stages": [
            {
                "index": 0,
                "name": "grasp",
                "actions": [
                    {
                        "op": "grasp_at",
                        "args": {"grasp_pose": {"hole": "grasp_pose"}},
                    }
                ],
            }
        ]
    }
    runtime = PlanningOnlyRuntime(
        _graph(),
        observation_provider=lambda stage: _observation(),
        candidate_provider=lambda stage, observation: (pose_only,),
        hard_checks=_all_pass_checks(),
        decision_log_path=tmp_path / "online_decisions.jsonl",
        stage_program=program,
    )

    runtime.begin_stage(_graph()["stages"][0])
    assert isinstance(runtime.solve("grasp_pose"), OpaqueHandle)


def test_no_candidate_logs_decision_then_stops(tmp_path: Path) -> None:
    runtime = PlanningOnlyRuntime(
        _graph(),
        observation_provider=lambda stage: _observation(),
        candidate_provider=lambda stage, observation: [_candidate("unknown")],
        hard_checks=(
            _check("reachability", {}),
            _check("collision_free", {}),
            _check("gripper_width", {"unknown": CheckStatus.UNKNOWN}),
        ),
        decision_log_path=tmp_path / "online_decisions.jsonl",
    )

    with pytest.raises(NoFeasibleCandidate, match="no candidate"):
        runtime.begin_stage(_graph()["stages"][0])

    record = json.loads((tmp_path / "online_decisions.jsonl").read_text("utf-8"))
    assert record["selected_candidate_id"] is None
    assert record["status"] == "NO_FEASIBLE_CANDIDATE"
    assert record["ranking"] == []
    assert record["candidates"][0]["accepted"] is False


def test_failed_new_stage_clears_previous_selection(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, [_candidate("candidate")])
    stage = _graph()["stages"][0]
    runtime.begin_stage(stage)
    assert isinstance(runtime.solve("grasp_pose"), OpaqueHandle)

    def fail_observation(_stage):
        raise RuntimeError("sensor unavailable")

    runtime._observation_provider = fail_observation
    with pytest.raises(RuntimeError, match="sensor unavailable"):
        runtime.begin_stage(stage)
    with pytest.raises(NoFeasibleCandidate, match="begin_stage"):
        runtime.solve("grasp_pose")


def test_observation_rejects_untyped_robot_state() -> None:
    with pytest.raises(TypeError, match="Proprioception"):
        ObservationPacket(
            observation_id="obs-privileged",
            captured_at_s=10.0,
            frame="robot_base",
            calibration_ref="calibration/run-1.json",
            sensor_refs=("rgb/frame-10.png",),
            robot_state={"task_success": True},
        )


def test_candidate_data_is_immutable_finite_and_json_safe() -> None:
    candidate = _candidate("stable")
    with pytest.raises(TypeError):
        candidate.features["height_fraction"] = 0.0
    with pytest.raises(TypeError, match="non-JSON value"):
        CandidateBundle(
            candidate_id="bad",
            observation_id="obs-001",
            hole_values={"pose": object()},
        )
    with pytest.raises(ValueError, match="NaN"):
        CandidateBundle(
            candidate_id="nan",
            observation_id="obs-001",
            hole_values={"score": float("nan")},
        )
    with pytest.raises(TypeError, match="boolean"):
        CandidateBundle(
            candidate_id="bool",
            observation_id="obs-001",
            hole_values={},
            features={"reachable": True},
            evidence_refs=("candidate_cards/bool.json",),
        )
    json.dumps(candidate.to_record(), allow_nan=False)


def test_null_hole_value_is_rejected_before_physical_checks(tmp_path: Path) -> None:
    candidate = CandidateBundle(
        candidate_id="null-pose",
        observation_id="obs-001",
        hole_values={
            "grasp_pose": None,
            "object_axis": {
                "value": [0.0, 0.0, 1.0],
                "frame": "robot_base",
                "calibration_ref": "calibration/run-1.json",
                "object_id": "object",
            },
        },
        evidence_refs=("candidate_cards/null-pose.json",),
    )
    runtime = _runtime(tmp_path, [candidate])
    with pytest.raises(NoFeasibleCandidate, match="no candidate"):
        runtime.begin_stage(_graph()["stages"][0])
    record = json.loads((tmp_path / "online_decisions.jsonl").read_text("utf-8"))
    checks = {
        item["check"]: item
        for item in record["candidates"][0]["certificates"]
    }
    assert checks["typed_hole_values"]["status"] == "FAIL"
    assert checks["reachability"]["reason"].startswith("not_run:")


def test_every_control_primitive_is_explicitly_disabled(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, [_candidate("candidate")])
    runtime.begin_stage(_graph()["stages"][0])
    handle = runtime.solve("grasp_pose")
    calls = [
        (runtime.approach, (handle,), {}),
        (runtime.grasp_at, (handle,), {}),
        (runtime.lift, ("object",), {}),
        (runtime.transport, ("object", handle), {}),
        (runtime.align, ("object", handle), {}),
        (runtime.lower_until, (handle,), {}),
        (runtime.release, (), {}),
        (runtime.retreat, (handle,), {}),
    ]

    for primitive, args, kwargs in calls:
        with pytest.raises(ExecutionDisabled, match="stops before execution"):
            primitive(*args, **kwargs)


def test_online_scaffold_has_no_backend_or_execution_dependency() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "demo_graph_lab"
    sources = [
        root / "perception" / "observations.py",
        root / "selection" / "candidates.py",
        root / "execution" / "planning_runtime.py",
    ]
    joined = "\n".join(path.read_text("utf-8") for path in sources)
    forbidden = (
        "common.llm",
        "openai",
        "oracle_runtime",
        "PipelineClient",
        "robot_api",
        '"/state"',
        "'/state'",
    )
    for token in forbidden:
        assert token not in joined
