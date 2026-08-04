"""Offline tests for strict observation and candidate record adapters."""

from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from demo_graph_lab.perception import (
    ObjectObservation,
    ObservationPacket,
    Proprioception,
)
from demo_graph_lab.perception.adapters import (
    candidate_from_record,
    graspnet_candidates_from_response,
    observation_from_record,
)


def _observation_record() -> dict:
    return {
        "observation_id": "obs-head-0001",
        "captured_at_s": 12.5,
        "frame": "camera_head",
        "calibration_ref": "calibration/head.json",
        "sensor_refs": [
            "rgb/head-0001.npy",
            "depth/head-0001.npy",
            "pointcloud/head-0001.npz",
            "pointcloud/head-0001.manifest.json",
            "transforms/graspnet-to-runtime-ee.json",
        ],
        "robot_state": {
            "joint_positions": [0.0] * 14,
            "gripper_positions": [100.0, 100.0],
            "end_effector_frame": "robot_base",
            "end_effector_poses": {
                "left": [0.4, 0.2, 0.8, 0.0, 0.0, 0.0, 1.0],
                "right": [0.4, -0.2, 0.8, 0.0, 0.0, 0.0, 1.0],
            },
            "evidence_ref": "proprio/head-0001.json",
        },
        "objects": [
            {
                "object_id": "tube_left",
                "frame": "camera_head",
                "pose": [0.1, 0.2, 0.5, 0.0, 0.0, 0.0, 1.0],
                "axis": [0.0, 0.0, 1.0],
                "extent": {
                    "min": [0.09, 0.19, 0.45],
                    "max": [0.11, 0.21, 0.55],
                },
                "evidence_refs": ["objects/tube-left.json"],
            }
        ],
    }


def _observation() -> ObservationPacket:
    return observation_from_record(_observation_record())


def _candidate_record() -> dict:
    return {
        "candidate_id": "graspnet:0",
        "observation_id": "obs-head-0001",
        "hole_values": {
            "tube_grasp_pose": {
                "value": [0.1, 0.2, 0.5, 0.0, 0.0, 0.0, 1.0],
                "frame": "camera_head",
                "calibration_ref": "calibration/head.json",
                "object_id": "tube_left",
            }
        },
        "features": {"score": 0.9, "width": 0.03},
        "provenance": {},
        "evidence_refs": ["graspnet/raw-0001.json"],
    }


