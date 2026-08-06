"""Real geometry/planner outcomes behind CaP ``require_future`` calls."""

from __future__ import annotations

import math

import pytest

from demo_graph_lab.execution.compatibility import (
    CandidateInsertionGeometry,
    ContinuationResult,
    InsertionCompatibility,
    InsertionGeometry,
    InsertionTarget,
    MotionPlanningGraspChecks,
    MotionPlanningContinuation,
)
from demo_graph_lab.perception import ObjectObservation, ObservationPacket, Proprioception
from demo_graph_lab.selection.candidates import CandidateBundle, CheckStatus


def _observation() -> ObservationPacket:
    return ObservationPacket(
        observation_id="obs-real-geometry",
        captured_at_s=1.0,
        frame="robot_base",
        calibration_ref="calibration/head.json",
        sensor_refs=("sensor/rgb.npy", "sensor/depth.npy"),
        robot_state=Proprioception(
            joint_positions=(0.0,) * 14,
            gripper_positions=(0.0, 0.0),
            end_effector_frame="robot_base",
            evidence_ref="sensor/proprioception.json",
        ),
        objects=(ObjectObservation(
            object_id="tube_left",
            frame="robot_base",
            axis=(1.0, 0.0, 0.0),
            evidence_refs=("perception/tube_left.json",),
        ),),
    )


def _candidate(candidate_id: str, grasp_x: float) -> CandidateBundle:
    return CandidateBundle(
        candidate_id=candidate_id,
        observation_id="obs-real-geometry",
        hole_values={
            "tube_left_grasp_pose": {
                "value": [grasp_x, 0.0, 0.8, 0.0, 0.0, 0.0, 1.0],
                "frame": "robot_base",
                "calibration_ref": "calibration/head.json",
                "object_id": "tube_left",
            },
        },
        features={"height_fraction": 0.5, "approach_tilt_deg": 0.0},
        evidence_refs=(f"graspnet/{candidate_id}.json",),
    )


def _current_stage() -> dict:
    return {
        "index": 4,
        "name": "transport",
        "stage_objects": {"manipulated": "tube_left", "target": "rack"},
        "holes": [{
            "name": "tube_left_grasp_pose",
            "type": "pose_se3",
            "frame": "robot_base",
            "resolver": "grasp_candidate",
        }],
    }


def _future_stage() -> dict:
    return {
        "index": 5,
        "name": "insertion",
        "stage_objects": {"manipulated": "tube_left", "target": "rack"},
    }


def _geometry(*, object_radius=0.01, opening_radius=0.014) -> InsertionGeometry:
    return InsertionGeometry(
        object_id="tube_left",
        object_center=(0.0, 0.0, 0.8),
        object_axis=(1.0, 0.0, 0.0),
        object_length_m=0.20,
        object_radius_m=object_radius,
        target_center=(0.50, 0.0, 0.75),
        target_axis=(0.0, 0.0, 1.0),
        opening_radius_m=opening_radius,
        insertion_depth_m=0.10,
        preinsert_gap_m=0.04,
        gripper_axial_extent_m=0.025,
        clearance_margin_m=0.002,
        evidence_refs=("geometry/tube-and-left-hole.json", "calibration/gripper.json"),
    )


class RecordingPlanner:
    def __init__(self, status=CheckStatus.PASS):
        self.status = status
        self.requests = []

    def __call__(self, request, candidate, observation):
        self.requests.append((request, candidate.candidate_id, observation.observation_id))
        return ContinuationResult(
            status=self.status,
            reason="recorded_motion_plan",
            evidence_refs=(
                (f"plans/{candidate.candidate_id}.json",)
                if self.status is not CheckStatus.UNKNOWN else ()
            ),
            planning_calls=3 if self.status is CheckStatus.PASS else 0,
        )


def _evaluate(evaluator, candidate, ref, name, args=None):
    return evaluator(
        candidate,
        _observation(),
        _current_stage(),
        _future_stage(),
        ref,
        {"name": name, "args": args or {}},
    )


