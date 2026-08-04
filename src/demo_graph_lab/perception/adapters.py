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
    if point_cloud_ref not in observation.sensor_refs:
        raise ValueError(
            "point_cloud_manifest artifact_ref does not belong to observation"
        )
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


def validate_point_cloud_manifest_record(
    manifest: Mapping[str, Any],
    *,
    observation: ObservationPacket,
) -> dict[str, str]:
    """Validate a frozen point-cloud binding before any live model call."""

    if not isinstance(observation, ObservationPacket):
        raise TypeError("observation must be an ObservationPacket")
    value = _exact_object(
        manifest, _POINT_CLOUD_MANIFEST_KEYS, "point_cloud_manifest"
    )
    point_cloud_ref = _required_string(
        value["artifact_ref"], "point_cloud_manifest.artifact_ref"
    )
    evidence_ref = _validate_point_cloud_manifest(
        value,
        observation,
        point_cloud_ref,
    )
    return {
        "artifact_ref": point_cloud_ref,
        "unit": "meter",
        "frame": observation.frame,
        "calibration_ref": observation.calibration_ref,
        "evidence_ref": evidence_ref,
    }


def _validated_graspnet_response(
    response: Mapping[str, Any],
    observation: ObservationPacket,
    point_cloud_manifest: Mapping[str, Any],
) -> tuple[str, str, tuple[dict[str, Any], ...]]:
    """Validate one frozen raw response without assigning graph objects.

    The upstream baseline currently emits ``object_id=-1`` for every proposal.
    That value is valid raw evidence, but it is not a graph-object assignment.
    Graph-object assignment and candidate conversion are intentionally outside
    this raw-record module until a reviewed assignment artifact exists.
    """

    if not isinstance(observation, ObservationPacket):
        raise TypeError("observation must be an ObservationPacket")
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
    frame = _required_string(
        root["coordinate_frame"], "graspnet_response.coordinate_frame"
    )
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
    parsed_rows = []
    seen_indexes: set[int] = set()
    for index, value in enumerate(rows):
        path = f"graspnet_response.grasps[{index}]"
        row = _exact_object(value, _GRASP_KEYS, path)
        raw_index = _integer(row["raw_index"], f"{path}.raw_index", minimum=0)
        if raw_index in seen_indexes:
            raise ValueError(f"duplicate grasp raw_index: {raw_index}")
        seen_indexes.add(raw_index)

        candidate_frame = _required_string(
            row["coordinate_frame"], f"{path}.coordinate_frame"
        )
        if candidate_frame.lower() == "unknown" or candidate_frame != frame:
            raise ValueError(
                f"{path}.coordinate_frame does not match the response frame"
            )
        raw_object_id = _integer(
            row["object_id"], f"{path}.object_id", minimum=-1
        )
        translation = _number_list(
            row["translation"], f"{path}.translation", length=3
        )
        rotation = _rotation_matrix(
            row["rotation_matrix"], f"{path}.rotation_matrix"
        )
        score = _finite_number(row["score"], f"{path}.score")
        dimensions = {
            name: _finite_number(row[name], f"{path}.{name}")
            for name in ("width", "height", "depth")
        }
        if any(number < 0.0 for number in dimensions.values()):
            raise ValueError(
                f"{path} width, height, and depth must be non-negative"
            )
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
        parsed_rows.append({
            "raw_index": raw_index,
            "object_id": raw_object_id,
            "translation": translation,
            "rotation": rotation,
            "score": score,
            "dimensions": dimensions,
        })
    return frame, point_cloud_evidence, tuple(parsed_rows)


def validate_graspnet_response_record(
    response: Mapping[str, Any],
    *,
    observation: ObservationPacket,
    point_cloud_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and summarize raw GraspNet evidence without making candidates."""

    frame, _, rows = _validated_graspnet_response(
        response,
        observation,
        point_cloud_manifest,
    )
    return {
        "schema": _GRASPNET_SCHEMA,
        "backend": "graspnet_baseline",
        "frame": frame,
        "grasp_count": len(rows),
        "raw_indices": [row["raw_index"] for row in rows],
        "object_ids": [row["object_id"] for row in rows],
        "requires_object_assignment": bool(rows),
    }