def _graspnet_response(rotation=None) -> dict:
    rotation = rotation or [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    raw_grasp_array = [
        0.91,
        0.032,
        0.02,
        0.04,
        *(item for row in rotation for item in row),
        0.1,
        0.2,
        0.5,
        7.0,
    ]
    return {
        "ok": True,
        "schema": "kw_independent.graspnet.raw_response.v1",
        "backend": "graspnet_baseline",
        "checkpoint_path": "weights/checkpoint.tar",
        "checkpoint_epoch": 10,
        "coordinate_frame": "camera_head",
        "grasps": [
            {
                "raw_index": 3,
                "score": 0.91,
                "width": 0.032,
                "height": 0.02,
                "depth": 0.04,
                "rotation_matrix": rotation,
                "translation": [0.1, 0.2, 0.5],
                "object_id": 7,
                "coordinate_frame": "camera_head",
                "raw_grasp_array": raw_grasp_array,
            }
        ],
        "input_reference": {
            "image_path": None,
            "depth_path": None,
            "mask_path": None,
            "point_cloud_path": "pointcloud/head-0001.npz",
            "object_hint": None,
            "frame_id": "obs-head-0001",
            "coordinate_frame": "camera_head",
            "camera_intrinsics": None,
            "extra": {},
        },
    }


def _point_cloud_manifest() -> dict:
    return {
        "artifact_ref": "pointcloud/head-0001.npz",
        "unit": "meter",
        "frame": "camera_head",
        "calibration_ref": "calibration/head.json",
        "evidence_ref": "pointcloud/head-0001.manifest.json",
    }


def _grasp_to_runtime_ee(
    value=None,
    evidence_ref="transforms/graspnet-to-runtime-ee.json",
) -> dict:
    return {
        "value": value or [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        "parent_frame": "graspnet_parallel_jaw",
        "child_frame": "runtime_ee",
        "calibration_ref": "calibration/head.json",
        "evidence_ref": evidence_ref,
        "translation_unit": "meter",
        "quaternion_convention": "xyzw",
    }


def _convert_grasps(
    response,
    *,
    object_id_mapping=None,
    pose_hole_name="tube_grasp_pose",
    observation=None,
    evidence_ref="graspnet/raw-0001.json",
    point_cloud_manifest=None,
    grasp_to_runtime_ee=None,
):
    return graspnet_candidates_from_response(
        response,
        object_id_mapping=object_id_mapping or {7: "tube_left"},
        pose_hole_name=pose_hole_name,
        observation=observation or _observation(),
        point_cloud_manifest=point_cloud_manifest or _point_cloud_manifest(),
        grasp_to_runtime_ee=grasp_to_runtime_ee or _grasp_to_runtime_ee(),
        evidence_ref=evidence_ref,
    )


def test_observation_from_record_builds_typed_immutable_values() -> None:
    observation = _observation()

    assert isinstance(observation, ObservationPacket)
    assert isinstance(observation.robot_state, Proprioception)
    assert isinstance(observation.objects[0], ObjectObservation)
    assert observation.robot_state.joint_positions == (0.0,) * 14
    assert observation.objects[0].pose == (0.1, 0.2, 0.5, 0.0, 0.0, 0.0, 1.0)
    assert observation.objects[0].extent["max"] == (0.11, 0.21, 0.55)
    with pytest.raises(TypeError):
        observation.robot_state.end_effector_poses["left"] = (0.0,) * 7


@pytest.mark.parametrize(
    "statement",
    [
        (
            "import demo_graph_lab.selection.candidates; "
            "import demo_graph_lab.perception.adapters"
        ),
        (
            "import demo_graph_lab.perception.adapters; "
            "import demo_graph_lab.selection.candidates"
        ),
    ],
)
def test_candidate_and_adapter_modules_import_in_either_order(statement) -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(source_root)}
    result = subprocess.run(
        [sys.executable, "-c", statement],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field", ["observation_id", "objects"])
def test_observation_record_rejects_missing_and_extra_fields(field: str) -> None:
    missing = _observation_record()
    del missing[field]
    with pytest.raises(ValueError, match="missing"):
        observation_from_record(missing)

    extra = _observation_record()
    extra["task_success"] = True
    with pytest.raises(ValueError, match="extra"):
        observation_from_record(extra)


def test_observation_record_rejects_nested_schema_drift() -> None:
    record = _observation_record()
    record["robot_state"]["state"] = {"oracle": True}
    with pytest.raises(ValueError, match="extra"):
        observation_from_record(record)

    record = _observation_record()
    del record["objects"][0]["axis"]
    with pytest.raises(ValueError, match="missing"):
        observation_from_record(record)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(captured_at_s=True),
        lambda value: value["robot_state"]["joint_positions"].__setitem__(0, False),
        lambda value: value.update(captured_at_s=float("nan")),
        lambda value: value["objects"][0]["axis"].__setitem__(0, float("inf")),
        lambda value: value["objects"][0].update(axis=[0.0, 0.0, 2.0]),
        lambda value: value["objects"][0].update(
            pose=[0.1, 0.2, 0.5, 0.0, 0.0, 0.0, 0.0]
        ),
        lambda value: value["objects"][0]["extent"].update(
            min=[0.12, 0.19, 0.45]
        ),
        lambda value: value["sensor_refs"].__setitem__(0, " "),
        lambda value: value["objects"][0]["evidence_refs"].__setitem__(0, ""),
    ],
)
def test_observation_record_rejects_invalid_numbers_and_empty_refs(mutate) -> None:
    record = _observation_record()
    mutate(record)
    with pytest.raises((TypeError, ValueError)):
        observation_from_record(record)


def test_candidate_from_record_binds_observation_and_freezes_data() -> None:
    candidate = candidate_from_record(_candidate_record())

    assert candidate.observation_id == "obs-head-0001"
    assert candidate.hole_values["tube_grasp_pose"]["object_id"] == "tube_left"
    with pytest.raises(TypeError):
        candidate.features["score"] = 0.0


