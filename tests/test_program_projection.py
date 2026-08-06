"""Offline tests for optical->base projection, identity acceptance and solve.

The extrinsics are the 2026-08-06 head calibration.  Everything else is a
synthetic frozen record: no camera, no model and no robot is involved, so these
are contract tests for the binding path, not evidence that a real chain ran.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from demo_graph_lab import cli
from demo_graph_lab.execution.planning_runtime import (
    NoFeasibleCandidate,
    OpaqueHandle,
    PlanningOnlyRuntime,
)
from demo_graph_lab.execution.program_projection import (
    accept_identity,
    base_frame_sources,
    project_base_values,
)
from demo_graph_lab.selection.candidates import (
    CheckCertificate,
    CheckStatus,
    HardCheck,
)

from test_frames import REAL_ROTATION, extrinsics_record


_OPTICAL_FRAME = "camera_head_optical"
_OBSERVATION_ID = "head-17-12500000"

# 相机系数值,全部由 8/6 实测 R/t 反算,便于断言 base 侧的物理量:
# - 开口中心反投影自 base [0.60, 0.00, 0.7571](桌面 0.750 之上 7.1mm);
# - 开口轴就是桌面法向在相机系的方向,变换后应当落在 base 竖直上。
_RACK_CENTER_CAM = [0.066498997, -0.085408794, 0.637185719]
_RACK_AXIS_CAM = [-0.000515, -0.687179, -0.726488]
_TUBE_AXIS_CAM = [0.0, 0.0, 1.0]
_Q_LIFT = -0.00642
_TABLE_HEIGHT_M = 0.750

_TUBE_ANCHOR = {
    "object_id": "tube_left", "part": "whole", "instance": None, "selection": None,
}
_RACK_ANCHOR = {
    "object_id": "rack", "part": "hole", "instance": "left", "selection": None,
}

_GRAPH = {
    "task": "insert_tubes",
    "stages": [
        {
            "index": 0,
            "name": "pick",
            "stage_objects": {"manipulated": "tube_left", "target": "rack"},
            "holes": [
                {
                    "name": "tube_long_axis",
                    "type": "axis_3d",
                    "frame": "robot_base",
                    "solver_hint": "long axis of the tube body",
                    "resolver": "principal_axis",
                    "anchor": dict(_TUBE_ANCHOR),
                },
            ],
        },
        {
            "index": 1,
            "name": "insertion",
            "stage_objects": {"manipulated": "tube_left", "target": "rack"},
            "holes": [
                {
                    "name": "rack_hole_center",
                    "type": "point_3d",
                    "frame": "robot_base",
                    "solver_hint": "center of the rack left-hole opening",
                    "resolver": "part_center",
                    "anchor": dict(_RACK_ANCHOR),
                },
                {
                    "name": "rack_hole_axis",
                    "type": "axis_3d",
                    "frame": "robot_base",
                    "solver_hint": "axis of the rack left-hole opening",
                    "resolver": "part_axis",
                    "anchor": dict(_RACK_ANCHOR),
                },
            ],
        },
    ],
}


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _envelope(root: Path, *, value, object_id, program, status="PASS", reason):
    calibration = str((root / "calibration/bundle.json").resolve())
    return {
        "value": value,
        "frame": _OPTICAL_FRAME,
        "calibration_ref": calibration,
        "object_id": object_id,
        "identity_status": "MODEL_PROPOSED",
        "status": status,
        "reason": reason,
        "failed_step": None,
        "evidence_refs": [str((root / f"programs/{program}/geometry/result.json"))],
        "program": program,
        "collides_with": [],
    }


def _summary(*, program, stage, chain, anchor, status="PASS", reason):
    return {
        "program": program,
        "stage": stage,
        "chain": list(chain),
        "anchor": dict(anchor),
        "bbox_pixel": [1, 1, 6, 6],
        "hole_name_rendered_from": "hole",
        "provides": [],
        "status": status,
        "reason": reason,
        "failed_step": None,
        "detail": None,
        "artifact_dir": f"programs/{program}",
        "collides_with": [],
    }


def _record(
    tmp_path: Path,
    *,
    graph=None,
    lift_position_m: float | None = _Q_LIFT,
    holes=None,
    programs=None,
) -> Path:
    """Build one synthetic PROGRAMS_RECORDED directory plus its extrinsics file."""

    root = (tmp_path / "record").resolve()
    graph_path = _write(tmp_path / "graph.json", _GRAPH if graph is None else graph)
    calibration_path = _write(root / "calibration/bundle.json", {
        "schema": "demo_graph_lab.head_intrinsics.v1",
        "camera": "head",
        "frame": _OPTICAL_FRAME,
        "intrinsics": {"width": 8, "height": 6, "fx": 100.0, "fy": 100.0,
                       "cx": 3.5, "cy": 2.5, "baseline": 0.05},
    })
    proprio_path = _write(root / "proprioception.json", {
        "schema": "demo_graph_lab.readonly_proprioception.v2",
        "captured_after_source_frame_id": 17,
        "position_unit": "meter",
        "lift_position_m": lift_position_m,
        "lift_source": (
            "unavailable_no_lift_joint_in_readonly_proprio"
            if lift_position_m is None else "pipeline_info:get_qpos_lift"
        ),
    })
    _write(root / "observation.json", {
        "observation_id": _OBSERVATION_ID,
        "captured_at_s": 12.5,
        "frame": _OPTICAL_FRAME,
        "calibration_ref": str(calibration_path),
        "sensor_refs": [str(calibration_path), str(proprio_path)],
        "robot_state": {
            "joint_positions": [0.0] * 14,
            "gripper_positions": [],
            "end_effector_frame": "robot_base",
            "end_effector_poses": {},
            "evidence_ref": str(proprio_path),
        },
        "objects": [],
    })
    default_holes = {
        "s0.tube_long_axis": _envelope(
            root, value=list(_TUBE_AXIS_CAM), object_id="tube_left",
            program="p0_0", reason="pca_dominant_axis",
        ),
        "s1.rack_hole_center": _envelope(
            root, value=list(_RACK_CENTER_CAM), object_id="rack",
            program="p1_1",
            reason="estimated_from_rgbd_roi_and_local_support_plane",
        ),
        "s1.rack_hole_axis": _envelope(
            root, value=list(_RACK_AXIS_CAM), object_id="rack", program="p1_1",
            reason="estimated_from_rgbd_roi_and_local_support_plane",
        ),
    }
    default_programs = [
        _summary(
            program="p0_0", stage=0,
            chain=["localize", "segment", "crop_points", "fit_axis"],
            anchor=_TUBE_ANCHOR, reason="pca_dominant_axis",
        ),
        _summary(
            program="p1_1", stage=1,
            chain=["localize", "segment", "fit_opening"], anchor=_RACK_ANCHOR,
            reason="estimated_from_rgbd_roi_and_local_support_plane",
        ),
    ]
    _write(root / "program_results.json", {
        "schema": "demo_graph_lab.perception_program_results.v1",
        "observation_id": _OBSERVATION_ID,
        "perception_program_ref": str(tmp_path / "perception_program.json"),
        "graph_ref": str(graph_path),
        "objects_ref": str(tmp_path / "objects.json"),
        "identity_status": "MODEL_PROPOSED",
        "frame": _OPTICAL_FRAME,
        "calibration_ref": str(calibration_path),
        "image_ref": str(root / "programs/observation_input.jpg"),
        "programs": default_programs if programs is None else programs,
        "holes": default_holes if holes is None else holes,
    })
    _write(root / "manifest.json", {
        "schema": "demo_graph_lab.planning_record_manifest.v1",
        "status": "PROGRAMS_RECORDED",
        "source_kind": "recorded_real_readonly",
        "backend_model_enabled": False,
        "execution_enabled": False,
        "created_at": "2026-08-06T00:00:00+00:00",
        "updated_at": "2026-08-06T00:00:00+00:00",
        "observation_id": _OBSERVATION_ID,
        "artifacts": {"program_results": "program_results.json"},
        "normalization_blockers": [],
        "last_error": None,
    })
    _write(tmp_path / "head_extrinsics.json", extrinsics_record())
    return root


def _extrinsics(tmp_path: Path) -> Path:
    return (tmp_path / "head_extrinsics.json").resolve()


def _accept_rack(root: Path) -> None:
    accept_identity(
        root,
        program="p1_1",
        object_id="rack",
        accepted_by="wenqian",
        basis="hand-checked programs/p1_1/grounding/result.json against the rack",
    )


def _angle_deg(vector, reference) -> float:
    dot = sum(first * second for first, second in zip(vector, reference))
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(dot)))))


def test_projection_applies_the_lift_correction_and_names_the_extrinsics(
    tmp_path,
) -> None:
    root = _record(tmp_path)

    document = project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    center = document["holes"]["s1.rack_hole_center"]
    assert document["frame"] == "robot_base"
    assert document["q_lift"] == _Q_LIFT
    assert document["q_lift_source"] == "pipeline_info:get_qpos_lift"
    assert center["status"] == "PASS"
    assert center["frame"] == "robot_base"
    # 变换后的有效性由外参决定,所以 calibration_ref 换成外参记录,内参留在旁边。
    assert center["calibration_ref"] == str(_extrinsics(tmp_path))
    assert center["source_frame"] == _OPTICAL_FRAME
    assert center["source_value"] == _RACK_CENTER_CAM
    assert center["source_calibration_ref"].endswith("calibration/bundle.json")
    # 6.42mm 的升降修正把桌高残差从 7.1mm 拉回 0.69mm。
    assert abs(center["value"][2] - _TABLE_HEIGHT_M - 0.00069) < 1e-4
    assert json.loads(
        (root / "base_frame_values.json").read_text()
    )["holes"]["s1.rack_hole_center"] == center
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "BASE_VALUES_PROJECTED"
    assert manifest["artifacts"]["base_frame_values"] == "base_frame_values.json"


def test_axis_projection_lands_on_base_vertical_and_needs_no_lift(tmp_path) -> None:
    """方向只吃 R,所以升降读数缺失时 axis 照样可用,point 才必须拒绝。"""

    root = _record(tmp_path, lift_position_m=None)

    document = project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    axis = document["holes"]["s1.rack_hole_axis"]
    center = document["holes"]["s1.rack_hole_center"]
    assert axis["status"] == "PASS"
    assert axis["reason"] == "rotated_without_translation"
    assert _angle_deg(axis["value"], (0.0, 0.0, 1.0)) < 0.1
    assert math.isclose(
        math.sqrt(sum(item * item for item in axis["value"])), 1.0, abs_tol=1e-9
    )
    assert center["status"] == "UNKNOWN"
    assert center["value"] is None
    assert center["reason"] == "q_lift_unavailable"
    assert document["q_lift_source"] == "unavailable_no_lift_joint_in_readonly_proprio"


def test_tube_axis_uses_rotation_only(tmp_path) -> None:
    root = _record(tmp_path)

    document = project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    axis = document["holes"]["s0.tube_long_axis"]
    # 相机系 +z 在 base 系就是 R 的第三列;加上 t 会得到完全不同的向量。
    expected = [row[2] for row in REAL_ROTATION]
    assert all(
        abs(first - second) < 1e-6
        for first, second in zip(axis["value"], expected)
    )


def test_upstream_refusal_reason_survives_the_projection(tmp_path) -> None:
    root = _record(tmp_path)
    results = json.loads((root / "program_results.json").read_text())
    results["holes"]["s1.rack_hole_center"].update({
        "value": None,
        "status": "UNKNOWN",
        "reason": "grounding_identity_collision",
        "failed_step": "localize",
    })
    _write(root / "program_results.json", results)

    document = project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    center = document["holes"]["s1.rack_hole_center"]
    assert center["status"] == "UNKNOWN"
    assert center["value"] is None
    # 上游的理由更具体,投影不能用一句泛化的"无法变换"把它盖掉。
    assert center["reason"] == "grounding_identity_collision"


def test_point_hole_refuses_a_point_cloud_derived_value(tmp_path) -> None:
    """点云质心不是部件中心:8/6 实测它比实体中心偏向相机约一个半径。"""

    graph = json.loads(json.dumps(_GRAPH))
    graph["stages"][1]["holes"][0]["resolver"] = "principal_axis"
    root = _record(tmp_path, graph=graph)
    results = json.loads((root / "program_results.json").read_text())
    results["programs"][1]["chain"] = [
        "localize", "segment", "crop_points", "fit_axis"
    ]
    _write(root / "program_results.json", results)

    document = project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    center = document["holes"]["s1.rack_hole_center"]
    assert center["status"] == "UNKNOWN"
    assert center["reason"] == "resolver_may_not_fill_point_3d:principal_axis"


def test_chain_terminal_must_match_the_declared_resolver(tmp_path) -> None:
    root = _record(tmp_path)
    results = json.loads((root / "program_results.json").read_text())
    results["programs"][1]["chain"] = [
        "localize", "segment", "crop_points", "fit_axis"
    ]
    _write(root / "program_results.json", results)

    document = project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    for name in ("s1.rack_hole_center", "s1.rack_hole_axis"):
        envelope = document["holes"][name]
        assert envelope["status"] == "UNKNOWN"
        assert envelope["reason"] == (
            "chain_terminal_does_not_match_resolver:fit_axis"
        )


def test_grasp_pose_holes_stay_out_of_this_projection(tmp_path) -> None:
    """pose_se3 走候选身份与工具变换那条路,不在本轮的投影里。"""

    graph = json.loads(json.dumps(_GRAPH))
    graph["stages"][0]["holes"][0].update({
        "name": "tube_grasp_pose",
        "type": "pose_se3",
        "resolver": "grasp_candidate",
    })
    root = _record(tmp_path, graph=graph)
    results = json.loads((root / "program_results.json").read_text())
    envelope = results["holes"].pop("s0.tube_long_axis")
    envelope["value"] = [0.1, 0.2, 0.6, 0.0, 0.0, 0.0, 1.0]
    results["holes"]["s0.tube_grasp_pose"] = envelope
    _write(root / "program_results.json", results)

    document = project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    pose = document["holes"]["s0.tube_grasp_pose"]
    assert pose["status"] == "UNKNOWN"
    assert pose["reason"] == "hole_type_not_projected:pose_se3"


def test_projected_values_are_not_candidates_without_an_acceptance(tmp_path) -> None:
    root = _record(tmp_path)
    project_base_values(root, extrinsics_path=_extrinsics(tmp_path))

    sources = base_frame_sources(root)

    assert all(
        envelope["identity_status"] == "MODEL_PROPOSED"
        and envelope["identity_accepted"] is False
        for envelope in sources.document["holes"].values()
    )
    assert sources.observation.objects == ()
    assert sources.candidate_provider(
        _GRAPH["stages"][1], sources.observation
    ) == ()


def test_identity_acceptance_records_who_accepted_and_on_what_basis(
    tmp_path,
) -> None:
    root = _record(tmp_path)
    _accept_rack(root)

    project_base_values(root, extrinsics_path=_extrinsics(tmp_path))
    acceptance = json.loads((root / "identity_acceptance.json").read_text())
    sources = base_frame_sources(root)

    entry = acceptance["acceptances"][0]
    assert acceptance["observation_id"] == _OBSERVATION_ID
    assert entry["program"] == "p1_1"
    assert entry["object_id"] == "rack"
    assert entry["accepted_by"] == "wenqian"
    assert entry["basis"].startswith("hand-checked")
    assert entry["bbox_pixel"] == [1, 1, 6, 6]
    # 只有被接受的洞进候选;同一次观测里没被接受的程序原样留在 MODEL_PROPOSED。
    assert sources.document["holes"]["s1.rack_hole_axis"]["identity_accepted"] is True
    assert sources.document["holes"]["s0.tube_long_axis"]["identity_accepted"] is False
    assert [item.object_id for item in sources.observation.objects] == ["rack"]


def test_acceptance_must_match_the_program_anchor(tmp_path) -> None:
    root = _record(tmp_path)

    with pytest.raises(ValueError, match="anchored on"):
        accept_identity(
            root, program="p1_1", object_id="tube_left",
            accepted_by="wenqian", basis="mismatched on purpose",
        )
    assert not (root / "identity_acceptance.json").exists()


def test_acceptance_cannot_revive_a_refused_program(tmp_path) -> None:
    root = _record(tmp_path)
    results = json.loads((root / "program_results.json").read_text())
    results["programs"][1].update({
        "status": "UNKNOWN", "reason": "grounding_identity_collision",
    })
    _write(root / "program_results.json", results)

    with pytest.raises(ValueError, match="cannot revive"):
        _accept_rack(root)


def test_repeated_acceptance_of_one_program_is_refused(tmp_path) -> None:
    root = _record(tmp_path)
    _accept_rack(root)

    with pytest.raises(ValueError, match="already has an acceptance"):
        _accept_rack(root)


def _pass_checks() -> tuple[HardCheck, ...]:
    """Stub physical checks.

    Real reachability/collision/gripper-width adapters are not connected; these
    exist so the typed-binding path can be exercised offline and prove nothing
    about physical feasibility.
    """

    def evaluate(name):
        def check(candidate, observation):
            return CheckCertificate(
                check=name,
                status=CheckStatus.PASS,
                reason="offline_stub_check",
                evidence_refs=(observation.observation_id,),
            )
        return check

    return tuple(
        HardCheck(name=name, evaluate=evaluate(name))
        for name in ("reachability", "collision_free", "gripper_width")
    )


def _runtime(root: Path, tmp_path: Path, sources) -> PlanningOnlyRuntime:
    return PlanningOnlyRuntime(
        _GRAPH,
        sources.observation_provider,
        sources.candidate_provider,
        _pass_checks(),
        tmp_path / "decisions.jsonl",
    )


def test_accepted_base_values_bind_and_solve_in_the_planning_runtime(
    tmp_path,
) -> None:
    """从冻结记录一路走到 solve():洞的数值在 base 系,身份有人签过字。"""

    root = _record(tmp_path)
    _accept_rack(root)
    project_base_values(root, extrinsics_path=_extrinsics(tmp_path))
    sources = base_frame_sources(root)
    runtime = _runtime(root, tmp_path, sources)

    runtime.begin_stage(_GRAPH["stages"][1])
    center = runtime.solve("rack_hole_center")
    axis = runtime.solve("rack_hole_axis")

    assert isinstance(center, OpaqueHandle) and isinstance(axis, OpaqueHandle)
    assert center is not axis
    decision = json.loads((tmp_path / "decisions.jsonl").read_text().splitlines()[-1])
    assert decision["status"] == "SELECTED"
    assert decision["observation"]["frame"] == "robot_base"
    candidate = decision["candidates"][0]["candidate"]
    binding = next(
        item for item in decision["candidates"][0]["certificates"]
        if item["check"] == "typed_hole_values"
    )
    assert binding["status"] == "PASS"
    values = candidate["hole_values"]
    # 洞的值形状是闭集,身份状态只能待在 provenance 里。
    assert set(values["rack_hole_center"]) == {
        "value", "frame", "calibration_ref", "object_id"
    }
    assert values["rack_hole_center"]["frame"] == "robot_base"
    assert values["rack_hole_center"]["object_id"] == "rack"
    assert abs(values["rack_hole_center"]["value"][2] - 0.75069) < 1e-4
    assert _angle_deg(values["rack_hole_axis"]["value"], (0.0, 0.0, 1.0)) < 0.1
    assert candidate["provenance"]["identity_status"] == "MODEL_PROPOSED"
    assert candidate["provenance"]["q_lift_m"] == _Q_LIFT
    assert candidate["provenance"]["identity_acceptance_ref"].endswith(
        "identity_acceptance.json"
    )


def test_unaccepted_stage_gets_no_candidate_and_fails_closed(tmp_path) -> None:
    root = _record(tmp_path)
    _accept_rack(root)
    project_base_values(root, extrinsics_path=_extrinsics(tmp_path))
    sources = base_frame_sources(root)
    runtime = _runtime(root, tmp_path, sources)

    # stage 0 的管子身份没有人接受过,所以那个洞根本不产出候选。
    with pytest.raises(NoFeasibleCandidate):
        runtime.begin_stage(_GRAPH["stages"][0])
    with pytest.raises(NoFeasibleCandidate):
        runtime.solve("tube_long_axis")


def test_unknown_value_propagates_to_a_missing_required_binding(tmp_path) -> None:
    root = _record(tmp_path, lift_position_m=None)
    _accept_rack(root)
    project_base_values(root, extrinsics_path=_extrinsics(tmp_path))
    sources = base_frame_sources(root)
    runtime = _runtime(root, tmp_path, sources)

    with pytest.raises(NoFeasibleCandidate):
        runtime.begin_stage(_GRAPH["stages"][1])

    decision = json.loads((tmp_path / "decisions.jsonl").read_text().splitlines()[-1])
    binding = next(
        item for item in decision["candidates"][0]["certificates"]
        if item["check"] == "typed_hole_values"
    )
    assert decision["status"] == "NO_FEASIBLE_CANDIDATE"
    assert "rack_hole_center:missing_required_value" in binding["reason"]
    # typed-hole 校验没过时物理 checker 一个都不许跑。
    assert all(
        item["status"] == "UNKNOWN"
        and item["reason"].startswith("not_run:typed_hole_values_")
        for item in decision["candidates"][0]["certificates"]
        if item["check"] != "typed_hole_values"
    )


def test_cli_runs_identity_accept_and_project_base_locally(tmp_path) -> None:
    root = _record(tmp_path)

    assert cli.main([
        "planning-record", "--record-dir", str(root),
        "--step", "identity-accept",
        "--program", "p1_1", "--object-id", "rack",
        "--accepted-by", "wenqian", "--basis", "checked the frozen box by hand",
    ]) == 0
    assert cli.main([
        "planning-record", "--record-dir", str(root),
        "--step", "project-base", "--extrinsics", str(_extrinsics(tmp_path)),
    ]) == 0

    document = json.loads((root / "base_frame_values.json").read_text())
    assert document["holes"]["s1.rack_hole_center"]["identity_accepted"] is True
    assert document["holes"]["s1.rack_hole_center"]["status"] == "PASS"
