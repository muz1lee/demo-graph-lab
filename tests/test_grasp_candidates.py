"""GraspNet grasp-frame to runtime-EEF candidate conversion."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from demo_graph_lab.execution.grasp_candidates import (
    GraspToolTransform,
    calibrated_gripper_width_check,
    normalize_graspnet_candidates,
    validate_tool_transform_record,
)
from demo_graph_lab.perception import ObservationPacket, Proprioception
from demo_graph_lab.perception.frames import CameraExtrinsics
from demo_graph_lab.selection.candidates import CheckStatus


def _observation() -> ObservationPacket:
    return ObservationPacket(
        observation_id="obs-grasp",
        captured_at_s=1.0,
        frame="camera_head_optical",
        calibration_ref="calibration/intrinsics.json",
        sensor_refs=("cloud/object.npz", "cloud/manifest.json"),
        robot_state=Proprioception(
            joint_positions=(0.0,) * 14,
            gripper_positions=(0.0, 0.0),
            end_effector_frame="robot_base",
            evidence_ref="sensor/proprio.json",
        ),
    )


def _manifest() -> dict:
    return {
        "artifact_ref": "cloud/object.npz",
        "unit": "meter",
        "frame": "camera_head_optical",
        "calibration_ref": "calibration/intrinsics.json",
        "evidence_ref": "cloud/manifest.json",
    }


def _response(*, width=0.03, translation=(0.1, 0.0, 0.5)) -> dict:
    rotation = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    raw = [0.9, width, 0.02, 0.04, *(x for row in rotation for x in row),
           *translation, -1.0]
    return {
        "ok": True,
        "schema": "kw_independent.graspnet.raw_response.v1",
        "backend": "graspnet_baseline",
        "checkpoint_path": "weights/checkpoint.tar",
        "checkpoint_epoch": 10,
        "coordinate_frame": "camera_head_optical",
        "grasps": [{
            "raw_index": 2,
            "score": 0.9,
            "width": width,
            "height": 0.02,
            "depth": 0.04,
            "rotation_matrix": rotation,
            "translation": list(translation),
            "object_id": -1,
            "coordinate_frame": "camera_head_optical",
            "raw_grasp_array": raw,
        }],
        "input_reference": {
            "image_path": None,
            "depth_path": None,
            "mask_path": None,
            "point_cloud_path": "cloud/object.npz",
            "object_hint": None,
            "frame_id": "obs-grasp",
            "coordinate_frame": "camera_head_optical",
            "camera_intrinsics": None,
            "extra": {},
        },
    }


def _stage() -> dict:
    return {
        "index": 4,
        "name": "transport",
        "stage_objects": {"manipulated": "tube_left", "target": "rack"},
        "holes": [
            {
                "name": "tube_left_grasp_pose",
                "type": "pose_se3",
                "frame": "robot_base",
                "resolver": "grasp_candidate",
                "anchor": {"object_id": "tube_left", "part": "whole"},
            },
            {
                "name": "tube_left_long_axis",
                "type": "axis_3d",
                "frame": "robot_base",
                "resolver": "principal_axis",
                "anchor": {"object_id": "tube_left", "part": "whole"},
            },
        ],
    }


def _extrinsics() -> CameraExtrinsics:
    return CameraExtrinsics(
        rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation=(0.4, 0.0, 0.2),
        frame_from="camera_head_optical",
        frame_to="robot_base",
        lift_axis_base=(0.0, 0.0, 1.0),
        lift_limits_m=(0.0, 0.5),
        q_lift_assumed=0.0,
        lift_correction="translate_base_origin",
        record={},
        ref="calibration/extrinsics.json",
    )


def _tool() -> GraspToolTransform:
    return GraspToolTransform(
        rotation_graspnet_from_ee=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_graspnet_from_ee_m=(0.02, 0.0, 0.0),
        max_opening_m=0.08,
        axial_extent_m=0.03,
        evidence_refs=("calibration/tool-validation.json",),
        ref="calibration/tool.json",
    )


def _points():
    # Long tube along camera +x; small non-degenerate elliptical cross section.
    return np.array([
        [x, y, z]
        for x in np.linspace(0.0, 0.2, 21)
        for y, z in ((-0.01, 0.49), (0.01, 0.49), (-0.01, 0.51), (0.01, 0.51))
    ], dtype=np.float32)


def test_tool_transform_record_names_pose_direction_explicitly() -> None:
    transform = validate_tool_transform_record({
        "schema": "demo_graph_lab.graspnet_runtime_ee_transform.v1",
        "runtime_ee_in_graspnet": {
            "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation_m": [0.02, 0.0, 0.0],
        },
        "gripper": {"max_opening_m": 0.08, "axial_extent_m": 0.03},
        "evidence_refs": ["calibration/tool-validation.json"],
    })

    assert transform.translation_graspnet_from_ee_m == (0.02, 0.0, 0.0)
    assert transform.max_opening_m == 0.08


def test_raw_grasp_becomes_robot_base_eef_pose_and_geometry_features() -> None:
    result = normalize_graspnet_candidates(
        _response(),
        observation=_observation(),
        point_cloud_manifest=_manifest(),
        object_points=_points(),
        stage=_stage(),
        object_id="tube_left",
        identity_accepted=True,
        identity_evidence_ref="review/identity.json",
        camera_extrinsics=_extrinsics(),
        q_lift=0.1,
        tool_transform=_tool(),
        raw_response_ref="graspnet/raw.json",
    )

    assert result.observation.frame == "robot_base"
    assert result.observation.calibration_ref == "calibration/extrinsics.json"
    assert result.object_axis == pytest.approx((1.0, 0.0, 0.0))
    assert result.object_length_m == pytest.approx(0.2)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    pose = candidate.hole_values["tube_left_grasp_pose"]["value"]
    # p_B_E = t_B_C + lift + p_C_G + R_C_G * t_G_E.
    assert pose[:3] == pytest.approx((0.52, 0.0, 0.8))
    assert pose[3:] == pytest.approx((0.0, 0.0, 0.0, 1.0))
    assert candidate.hole_values["tube_left_long_axis"]["value"] == pytest.approx(
        (1.0, 0.0, 0.0))
    assert candidate.features["height_fraction"] == pytest.approx(0.5)
    assert candidate.features["approach_tilt_deg"] == pytest.approx(90.0)
    assert candidate.provenance["raw_object_id"] == -1
    assert candidate.provenance["graph_object_id"] == "tube_left"


def test_height_fraction_uses_world_vertical_not_object_long_axis() -> None:
    result = normalize_graspnet_candidates(
        _response(translation=(0.18, 0.0, 0.5)),
        observation=_observation(), point_cloud_manifest=_manifest(),
        object_points=_points(), stage=_stage(), object_id="tube_left",
        identity_accepted=True, identity_evidence_ref="review/identity.json",
        camera_extrinsics=_extrinsics(), q_lift=0.1, tool_transform=_tool(),
        raw_response_ref="graspnet/raw.json",
    )

    candidate = result.candidates[0]
    assert candidate.features["height_fraction"] == pytest.approx(0.5)
    assert candidate.features["grasp_center_base"] == pytest.approx(
        [0.58, 0.0, 0.8])


def test_candidate_conversion_refuses_unaccepted_identity_and_unreferenced_tool() -> None:
    kwargs = dict(
        observation=_observation(), point_cloud_manifest=_manifest(),
        object_points=_points(), stage=_stage(), object_id="tube_left",
        identity_evidence_ref="review/identity.json", camera_extrinsics=_extrinsics(),
        q_lift=0.1, tool_transform=_tool(), raw_response_ref="graspnet/raw.json",
    )
    with pytest.raises(PermissionError, match="identity"):
        normalize_graspnet_candidates(_response(), identity_accepted=False, **kwargs)

    kwargs["tool_transform"] = replace(_tool(), ref="")
    with pytest.raises(ValueError, match="referenced"):
        normalize_graspnet_candidates(_response(), identity_accepted=True, **kwargs)


def test_calibrated_width_checker_uses_detector_meters() -> None:
    result = normalize_graspnet_candidates(
        _response(width=0.09), observation=_observation(),
        point_cloud_manifest=_manifest(), object_points=_points(), stage=_stage(),
        object_id="tube_left", identity_accepted=True,
        identity_evidence_ref="review/identity.json", camera_extrinsics=_extrinsics(),
        q_lift=0.1, tool_transform=_tool(), raw_response_ref="graspnet/raw.json",
    )
    certificate = calibrated_gripper_width_check(_tool()).evaluate(
        result.candidates[0], result.observation)

    assert certificate.status is CheckStatus.FAIL
    assert certificate.reason == "calibrated_opening_margin_m=-0.010000"