def test_candidate_record_is_strict_and_rejects_unsafe_values() -> None:
    missing = _candidate_record()
    del missing["observation_id"]
    with pytest.raises(ValueError, match="missing"):
        candidate_from_record(missing)

    extra = _candidate_record()
    extra["reachable"] = True
    with pytest.raises(ValueError, match="extra"):
        candidate_from_record(extra)

    boolean = _candidate_record()
    boolean["features"]["score"] = True
    with pytest.raises(TypeError, match="boolean"):
        candidate_from_record(boolean)

    non_finite = _candidate_record()
    non_finite["hole_values"]["tube_grasp_pose"]["value"][0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        candidate_from_record(non_finite)

    empty_ref = _candidate_record()
    empty_ref["evidence_refs"] = []
    with pytest.raises(ValueError, match="empty"):
        candidate_from_record(empty_ref)


def test_graspnet_response_builds_strict_pose_envelope_without_claims() -> None:
    candidates = _convert_grasps(
        _graspnet_response(),
        object_id_mapping={7: "tube_left"},
        pose_hole_name="tube_grasp_pose",
        observation=_observation(),
        evidence_ref="graspnet/raw-0001.json",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id == "graspnet:3"
    assert candidate.observation_id == "obs-head-0001"
    assert candidate.to_record()["hole_values"] == {
        "tube_grasp_pose": {
            "value": [0.1, 0.2, 0.5, 0.0, 0.0, 0.0, 1.0],
            "frame": "camera_head",
            "calibration_ref": "calibration/head.json",
            "object_id": "tube_left",
        }
    }
    assert candidate.to_record()["features"] == {
        "score": 0.91,
        "width": 0.032,
        "height": 0.02,
        "depth": 0.04,
        "pose_convention": "runtime_ee_xyzw",
    }
    assert candidate.to_record()["provenance"] == {
        "grasp_to_runtime_ee": {
            "value": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "parent_frame": "graspnet_parallel_jaw",
            "child_frame": "runtime_ee",
            "translation_unit": "meter",
            "quaternion_convention": "xyzw",
            "calibration_ref": "calibration/head.json",
            "evidence_ref": "transforms/graspnet-to-runtime-ee.json",
        }
    }
    assert "approach_dir" not in candidate.features
    assert "height_fraction" not in candidate.features
    assert "collision_free" not in candidate.features
    assert candidate.evidence_refs == (
        "graspnet/raw-0001.json",
        "pointcloud/head-0001.manifest.json",
        "transforms/graspnet-to-runtime-ee.json",
        "calibration/head.json",
    )


def test_graspnet_rotation_is_converted_to_xyzw() -> None:
    rotation_z_90 = [
        [0.0, -1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    candidate = _convert_grasps(
        _graspnet_response(rotation_z_90),
        object_id_mapping={"7": "tube_left"},
        pose_hole_name="tube_grasp_pose",
        observation=_observation(),
        evidence_ref="graspnet/raw-0001.json",
    )[0]

    quaternion = candidate.to_record()["hole_values"]["tube_grasp_pose"]["value"][3:]
    assert quaternion == pytest.approx([0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)])


def test_graspnet_applies_recorded_grasp_to_runtime_ee_transform() -> None:
    candidate = _convert_grasps(
        _graspnet_response(),
        grasp_to_runtime_ee=_grasp_to_runtime_ee([
            0.1, 0.0, 0.0,
            0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5),
        ]),
    )[0]

    pose = candidate.to_record()["hole_values"]["tube_grasp_pose"]["value"]
    assert pose[:3] == pytest.approx([0.2, 0.2, 0.5])
    assert pose[3:] == pytest.approx([
        0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5),
    ])


def test_transform_value_is_preserved_even_when_evidence_ref_is_reused() -> None:
    observation = _observation()
    identity = _convert_grasps(
        _graspnet_response(),
        observation=observation,
    )[0]
    shifted = _convert_grasps(
        _graspnet_response(),
        observation=observation,
        grasp_to_runtime_ee=_grasp_to_runtime_ee(
            [0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        ),
    )[0]

    assert identity.hole_values != shifted.hole_values
    assert identity.evidence_refs == shifted.evidence_refs
    assert identity.provenance != shifted.provenance
    assert shifted.provenance["grasp_to_runtime_ee"]["value"] == (
        0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unit", "millimeter", "unit must be meter"),
        ("frame", "robot_base", "frame"),
        ("calibration_ref", "calibration/other.json", "calibration_ref"),
        ("artifact_ref", "pointcloud/other.npz", "artifact_ref"),
    ],
)
def test_graspnet_requires_meter_point_cloud_manifest(field, value, message) -> None:
    manifest = _point_cloud_manifest()
    manifest[field] = value

    with pytest.raises(ValueError, match=message):
        _convert_grasps(
            _graspnet_response(),
            point_cloud_manifest=manifest,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("parent_frame", "unknown", "parent_frame"),
        ("child_frame", "tool", "child_frame"),
        ("calibration_ref", "calibration/other.json", "calibration_ref"),
        ("value", [0.0] * 7, "unit length"),
        ("evidence_ref", "transforms/unrecorded.json", "belong to observation"),
        ("translation_unit", "millimeter", "translation_unit"),
        ("quaternion_convention", "wxyz", "quaternion_convention"),
    ],
)
def test_graspnet_requires_explicit_runtime_ee_transform(field, value, message) -> None:
    transform = _grasp_to_runtime_ee()
    transform[field] = value

    with pytest.raises(ValueError, match=message):
        _convert_grasps(
            _graspnet_response(),
            grasp_to_runtime_ee=transform,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "another.schema", "schema"),
        ("backend", "fixture", "backend"),
        ("coordinate_frame", "unknown", "coordinate_frame"),
    ],
)
def test_graspnet_rejects_unknown_response_identity(field: str, value, message: str) -> None:
    response = _graspnet_response()
    response[field] = value
    with pytest.raises(ValueError, match=message):
        _convert_grasps(
            response,
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )


