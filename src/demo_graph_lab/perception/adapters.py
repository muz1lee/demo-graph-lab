"""Strict, offline adapters for recorded observations and grasp proposals.

This module only parses in-memory records.  It contains no transport, simulator,
or controller client, so replay tests cannot accidentally become live calls.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from ..selection.candidates import CandidateBundle
from .observations import ObjectObservation, ObservationPacket, Proprioception


_OBSERVATION_KEYS = {
    "observation_id",
    "captured_at_s",
    "frame",
    "calibration_ref",
    "sensor_refs",
    "robot_state",
    "objects",
}
_PROPRIOCEPTION_KEYS = {
    "joint_positions",
    "gripper_positions",
    "end_effector_frame",
    "end_effector_poses",
    "evidence_ref",
}
_OBJECT_KEYS = {
    "object_id",
    "frame",
    "pose",
    "axis",
    "extent",
    "evidence_refs",
}
_CANDIDATE_KEYS = {
    "candidate_id",
    "observation_id",
    "hole_values",
    "features",
    "provenance",
    "evidence_refs",
}

_GRASPNET_SCHEMA = "kw_independent.graspnet.raw_response.v1"
_GRASPNET_RESPONSE_REQUIRED = {
    "ok",
    "schema",
    "backend",
    "coordinate_frame",
    "grasps",
    "input_reference",
}
_GRASPNET_RESPONSE_ALLOWED = _GRASPNET_RESPONSE_REQUIRED | {
    "checkpoint_path",
    "checkpoint_epoch",
    "error",
    "source_policy",
}
_GRASPNET_INPUT_KEYS = {
    "image_path",
    "depth_path",
    "mask_path",
    "point_cloud_path",
    "object_hint",
    "frame_id",
    "coordinate_frame",
    "camera_intrinsics",
    "extra",
}
_GRASP_KEYS = {
    "raw_index",
    "score",
    "width",
    "height",
    "depth",
    "rotation_matrix",
    "translation",
    "object_id",
    "coordinate_frame",
    "raw_grasp_array",
}
_POINT_CLOUD_MANIFEST_KEYS = {
    "artifact_ref",
    "unit",
    "frame",
    "calibration_ref",
    "evidence_ref",
}
_GRASP_TO_EE_KEYS = {
    "value",
    "parent_frame",
    "child_frame",
    "calibration_ref",
    "evidence_ref",
    "translation_unit",
    "quaternion_convention",
}
_ROTATION_TOLERANCE = 1e-4


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be an object")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise ValueError(f"{path} keys must be non-empty strings")
    return value


def _exact_object(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    record = _object(value, path)
    actual = set(record)
    missing = sorted(keys - actual)
    extra = sorted(actual - keys)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing={missing}")
        if extra:
            parts.append(f"extra={extra}")
        raise ValueError(f"{path} has invalid fields: {', '.join(parts)}")
    return record


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be a number, not {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{path} must be finite")
    return number


def _integer(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path} must be >= {minimum}")
    return value


def _number_list(
    value: Any,
    path: str,
    *,
    length: int | None = None,
    non_empty: bool = False,
) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be a JSON array")
    if length is not None and len(value) != length:
        raise ValueError(f"{path} must contain exactly {length} numbers")
    if non_empty and not value:
        raise ValueError(f"{path} must not be empty")
    return tuple(_finite_number(item, f"{path}[{index}]") for index, item in enumerate(value))


def _references(value: Any, path: str, *, non_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be a JSON array")
    refs = tuple(_required_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if non_empty and not refs:
        raise ValueError(f"{path} must not be empty")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{path} must not contain duplicate references")
    return refs


def _json_value(value: Any, path: str) -> Any:
    """Copy finite JSON candidate data while rejecting numeric booleans.

    Candidate records contain physical values and ranking features.  Boolean
    feasibility claims belong in hard-check certificates, not in this data.
    """

    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise TypeError(f"{path} must not use a boolean as candidate data")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        record = _object(value, path)
        return {
            key: _json_value(item, f"{path}.{key}")
            for key, item in record.items()
        }
    raise TypeError(f"{path} contains non-JSON value {type(value).__name__}")


def observation_from_record(record: Mapping[str, Any]) -> ObservationPacket:
    """Parse one complete offline observation record with no implicit defaults."""

    root = _exact_object(record, _OBSERVATION_KEYS, "observation")
    robot = _exact_object(root["robot_state"], _PROPRIOCEPTION_KEYS, "observation.robot_state")

    end_effectors_raw = _object(
        robot["end_effector_poses"],
        "observation.robot_state.end_effector_poses",
    )
    end_effectors = {
        _required_string(arm, "observation.robot_state.end_effector_poses key"):
        _number_list(
            pose,
            f"observation.robot_state.end_effector_poses.{arm}",
            length=7,
        )
        for arm, pose in end_effectors_raw.items()
    }
    proprioception = Proprioception(
        joint_positions=_number_list(
            robot["joint_positions"],
            "observation.robot_state.joint_positions",
            non_empty=True,
        ),
        end_effector_frame=_required_string(
            robot["end_effector_frame"],
            "observation.robot_state.end_effector_frame",
        ),
        gripper_positions=_number_list(
            robot["gripper_positions"],
            "observation.robot_state.gripper_positions",
        ),
        end_effector_poses=end_effectors,
        evidence_ref=_required_string(
            robot["evidence_ref"],
            "observation.robot_state.evidence_ref",
        ),
    )

    objects_raw = root["objects"]
    if not isinstance(objects_raw, list):
        raise TypeError("observation.objects must be a JSON array")
    objects = []
    for index, value in enumerate(objects_raw):
        path = f"observation.objects[{index}]"
        item = _exact_object(value, _OBJECT_KEYS, path)
        extent_raw = item["extent"]
        extent = None
        if extent_raw is not None:
            extent_obj = _exact_object(extent_raw, {"min", "max"}, f"{path}.extent")
            extent = {
                "min": _number_list(extent_obj["min"], f"{path}.extent.min", length=3),
                "max": _number_list(extent_obj["max"], f"{path}.extent.max", length=3),
            }
        objects.append(
            ObjectObservation(
                object_id=_required_string(item["object_id"], f"{path}.object_id"),
                frame=_required_string(item["frame"], f"{path}.frame"),
                pose=(
                    None
                    if item["pose"] is None
                    else _number_list(item["pose"], f"{path}.pose", length=7)
                ),
                axis=(
                    None
                    if item["axis"] is None
                    else _number_list(item["axis"], f"{path}.axis", length=3)
                ),
                extent=extent,
                evidence_refs=_references(item["evidence_refs"], f"{path}.evidence_refs"),
            )
        )

    return ObservationPacket(
        observation_id=_required_string(root["observation_id"], "observation.observation_id"),
        captured_at_s=_finite_number(root["captured_at_s"], "observation.captured_at_s"),
        frame=_required_string(root["frame"], "observation.frame"),
        calibration_ref=_required_string(
            root["calibration_ref"],
            "observation.calibration_ref",
        ),
        sensor_refs=_references(root["sensor_refs"], "observation.sensor_refs"),
        robot_state=proprioception,
        objects=tuple(objects),
    )


def candidate_from_record(record: Mapping[str, Any]) -> CandidateBundle:
    """Parse one complete offline candidate record with no implicit defaults."""

    root = _exact_object(record, _CANDIDATE_KEYS, "candidate")
    hole_values = _object(root["hole_values"], "candidate.hole_values")
    features = _object(root["features"], "candidate.features")
    provenance = _object(root["provenance"], "candidate.provenance")
    return CandidateBundle(
        candidate_id=_required_string(root["candidate_id"], "candidate.candidate_id"),
        observation_id=_required_string(root["observation_id"], "candidate.observation_id"),
        hole_values=_json_value(hole_values, "candidate.hole_values"),
        features=_json_value(features, "candidate.features"),
        provenance=_json_value(provenance, "candidate.provenance"),
        evidence_refs=_references(root["evidence_refs"], "candidate.evidence_refs"),
    )


def _graph_object_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("object_id_mapping must be an object")
    mapping = value
    if not mapping:
        raise ValueError("object_id_mapping must not be empty")
    normalized: dict[str, str] = {}
    for raw_id, graph_id in mapping.items():
        if not isinstance(raw_id, (str, int)) or isinstance(raw_id, bool):
            raise TypeError("object_id_mapping keys must be integer IDs or their string form")
        key = str(raw_id)
        try:
            parsed_id = int(key)
        except ValueError as error:
            raise ValueError(
                "object_id_mapping keys must be non-negative integer IDs"
            ) from error
        if parsed_id < 0 or key != str(parsed_id):
            raise ValueError(
                "object_id_mapping keys must be canonical non-negative integer IDs"
            )
        if key in normalized:
            raise ValueError(f"object_id_mapping contains ambiguous ID {key!r}")
        normalized[key] = _required_string(graph_id, f"object_id_mapping[{key!r}]")
    return normalized


def _rotation_matrix(value: Any, path: str) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{path} must be a 3x3 JSON array")
    rows = tuple(_number_list(row, f"{path}[{index}]", length=3) for index, row in enumerate(value))
    columns = tuple(tuple(rows[row][column] for row in range(3)) for column in range(3))
    for index, column in enumerate(columns):
        norm = sum(item * item for item in column)
        if abs(norm - 1.0) > _ROTATION_TOLERANCE:
            raise ValueError(f"{path} column {index} is not unit length")
    for first in range(3):
        for second in range(first + 1, 3):
            dot = sum(columns[first][i] * columns[second][i] for i in range(3))
            if abs(dot) > _ROTATION_TOLERANCE:
                raise ValueError(f"{path} columns are not orthogonal")
    determinant = (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )
    if abs(determinant - 1.0) > _ROTATION_TOLERANCE:
        raise ValueError(f"{path} determinant must be approximately +1")
    return rows


def _matrix_to_xyzw(matrix: tuple[tuple[float, ...], ...]) -> list[float]:
    """Convert a validated right-handed rotation matrix to canonical XYZW."""

    m00, m01, m02 = matrix[0]
    m10, m11, m12 = matrix[1]
    m20, m21, m22 = matrix[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        qx = (m21 - m12) / scale
        qy = (m02 - m20) / scale
        qz = (m10 - m01) / scale
        qw = 0.25 * scale
    elif m00 > m11 and m00 > m22:
        scale = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        qx = 0.25 * scale
        qy = (m01 + m10) / scale
        qz = (m02 + m20) / scale
        qw = (m21 - m12) / scale
    elif m11 > m22:
        scale = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        qx = (m01 + m10) / scale
        qy = 0.25 * scale
        qz = (m12 + m21) / scale
        qw = (m02 - m20) / scale
    else:
        scale = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        qx = (m02 + m20) / scale
        qy = (m12 + m21) / scale
        qz = 0.25 * scale
        qw = (m10 - m01) / scale

    quaternion = [qx, qy, qz, qw]
    norm = math.sqrt(sum(item * item for item in quaternion))
    quaternion = [item / norm for item in quaternion]
    if quaternion[3] < 0.0:
        quaternion = [-item for item in quaternion]
    return quaternion


def _xyzw_to_matrix(value: Any, path: str) -> tuple[tuple[float, ...], ...]:
    quaternion = _number_list(value, path, length=4)
    norm = math.sqrt(sum(item * item for item in quaternion))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
        raise ValueError(f"{path} must be unit length")
    x, y, z, w = (item / norm for item in quaternion)
    return (
        (
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
        ),
        (
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
        ),
        (
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ),
    )


def _compose_pose(
    parent_translation: tuple[float, ...],
    parent_rotation: tuple[tuple[float, ...], ...],
    child_transform: tuple[float, ...],
) -> tuple[list[float], tuple[tuple[float, ...], ...]]:
    child_translation = child_transform[:3]
    child_rotation = _xyzw_to_matrix(
        list(child_transform[3:]), "grasp_to_runtime_ee.value.quaternion_xyzw"
    )
    rotated_translation = [
        sum(parent_rotation[row][column] * child_translation[column]
            for column in range(3))
        for row in range(3)
    ]
    translation = [
        parent_translation[index] + rotated_translation[index]
        for index in range(3)
    ]
    rotation = tuple(
        tuple(
            sum(parent_rotation[row][k] * child_rotation[k][column]
                for k in range(3))
            for column in range(3)
        )
        for row in range(3)
    )
    return translation, rotation


def _validate_point_cloud_manifest(
    manifest: Mapping[str, Any],
    observation: ObservationPacket,
    point_cloud_ref: str,
) -> str:
    value = _exact_object(
        manifest, _POINT_CLOUD_MANIFEST_KEYS, "point_cloud_manifest"
    )
    if value["artifact_ref"] != point_cloud_ref:
        raise ValueError("point_cloud_manifest artifact_ref does not match request")
    if value["unit"] != "meter":
        raise ValueError("point_cloud_manifest unit must be meter")
    if value["frame"] != observation.frame:
        raise ValueError("point_cloud_manifest frame does not match observation")
    if value["calibration_ref"] != observation.calibration_ref:
        raise ValueError(
            "point_cloud_manifest calibration_ref does not match observation"
        )
    evidence_ref = _required_string(
        value["evidence_ref"], "point_cloud_manifest.evidence_ref"
    )
    if evidence_ref not in observation.sensor_refs:
        raise ValueError(
            "point_cloud_manifest evidence_ref does not belong to observation"
        )
    return evidence_ref


def _validate_grasp_to_runtime_ee(
    transform: Mapping[str, Any],
    observation: ObservationPacket,
) -> tuple[tuple[float, ...], str]:
    value = _exact_object(
        transform, _GRASP_TO_EE_KEYS, "grasp_to_runtime_ee"
    )
    if value["parent_frame"] != "graspnet_parallel_jaw":
        raise ValueError(
            "grasp_to_runtime_ee.parent_frame must be graspnet_parallel_jaw"
        )
    if value["child_frame"] != "runtime_ee":
        raise ValueError("grasp_to_runtime_ee.child_frame must be runtime_ee")
    if value["calibration_ref"] != observation.calibration_ref:
        raise ValueError(
            "grasp_to_runtime_ee calibration_ref does not match observation"
        )
    if value["translation_unit"] != "meter":
        raise ValueError("grasp_to_runtime_ee translation_unit must be meter")
    if value["quaternion_convention"] != "xyzw":
        raise ValueError("grasp_to_runtime_ee quaternion_convention must be xyzw")
    pose = _number_list(value["value"], "grasp_to_runtime_ee.value", length=7)
    _xyzw_to_matrix(list(pose[3:]), "grasp_to_runtime_ee.value.quaternion_xyzw")
    evidence_ref = _required_string(
        value["evidence_ref"], "grasp_to_runtime_ee.evidence_ref"
    )
    if evidence_ref not in observation.sensor_refs:
        raise ValueError(
            "grasp_to_runtime_ee evidence_ref does not belong to observation"
        )
    return pose, evidence_ref


def graspnet_candidates_from_response(
    response: Mapping[str, Any],
    *,
    object_id_mapping: Mapping[int | str, str],
    pose_hole_name: str,
    observation: ObservationPacket,
    point_cloud_manifest: Mapping[str, Any],
    grasp_to_runtime_ee: Mapping[str, Any],
    evidence_ref: str,
) -> tuple[CandidateBundle, ...]:
    """Convert one recorded real GraspNet ``/predict`` response to candidates.

    This performs only the explicit, recorded grasp-frame → runtime-EEF
    transform.  It does not rank candidates, assert collision freedom, or call
    a service.  The response and point-cloud manifest must match the observation.
    """

    if not isinstance(observation, ObservationPacket):
        raise TypeError("observation must be an ObservationPacket")
    hole_name = _required_string(pose_hole_name, "pose_hole_name")
    source_ref = _required_string(evidence_ref, "evidence_ref")
    graph_objects = _graph_object_map(object_id_mapping)
    runtime_ee_transform, runtime_ee_transform_evidence = _validate_grasp_to_runtime_ee(
        grasp_to_runtime_ee, observation
    )

    root = _object(response, "graspnet_response")
    actual_keys = set(root)
    missing = sorted(_GRASPNET_RESPONSE_REQUIRED - actual_keys)
    extra = sorted(actual_keys - _GRASPNET_RESPONSE_ALLOWED)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing={missing}")
        if extra:
            parts.append(f"extra={extra}")
        raise ValueError(f"graspnet_response has invalid fields: {', '.join(parts)}")
    if root["ok"] is not True:
        raise ValueError("graspnet_response is not a successful prediction")
    if root["schema"] != _GRASPNET_SCHEMA:
        raise ValueError(f"unknown graspnet response schema: {root['schema']!r}")
    if root["backend"] != "graspnet_baseline":
        raise ValueError(f"unsupported graspnet backend: {root['backend']!r}")
    frame = _required_string(root["coordinate_frame"], "graspnet_response.coordinate_frame")
    if frame.lower() == "unknown":
        raise ValueError("graspnet response coordinate_frame must be known")
    if frame != observation.frame:
        raise ValueError(
            f"graspnet response frame {frame!r} does not match observation frame "
            f"{observation.frame!r}"
        )
    input_reference = _exact_object(
        root["input_reference"],
        _GRASPNET_INPUT_KEYS,
        "graspnet_response.input_reference",
    )
    point_cloud_ref = _required_string(
        input_reference["point_cloud_path"],
        "graspnet_response.input_reference.point_cloud_path",
    )
    if point_cloud_ref not in observation.sensor_refs:
        raise ValueError(
            "graspnet point_cloud_path does not belong to the current observation"
        )
    input_frame_id = _required_string(
        input_reference["frame_id"],
        "graspnet_response.input_reference.frame_id",
    )
    if input_frame_id != observation.observation_id:
        raise ValueError("graspnet input frame_id does not match the observation")
    input_coordinate_frame = _required_string(
        input_reference["coordinate_frame"],
        "graspnet_response.input_reference.coordinate_frame",
    )
    if input_coordinate_frame != frame:
        raise ValueError(
            "graspnet input coordinate_frame does not match the response frame"
        )
    point_cloud_evidence = _validate_point_cloud_manifest(
        point_cloud_manifest,
        observation,
        point_cloud_ref,
    )

    rows = root["grasps"]
    if not isinstance(rows, list):
        raise TypeError("graspnet_response.grasps must be a JSON array")
    candidates = []
    seen_indexes: set[int] = set()
    for index, value in enumerate(rows):
        path = f"graspnet_response.grasps[{index}]"
        row = _exact_object(value, _GRASP_KEYS, path)
        raw_index = _integer(row["raw_index"], f"{path}.raw_index", minimum=0)
        if raw_index in seen_indexes:
            raise ValueError(f"duplicate grasp raw_index: {raw_index}")
        seen_indexes.add(raw_index)

        candidate_frame = _required_string(row["coordinate_frame"], f"{path}.coordinate_frame")
        if candidate_frame.lower() == "unknown" or candidate_frame != frame:
            raise ValueError(f"{path}.coordinate_frame does not match the response frame")
        raw_object_id = _integer(
            row["object_id"], f"{path}.object_id", minimum=0
        )
        graph_object_id = graph_objects.get(str(raw_object_id))
        if graph_object_id is None:
            raise ValueError(f"no graph object mapping for GraspNet object_id {raw_object_id}")

        translation = _number_list(row["translation"], f"{path}.translation", length=3)
        rotation = _rotation_matrix(row["rotation_matrix"], f"{path}.rotation_matrix")
        runtime_translation, runtime_rotation = _compose_pose(
            translation,
            rotation,
            runtime_ee_transform,
        )
        quaternion = _matrix_to_xyzw(runtime_rotation)
        score = _finite_number(row["score"], f"{path}.score")
        dimensions = {
            name: _finite_number(row[name], f"{path}.{name}")
            for name in ("width", "height", "depth")
        }
        if any(number < 0.0 for number in dimensions.values()):
            raise ValueError(f"{path} width, height, and depth must be non-negative")
        raw_array = _number_list(
            row["raw_grasp_array"], f"{path}.raw_grasp_array", length=17
        )
        expected_raw = (
            score,
            dimensions["width"],
            dimensions["height"],
            dimensions["depth"],
            *(item for matrix_row in rotation for item in matrix_row),
            *translation,
            float(raw_object_id),
        )
        if any(
            not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-6)
            for actual, expected in zip(raw_array, expected_raw)
        ):
            raise ValueError(
                f"{path}.raw_grasp_array disagrees with structured grasp fields"
            )

        candidates.append(
            CandidateBundle(
                candidate_id=f"graspnet:{raw_index}",
                observation_id=observation.observation_id,
                hole_values={
                    hole_name: {
                        "value": [*runtime_translation, *quaternion],
                        "frame": frame,
                        "calibration_ref": observation.calibration_ref,
                        "object_id": graph_object_id,
                    }
                },
                features={
                    "score": score,
                    **dimensions,
                    "pose_convention": "runtime_ee_xyzw",
                },
                provenance={
                    "grasp_to_runtime_ee": {
                        "value": list(runtime_ee_transform),
                        "parent_frame": "graspnet_parallel_jaw",
                        "child_frame": "runtime_ee",
                        "translation_unit": "meter",
                        "quaternion_convention": "xyzw",
                        "calibration_ref": observation.calibration_ref,
                        "evidence_ref": runtime_ee_transform_evidence,
                    }
                },
                evidence_refs=tuple(dict.fromkeys((
                    source_ref,
                    point_cloud_evidence,
                    runtime_ee_transform_evidence,
                    observation.calibration_ref,
                ))),
            )
        )
    return tuple(candidates)
