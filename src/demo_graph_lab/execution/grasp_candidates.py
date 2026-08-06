"""Normalize graph-bound GraspNet proposals into robot-base EEF candidates.

Raw GraspNet rows describe a parallel-jaw grasp frame in the head optical frame;
they are not runtime TCP poses.  Conversion is allowed only with three pieces
of evidence from the same frozen observation: the object-only point cloud, the
lift-aware camera extrinsics, and an explicit pose of ``runtime_ee`` expressed
in the GraspNet frame.  Missing object identity acceptance or calibration is a
hard refusal, not a default transform.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..perception import ObjectObservation, ObservationPacket
from ..perception.adapters import _validated_graspnet_response
from ..perception.frames import (
    BASE_FRAME,
    PASS,
    CameraExtrinsics,
    direction_to_base,
    lift_offset,
    point_to_base,
)
from ..perception.operators import fit_principal_axis
from ..selection.candidates import CandidateBundle, CheckCertificate, CheckStatus, HardCheck


TOOL_TRANSFORM_SCHEMA = "demo_graph_lab.graspnet_runtime_ee_transform.v1"
_TOOL_KEYS = {
    "schema", "runtime_ee_in_graspnet", "gripper", "evidence_refs",
}
_POSE_KEYS = {"rotation", "translation_m"}
_GRIPPER_KEYS = {"max_opening_m", "axial_extent_m"}


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _vector(value: Any, length: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    return tuple(_finite_number(item, f"{name}[{index}]") for index, item in enumerate(value))


def _rotation(value: Any, name: str):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must be a 3x3 matrix")
    rows = tuple(_vector(row, 3, f"{name}[{index}]") for index, row in enumerate(value))
    columns = tuple(zip(*rows))
    for column in columns:
        if not math.isclose(sum(item * item for item in column), 1.0, abs_tol=1e-4):
            raise ValueError(f"{name} columns must be unit length")
    if any(abs(sum(a * b for a, b in zip(columns[i], columns[j]))) > 1e-4
           for i in range(3) for j in range(i + 1, 3)):
        raise ValueError(f"{name} columns must be orthogonal")
    determinant = (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )
    if not math.isclose(determinant, 1.0, abs_tol=1e-4):
        raise ValueError(f"{name} determinant must be +1")
    return rows


def _exact(value: Any, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _refs(value: Any, name: str) -> tuple[str, ...]:
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)):
        raise ValueError(f"{name} must be a non-empty list of references")
    refs = tuple(value)
    if len(refs) != len(set(refs)):
        raise ValueError(f"{name} must not repeat references")
    return refs


@dataclass(frozen=True)
class GraspToolTransform:
    """Pose of runtime EEF coordinates expressed in a GraspNet grasp frame."""

    rotation_graspnet_from_ee: tuple[tuple[float, float, float], ...]
    translation_graspnet_from_ee_m: tuple[float, float, float]
    max_opening_m: float
    axial_extent_m: float
    evidence_refs: tuple[str, ...]
    ref: str = ""


def validate_tool_transform_record(record: Mapping[str, Any]) -> GraspToolTransform:
    root = _exact(record, _TOOL_KEYS, "tool_transform")
    if root["schema"] != TOOL_TRANSFORM_SCHEMA:
        raise ValueError(f"unsupported tool transform schema: {root['schema']!r}")
    pose = _exact(root["runtime_ee_in_graspnet"], _POSE_KEYS, "runtime_ee_in_graspnet")
    gripper = _exact(root["gripper"], _GRIPPER_KEYS, "gripper")
    max_opening = _finite_number(gripper["max_opening_m"], "gripper.max_opening_m")
    axial_extent = _finite_number(
        gripper["axial_extent_m"], "gripper.axial_extent_m")
    if max_opening <= 0.0 or axial_extent <= 0.0:
        raise ValueError("gripper dimensions must be positive")
    return GraspToolTransform(
        rotation_graspnet_from_ee=_rotation(
            pose["rotation"], "runtime_ee_in_graspnet.rotation"),
        translation_graspnet_from_ee_m=_vector(
            pose["translation_m"], 3, "runtime_ee_in_graspnet.translation_m"),
        max_opening_m=max_opening,
        axial_extent_m=axial_extent,
        evidence_refs=_refs(root["evidence_refs"], "tool_transform.evidence_refs"),
    )


def load_tool_transform(path: str | Path) -> GraspToolTransform:
    resolved = Path(path).resolve()
    record = json.loads(resolved.read_text("utf-8"))
    transform = validate_tool_transform_record(record)
    return GraspToolTransform(
        rotation_graspnet_from_ee=transform.rotation_graspnet_from_ee,
        translation_graspnet_from_ee_m=transform.translation_graspnet_from_ee_m,
        max_opening_m=transform.max_opening_m,
        axial_extent_m=transform.axial_extent_m,
        evidence_refs=transform.evidence_refs,
        ref=str(resolved),
    )


def _matmul(first, second):
    columns = tuple(zip(*second))
    return tuple(
        tuple(sum(a * b for a, b in zip(row, column)) for column in columns)
        for row in first
    )


def _matvec(matrix, value: Sequence[float]) -> tuple[float, float, float]:
    return tuple(sum(a * b for a, b in zip(row, value)) for row in matrix)  # type: ignore[return-value]


def _quat_xyzw(matrix) -> tuple[float, float, float, float]:
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        result = (
            (matrix[2][1] - matrix[1][2]) / scale,
            (matrix[0][2] - matrix[2][0]) / scale,
            (matrix[1][0] - matrix[0][1]) / scale,
            0.25 * scale,
        )
    else:
        diagonal = [matrix[0][0], matrix[1][1], matrix[2][2]]
        index = max(range(3), key=diagonal.__getitem__)
        if index == 0:
            scale = math.sqrt(1.0 + diagonal[0] - diagonal[1] - diagonal[2]) * 2.0
            result = (0.25 * scale, (matrix[0][1] + matrix[1][0]) / scale,
                      (matrix[0][2] + matrix[2][0]) / scale,
                      (matrix[2][1] - matrix[1][2]) / scale)
        elif index == 1:
            scale = math.sqrt(1.0 + diagonal[1] - diagonal[0] - diagonal[2]) * 2.0
            result = ((matrix[0][1] + matrix[1][0]) / scale, 0.25 * scale,
                      (matrix[1][2] + matrix[2][1]) / scale,
                      (matrix[0][2] - matrix[2][0]) / scale)
        else:
            scale = math.sqrt(1.0 + diagonal[2] - diagonal[0] - diagonal[1]) * 2.0
            result = ((matrix[0][2] + matrix[2][0]) / scale,
                      (matrix[1][2] + matrix[2][1]) / scale, 0.25 * scale,
                      (matrix[1][0] - matrix[0][1]) / scale)
    norm = math.sqrt(sum(item * item for item in result))
    return tuple(item / norm for item in result)  # type: ignore[return-value]


@dataclass(frozen=True)
class GraspCandidateSet:
    observation: ObservationPacket
    candidates: tuple[CandidateBundle, ...]
    object_center: tuple[float, float, float]
    object_axis: tuple[float, float, float]
    object_length_m: float
    object_radius_m: float


def normalize_graspnet_candidates(
    response: Mapping[str, Any],
    *,
    observation: ObservationPacket,
    point_cloud_manifest: Mapping[str, Any],
    object_points,
    stage: Mapping[str, Any],
    object_id: str,
    identity_accepted: bool,
    identity_evidence_ref: str,
    camera_extrinsics: CameraExtrinsics,
    q_lift: float | None,
    tool_transform: GraspToolTransform,
    raw_response_ref: str,
) -> GraspCandidateSet:
    """Create EEF candidates and a matching robot-base observation."""

    if identity_accepted is not True:
        raise PermissionError("graph object identity has not been accepted")
    for value, name in (
        (object_id, "object_id"),
        (identity_evidence_ref, "identity_evidence_ref"),
        (raw_response_ref, "raw_response_ref"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty reference")
    if not camera_extrinsics.ref:
        raise ValueError("camera extrinsics must come from a referenced record")
    if not tool_transform.ref:
        raise ValueError("tool transform must come from a referenced record")

    frame, cloud_evidence, rows = _validated_graspnet_response(
        response, observation, point_cloud_manifest)
    if frame != camera_extrinsics.frame_from:
        raise ValueError("GraspNet frame does not match camera extrinsics")
    grasp_holes = [
        hole for hole in stage.get("holes", [])
        if isinstance(hole, Mapping) and hole.get("resolver") == "grasp_candidate"
    ]
    if len(grasp_holes) != 1:
        raise ValueError("stage must declare exactly one grasp_candidate hole")
    grasp_hole = grasp_holes[0]
    if (grasp_hole.get("frame") != BASE_FRAME
            or (grasp_hole.get("anchor") or {}).get("object_id") != object_id):
        raise ValueError("grasp hole does not match robot_base graph object")

    import numpy as np

    points = np.asarray(object_points)
    if (points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 3
            or not np.issubdtype(points.dtype, np.number)
            or not np.isfinite(points).all()):
        raise ValueError("object_points must be a finite Nx3 numeric array")
    axis_camera = np.asarray(fit_principal_axis(points), dtype=np.float64)
    center_camera = points.astype(np.float64).mean(axis=0)
    axial = (points.astype(np.float64) - center_camera) @ axis_camera
    axial_min, axial_max = float(axial.min()), float(axial.max())
    object_length = axial_max - axial_min
    if object_length <= 1e-6:
        raise ValueError("object point cloud has no axial length")
    radial = points.astype(np.float64) - center_camera - np.outer(axial, axis_camera)
    object_radius = float(np.quantile(np.linalg.norm(radial, axis=1), 0.95))
    if object_radius <= 0.0:
        raise ValueError("object point cloud has no radial extent")

    lift = lift_offset(camera_extrinsics, q_lift)
    if lift.status != PASS:
        raise ValueError(f"camera transform unavailable:{lift.reason}")
    rotation_base_camera = camera_extrinsics.rotation
    points_base = (
        points.astype(np.float64) @ np.asarray(rotation_base_camera).T
        + np.asarray(camera_extrinsics.translation)
        + np.asarray(lift.value)
    )
    center_result = point_to_base(center_camera, camera_extrinsics, q_lift)
    axis_result = direction_to_base(axis_camera, camera_extrinsics)
    if center_result.status != PASS or axis_result.status != PASS:
        raise ValueError("object geometry could not be transformed to robot_base")
    object_center = center_result.value
    object_axis = axis_result.value
    extent = {
        "min": tuple(float(item) for item in points_base.min(axis=0)),
        "max": tuple(float(item) for item in points_base.max(axis=0)),
    }
    vertical_min = extent["min"][2]
    vertical_span = extent["max"][2] - vertical_min
    if vertical_span <= 1e-6:
        raise ValueError("object point cloud has no vertical extent")
    evidence = tuple(dict.fromkeys((
        raw_response_ref,
        cloud_evidence,
        identity_evidence_ref,
        camera_extrinsics.ref,
        tool_transform.ref,
        *tool_transform.evidence_refs,
    )))
    base_observation = ObservationPacket(
        observation_id=observation.observation_id,
        captured_at_s=observation.captured_at_s,
        frame=BASE_FRAME,
        calibration_ref=camera_extrinsics.ref,
        sensor_refs=tuple(dict.fromkeys((*observation.sensor_refs, *evidence))),
        robot_state=observation.robot_state,
        objects=(ObjectObservation(
            object_id=object_id,
            frame=BASE_FRAME,
            axis=object_axis,
            extent=extent,
            evidence_refs=evidence,
        ),),
    )

    candidates = []
    for row in rows:
        rotation_camera_grasp = row["rotation"]
        offset_camera = _matvec(
            rotation_camera_grasp,
            tool_transform.translation_graspnet_from_ee_m,
        )
        ee_camera = tuple(
            row["translation"][index] + offset_camera[index]
            for index in range(3)
        )
        ee_base = point_to_base(ee_camera, camera_extrinsics, q_lift)
        grasp_center_base = point_to_base(
            row["translation"], camera_extrinsics, q_lift)
        approach_base = direction_to_base(
            tuple(rotation_camera_grasp[index][0] for index in range(3)),
            camera_extrinsics,
        )
        if (ee_base.status != PASS or grasp_center_base.status != PASS
                or approach_base.status != PASS):
            continue
        rotation_camera_ee = _matmul(
            rotation_camera_grasp,
            tool_transform.rotation_graspnet_from_ee,
        )
        rotation_base_ee = _matmul(rotation_base_camera, rotation_camera_ee)
        quaternion = _quat_xyzw(rotation_base_ee)
        # Region preferences are defined along robot-base vertical, not along
        # the object's PCA axis.  Use the GraspNet contact center (not the EEF
        # position, which includes the calibrated tool offset) against the
        # observed object's world-z extent.
        raw_fraction = (grasp_center_base.value[2] - vertical_min) / vertical_span
        height_fraction = max(0.0, min(1.0, raw_fraction))
        approach_tilt = math.degrees(math.acos(max(
            -1.0, min(1.0, -approach_base.value[2])
        )))
        hole_values = {
            grasp_hole["name"]: {
                "value": [*ee_base.value, *quaternion],
                "frame": BASE_FRAME,
                "calibration_ref": camera_extrinsics.ref,
                "object_id": object_id,
            },
        }
        for hole in stage.get("holes", []):
            if (isinstance(hole, Mapping)
                    and hole.get("resolver") == "principal_axis"
                    and (hole.get("anchor") or {}).get("object_id") == object_id):
                hole_values[hole["name"]] = {
                    "value": list(object_axis),
                    "frame": BASE_FRAME,
                    "calibration_ref": camera_extrinsics.ref,
                    "object_id": object_id,
                }
        candidates.append(CandidateBundle(
            candidate_id=f"graspnet:{row['raw_index']:04d}",
            observation_id=observation.observation_id,
            hole_values=hole_values,
            features={
                "detector_score": row["score"],
                "width_m": row["dimensions"]["width"],
                "height_m": row["dimensions"]["height"],
                "depth_m": row["dimensions"]["depth"],
                "height_fraction": height_fraction,
                "raw_height_fraction": raw_fraction,
                "approach_tilt_deg": approach_tilt,
                "object_center_base": list(object_center),
                "object_axis_base": list(object_axis),
                "object_length_m": object_length,
                "object_radius_m": object_radius,
                "grasp_center_base": list(grasp_center_base.value),
            },
            provenance={
                "source": "graspnet_baseline",
                "raw_index": row["raw_index"],
                "raw_object_id": row["object_id"],
                "graph_object_id": object_id,
                "identity_evidence_ref": identity_evidence_ref,
                "camera_extrinsics_ref": camera_extrinsics.ref,
                "tool_transform_ref": tool_transform.ref,
                "q_lift_m": q_lift,
            },
            evidence_refs=evidence,
        ))
    return GraspCandidateSet(
        observation=base_observation,
        candidates=tuple(candidates),
        object_center=object_center,
        object_axis=object_axis,
        object_length_m=object_length,
        object_radius_m=object_radius,
    )


def calibrated_gripper_width_check(tool_transform: GraspToolTransform) -> HardCheck:
    """Create the immediate width check from the same measured tool record."""

    def evaluate(candidate: CandidateBundle, observation: ObservationPacket):
        width = candidate.features.get("width_m")
        if isinstance(width, bool) or not isinstance(width, (int, float)):
            return CheckCertificate(
                check="gripper_width",
                status=CheckStatus.UNKNOWN,
                reason="candidate_width_unavailable",
            )
        margin = tool_transform.max_opening_m - float(width)
        status = CheckStatus.PASS if margin >= 0.0 else CheckStatus.FAIL
        return CheckCertificate(
            check="gripper_width",
            status=status,
            reason=f"calibrated_opening_margin_m={margin:.6f}",
            evidence_refs=tuple(dict.fromkeys((
                tool_transform.ref, *tool_transform.evidence_refs,
                *candidate.evidence_refs,
            ))),
        )

    return HardCheck(name="gripper_width", evaluate=evaluate)