def test_graspnet_rejects_frame_and_object_mapping_mismatches() -> None:
    response = _graspnet_response()
    response["grasps"][0]["coordinate_frame"] = "robot_base"
    with pytest.raises(ValueError, match="response frame"):
        _convert_grasps(
            response,
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )

    unknown_object = _graspnet_response()
    unknown_object["grasps"][0]["object_id"] = -1
    unknown_object["grasps"][0]["raw_grasp_array"][-1] = -1.0
    with pytest.raises((TypeError, ValueError), match="object_id|non-negative"):
        _convert_grasps(
            unknown_object,
            object_id_mapping={-1: "tube_left"},
        )

    with pytest.raises(ValueError, match="no graph object mapping"):
        _convert_grasps(
            _graspnet_response(),
            object_id_mapping={8: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )


def test_graspnet_rejects_reply_from_another_observation() -> None:
    response = _graspnet_response()
    response["input_reference"]["point_cloud_path"] = "pointcloud/stale.npz"

    with pytest.raises(ValueError, match="current observation"):
        _convert_grasps(
            response,
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("frame_id", "obs-stale", "frame_id"),
        ("coordinate_frame", "robot_base", "input coordinate_frame"),
    ],
)
def test_graspnet_rejects_mismatched_input_identity(field, value, message) -> None:
    response = _graspnet_response()
    response["input_reference"][field] = value

    with pytest.raises(ValueError, match=message):
        _convert_grasps(
            response,
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )

@pytest.mark.parametrize(
    "rotation",
    [
        [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0]],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]],
        [[1.0, 0.0, 0.0], [0.0, float("nan"), 0.0], [0.0, 0.0, 1.0]],
        [[True, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    ],
)
def test_graspnet_rejects_invalid_rotation_matrices(rotation) -> None:
    with pytest.raises((TypeError, ValueError)):
        _convert_grasps(
            _graspnet_response(rotation),
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )


def test_graspnet_rejects_bool_numeric_and_empty_evidence() -> None:
    response = _graspnet_response()
    response["grasps"][0]["score"] = True
    with pytest.raises(TypeError, match="number"):
        _convert_grasps(
            response,
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )

    with pytest.raises(ValueError, match="non-empty"):
        _convert_grasps(
            _graspnet_response(),
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref=" ",
        )


def test_graspnet_rejects_structured_and_raw_field_disagreement() -> None:
    response = _graspnet_response()
    response["grasps"][0]["width"] = 0.05

    with pytest.raises(ValueError, match="disagrees"):
        _convert_grasps(
            response,
            object_id_mapping={7: "tube_left"},
            pose_hole_name="tube_grasp_pose",
            observation=_observation(),
            evidence_ref="graspnet/raw-0001.json",
        )
