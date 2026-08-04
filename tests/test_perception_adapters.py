"""Offline tests for strict observation, candidate, and raw GraspNet records."""

from __future__ import annotations

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
from demo_graph_lab.perception import adapters
from demo_graph_lab.perception.adapters import (
    candidate_from_record,
    observation_from_record,
    validate_graspnet_response_record,
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
        "objects": [{
            "object_id": "tube_left",
            "frame": "camera_head",
            "pose": [0.1, 0.2, 0.5, 0.0, 0.0, 0.0, 1.0],
            "axis": [0.0, 0.0, 1.0],
            "extent": {
                "min": [0.09, 0.19, 0.45],
                "max": [0.11, 0.21, 0.55],
            },
            "evidence_refs": ["objects/tube-left.json"],
        }],
    }


def _observation() -> ObservationPacket:
    return observation_from_record(_observation_record())


def _candidate_record() -> dict:
    return {
        "candidate_id": "recorded:candidate-0",
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
        "evidence_refs": ["candidates/candidate-0.json"],
    }


def _graspnet_response(rotation=None, *, object_id=7) -> dict:
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
        float(object_id),
    ]
    return {
        "ok": True,
        "schema": "kw_independent.graspnet.raw_response.v1",
        "backend": "graspnet_baseline",
        "checkpoint_path": "weights/checkpoint.tar",
        "checkpoint_epoch": 10,
        "coordinate_frame": "camera_head",
        "grasps": [{
            "raw_index": 3,
            "score": 0.91,
            "width": 0.032,
            "height": 0.02,
            "depth": 0.04,
            "rotation_matrix": rotation,
            "translation": [0.1, 0.2, 0.5],
            "object_id": object_id,
            "coordinate_frame": "camera_head",
            "raw_grasp_array": raw_grasp_array,
        }],
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


def _validate(response, *, observation=None, point_cloud_manifest=None):
    return validate_graspnet_response_record(
        response,
        observation=observation or _observation(),
        point_cloud_manifest=point_cloud_manifest or _point_cloud_manifest(),
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
        "import demo_graph_lab.selection.candidates; import demo_graph_lab.perception.adapters",
        "import demo_graph_lab.perception.adapters; import demo_graph_lab.selection.candidates",
    ],
)
def test_candidate_and_adapter_modules_import_in_either_order(statement) -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-c", statement],
        env={**os.environ, "PYTHONPATH": str(source_root)},
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


@pytest.mark.parametrize("object_id", [-1, 7])
def test_raw_graspnet_validation_never_assigns_graph_objects(object_id) -> None:
    summary = _validate(_graspnet_response(object_id=object_id))

    assert summary["grasp_count"] == 1
    assert summary["object_ids"] == [object_id]
    assert summary["requires_object_assignment"] is True
    assert not hasattr(adapters, "graspnet_candidates_from_response")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unit", "millimeter", "unit must be meter"),
        ("frame", "robot_base", "frame"),
        ("calibration_ref", "calibration/other.json", "calibration_ref"),
        ("artifact_ref", "pointcloud/other.npz", "artifact_ref"),
        ("evidence_ref", "pointcloud/other.manifest.json", "observation"),
    ],
)
def test_raw_graspnet_requires_bound_meter_point_cloud(field, value, message) -> None:
    manifest = _point_cloud_manifest()
    manifest[field] = value
    with pytest.raises(ValueError, match=message):
        _validate(_graspnet_response(), point_cloud_manifest=manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "another.schema", "schema"),
        ("backend", "fixture", "backend"),
        ("coordinate_frame", "unknown", "coordinate_frame"),
        ("ok", False, "successful"),
    ],
)
def test_raw_graspnet_rejects_response_identity_drift(field, value, message) -> None:
    response = _graspnet_response()
    response[field] = value
    with pytest.raises(ValueError, match=message):
        _validate(response)


def test_raw_graspnet_rejects_reply_from_another_observation() -> None:
    response = _graspnet_response()
    response["input_reference"]["point_cloud_path"] = "pointcloud/stale.npz"
    with pytest.raises(ValueError, match="current observation"):
        _validate(response)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("frame_id", "obs-stale", "frame_id"),
        ("coordinate_frame", "robot_base", "input coordinate_frame"),
    ],
)
def test_raw_graspnet_rejects_mismatched_input_identity(field, value, message) -> None:
    response = _graspnet_response()
    response["input_reference"][field] = value
    with pytest.raises(ValueError, match=message):
        _validate(response)


@pytest.mark.parametrize(
    "rotation",
    [
        [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0]],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]],
        [[1.0, 0.0, 0.0], [0.0, float("nan"), 0.0], [0.0, 0.0, 1.0]],
        [[True, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    ],
)
def test_raw_graspnet_rejects_invalid_rotation_matrices(rotation) -> None:
    with pytest.raises((TypeError, ValueError)):
        _validate(_graspnet_response(rotation))


def test_raw_graspnet_rejects_numeric_bool_and_raw_disagreement() -> None:
    response = _graspnet_response()
    response["grasps"][0]["score"] = True
    with pytest.raises(TypeError, match="number"):
        _validate(response)

    response = _graspnet_response()
    response["grasps"][0]["width"] = 0.05
    with pytest.raises(ValueError, match="disagrees"):
        _validate(response)