def test_grasp_is_rigidly_propagated_and_plan_is_cached_across_constraints() -> None:
    planner = RecordingPlanner()
    evaluator = InsertionCompatibility(lambda stage, obs, candidate: _geometry(), planner)
    candidate = _candidate("upper", 0.07)

    axis = _evaluate(
        evaluator, candidate, "s5:c2:axis_parallel", "axis_parallel",
    )
    inside = _evaluate(evaluator, candidate, "s5:c0:inside", "inside")

    assert axis.status is CheckStatus.PASS
    assert inside.status is CheckStatus.PASS
    assert len(planner.requests) == 1
    request = planner.requests[0][0]
    # Current tube +x is aligned to opening +z.  A grasp 7 cm along the tube
    # therefore stays 7 cm above its center at both future poses.
    assert request.preinsert_pose[:3] == (0.5, 0.0, 0.96)
    assert request.inserted_pose[:2] == (0.5, 0.0)
    assert abs(request.inserted_pose[2] - 0.82) < 1e-12
    assert request.grasp_item["item_type"] == "capsule"
    assert request.grasp_item["length"] == 0.20
    assert request.grasp_item["radius"] == 0.01
    assert request.grasp_item["offset_xyz"] == pytest.approx([-0.07, 0.0, 0.0])
    assert request.grasp_item["euler_xyz"] == pytest.approx(
        [0.0, math.pi / 2.0, 0.0])
    assert "planning_calls=3" in axis.reason


def test_inside_fails_from_measured_radius_before_calling_planner() -> None:
    planner = RecordingPlanner()
    evaluator = InsertionCompatibility(
        lambda stage, obs, candidate: _geometry(
            object_radius=0.014, opening_radius=0.014),
        planner,
    )

    result = _evaluate(evaluator, _candidate("wide", 0.07), "s5:c0:inside", "inside")

    assert result.status is CheckStatus.FAIL
    assert "opening_too_narrow" in result.reason
    assert planner.requests == []


def test_gripper_clearance_distinguishes_grasp_locations() -> None:
    planner = RecordingPlanner()
    evaluator = InsertionCompatibility(lambda stage, obs, candidate: _geometry(), planner)

    upper = _evaluate(
        evaluator,
        _candidate("upper", 0.07),
        "s5:c5:clearance",
        "clearance",
        {"obj_a": "gripper", "obj_b": "rack"},
    )
    low = _evaluate(
        evaluator,
        _candidate("low", 0.01),
        "s5:c5:clearance",
        "clearance",
        {"obj_a": "gripper", "obj_b": "rack"},
    )

    assert upper.status is CheckStatus.PASS
    assert low.status is CheckStatus.FAIL
    assert "gripper_intersects_opening_plane" in low.reason
    assert [item[1] for item in planner.requests] == ["upper"]


def test_planner_unknown_is_not_promoted_to_geometry_pass() -> None:
    planner = RecordingPlanner(CheckStatus.UNKNOWN)
    evaluator = InsertionCompatibility(lambda stage, obs, candidate: _geometry(), planner)

    result = _evaluate(
        evaluator, _candidate("candidate", 0.07),
        "s5:c2:axis_parallel", "axis_parallel",
    )

    assert result.status is CheckStatus.UNKNOWN
    assert "geometry/tube-and-left-hole.json" in result.evidence_refs
    assert "recorded_motion_plan" in result.reason


def test_unsupported_downstream_constraint_is_unknown() -> None:
    planner = RecordingPlanner()
    evaluator = InsertionCompatibility(lambda stage, obs, candidate: _geometry(), planner)

    result = _evaluate(
        evaluator, _candidate("candidate", 0.07),
        "s5:c9:carry", "carry",
    )

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason == "unsupported_downstream_constraint:carry"
    assert planner.requests == []


def test_candidate_geometry_provider_joins_real_candidate_and_target_measurements() -> None:
    candidate = CandidateBundle(
        candidate_id="normalized",
        observation_id="obs-real-geometry",
        hole_values=_candidate("source", 0.07).hole_values,
        features={
            "object_center_base": [0.0, 0.0, 0.8],
            "object_axis_base": [1.0, 0.0, 0.0],
            "object_length_m": 0.20,
            "object_radius_m": 0.01,
            "height_fraction": 0.85,
            "approach_tilt_deg": 0.0,
        },
        evidence_refs=("graspnet/normalized.json",),
    )
    provider = CandidateInsertionGeometry(
        {5: InsertionTarget(
            center=(0.5, 0.0, 0.75),
            axis=(0.0, 0.0, 1.0),
            opening_radius_m=0.014,
            insertion_depth_m=0.10,
            preinsert_gap_m=0.04,
            clearance_margin_m=0.002,
            evidence_refs=("perception/left-hole.json", "calibration/rack.json"),
        )},
        gripper_axial_extent_m=0.025,
        gripper_evidence_refs=("calibration/gripper.json",),
    )

    geometry = provider(_future_stage(), _observation(), candidate)

    assert geometry.object_center == (0.0, 0.0, 0.8)
    assert geometry.target_center == (0.5, 0.0, 0.75)
    assert geometry.gripper_axial_extent_m == 0.025
    assert geometry.evidence_refs == (
        "perception/left-hole.json",
        "calibration/rack.json",
        "calibration/gripper.json",
    )


def test_reachability_and_collision_checks_share_one_live_grasp_plan(tmp_path) -> None:
    class Pipe:
        def __init__(self):
            self.calls = []

        def call(self, action, name, kwargs):
            self.calls.append((action, name, dict(kwargs)))
            if action == "info" and name == "get_qpos":
                return [0.0] * 7
            if action == "reasoning" and name == "motion_planning_stereo":
                return ["head"], [0.0] * 14
            raise AssertionError((action, name))

    pipe = Pipe()
    checks = MotionPlanningGraspChecks(
        pipe, arm_id=1, artifact_dir=tmp_path / "plans").checks()
    candidate, observation = _candidate("candidate", 0.07), _observation()

    certificates = [check.evaluate(candidate, observation) for check in checks]

    assert [item.status for item in certificates] == [
        CheckStatus.PASS, CheckStatus.PASS,
    ]
    planning_calls = [call for call in pipe.calls if call[1] == "motion_planning_stereo"]
    assert len(planning_calls) == 1
    assert (tmp_path / "plans" / "obs-real-geometry__candidate__grasp.json").is_file()


def test_continuation_attaches_capsule_only_after_grasp(tmp_path) -> None:
    class Pipe:
        def __init__(self):
            self.calls = []

        def call(self, action, name, kwargs):
            self.calls.append((action, name, dict(kwargs)))
            if action == "info" and name == "get_qpos":
                return [0.0] * 7
            if action == "reasoning" and name == "motion_planning_stereo":
                return ["head"], [0.0] * 7
            raise AssertionError((action, name))

    pipe = Pipe()
    planner = MotionPlanningContinuation(
        pipe, arm_id=1, artifact_dir=tmp_path / "plans")
    evaluator = InsertionCompatibility(
        lambda stage, obs, candidate: _geometry(), planner)

    result = _evaluate(
        evaluator, _candidate("candidate", 0.07),
        "s5:c2:axis_parallel", "axis_parallel",
    )

    assert result.status is CheckStatus.PASS
    planning = [
        kwargs for action, name, kwargs in pipe.calls
        if action == "reasoning" and name == "motion_planning_stereo"
    ]
    assert len(planning) == 3
    assert [kwargs["text"][3] for kwargs in planning] == [
        "mp.scene_input=live",
        "mp.scene_input=cache",
        "mp.scene_input=cache",
    ]
    assert "grasp_item" not in planning[0]
    assert planning[1]["grasp_item"] == planning[2]["grasp_item"]
    assert planning[1]["grasp_item"]["offset_xyz"] == pytest.approx(
        [-0.07, 0.0, 0.0])
