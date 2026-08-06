"""Camera-to-base extrinsics and the two transforms the method path may use.

Every raw perception value in this repository is measured in the head optical
frame, while every graph geometry hole requests ``robot_base``.  Relabelling the
optical value was always refused; this module is the only sanctioned way to
actually move it, and it exists because a measured extrinsics record now exists.

Two facts decide the whole design:

1. The head camera is mounted on a prismatic ``lifting_link``.  A single static
   ``(R, t)`` is therefore only true at one lift position.  The record states the
   ``q_lift`` it was calibrated at, and consuming code must supply the ``q_lift``
   that held during the observation it is transforming.  Without that number the
   value is ``UNKNOWN`` — publishing a silently shifted pose is worse than
   publishing nothing, because the offset is invisible downstream.
2. A direction is not a point.  ``direction_to_base`` uses ``R`` only and never
   adds ``t`` or any lift term, so an axis is immune to the whole lift problem.
   Sharing one "transform" helper between the two would be the classic bug.

The module is pure Python on purpose: 3x3 arithmetic needs no NumPy, so the
offline binding path stays importable without the optional live dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


CAMERA_EXTRINSICS_SCHEMA = "demo_graph_lab.camera_extrinsics.v1"

OPTICAL_FRAME = "camera_head_optical"
BASE_FRAME = "robot_base"

PASS = "PASS"
UNKNOWN = "UNKNOWN"

# 相机挂在升降关节上,标定只在某一个 q_lift 成立。超过这个容差而无法施加修正的
# 读数一律拒绝:2mm 是 8/6 标定战役里桌高残差的量级(修正后 +0.69mm),再大就不是
# 噪声而是没被记账的升降位移。
LIFT_TOLERANCE_M = 0.002
_ROTATION_TOLERANCE = 1e-4
_UNIT_TOLERANCE = 1e-3
_LIMIT_SLACK_M = 1e-6

_REQUIRED_KEYS = {
    "schema",
    "frame_from",
    "frame_to",
    "axis_convention",
    "translation",
    "translation_unit",
    "lift_dependency",
    "method",
    "provenance",
    "validation",
}
# R 与四元数是同一件事的两种写法,只能给一个:两个都给就要回答"哪个是真的"。
_ROTATION_KEYS = {"rotation", "quaternion_xyzw"}
_LIFT_KEYS = {
    "link",
    "joint_type",
    "axis_base",
    "limits_m",
    "q_lift_assumed",
    "correction",
}
_PROVENANCE_KEYS = {"calibrated_at", "operator", "source_refs"}
_VALIDATION_KEYS = {
    "table_normal_angle_deg",
    "table_height_residual_m",
    "evidence_refs",
}
# OpenCV 光学系。R 的数值就是在这个约定下解出来的,换约定 R 直接失效,所以这里
# 不接受任何别名,只认这一组。
_AXIS_CONVENTION = {"x": "right", "y": "down", "z": "forward"}
_CORRECTIONS = {"translate_base_origin", "none"}


@dataclass(frozen=True)
class CameraExtrinsics:
    """One validated ``camera_head_optical -> robot_base`` transform."""

    rotation: tuple[tuple[float, float, float], ...]
    translation: tuple[float, float, float]
    frame_from: str
    frame_to: str
    lift_axis_base: tuple[float, float, float]
    lift_limits_m: tuple[float, float]
    q_lift_assumed: float
    lift_correction: str
    record: Mapping[str, Any]
    ref: str = ""


@dataclass(frozen=True)
class FrameValue:
    """Fail-closed result of one transform.

    Refusal is a value, not an exception: a single unusable hole must not abort
    the projection of every other hole in the same observation.
    """

    status: str
    value: tuple[float, float, float] | None
    reason: str

    def __post_init__(self) -> None:
        if self.status not in {PASS, UNKNOWN}:
            raise ValueError("frame value status must be PASS or UNKNOWN")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("frame value must carry a non-empty reason")
        if (self.status == PASS) is (self.value is None):
            raise ValueError("PASS requires a value and UNKNOWN forbids one")


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


def _number_list(value: Any, path: str, *, length: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{path} must be a JSON array of {length} numbers")
    return tuple(
        _finite_number(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )


def _references(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty JSON array")
    refs = tuple(
        _required_string(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )
    if len(refs) != len(set(refs)):
        raise ValueError(f"{path} must not repeat a reference")
    return refs


def _validated_rotation(rows: tuple[tuple[float, ...], ...], path: str):
    """Reject anything that is not a right-handed rotation.

    An improper matrix (``det = -1``) mirrors the scene, which silently flips
    every axis this repository publishes; it must never reach a hole value.
    """

    columns = tuple(
        tuple(rows[row][column] for row in range(3)) for column in range(3)
    )
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


def _rotation_from_quaternion(value: Any, path: str):
    x, y, z, w = _number_list(value, path, length=4)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=_UNIT_TOLERANCE):
        raise ValueError(f"{path} must be a unit quaternion in xyzw order")
    x, y, z, w = (item / norm for item in (x, y, z, w))
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def _validated_lift(value: Any) -> dict[str, Any]:
    lift = _exact_object(value, _LIFT_KEYS, "camera_extrinsics.lift_dependency")
    link = _required_string(lift["link"], "lift_dependency.link")
    joint_type = _required_string(lift["joint_type"], "lift_dependency.joint_type")
    if joint_type != "prismatic":
        raise ValueError("lift_dependency.joint_type must be prismatic")
    axis = _number_list(lift["axis_base"], "lift_dependency.axis_base", length=3)
    norm = math.sqrt(sum(item * item for item in axis))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=_UNIT_TOLERANCE):
        raise ValueError("lift_dependency.axis_base must be a unit vector")
    limits = _number_list(lift["limits_m"], "lift_dependency.limits_m", length=2)
    if limits[0] > limits[1]:
        raise ValueError("lift_dependency.limits_m must be ordered [low, high]")
    assumed = _finite_number(
        lift["q_lift_assumed"], "lift_dependency.q_lift_assumed"
    )
    if not limits[0] - _LIMIT_SLACK_M <= assumed <= limits[1] + _LIMIT_SLACK_M:
        raise ValueError("lift_dependency.q_lift_assumed is outside limits_m")
    correction = _required_string(lift["correction"], "lift_dependency.correction")
    if correction not in _CORRECTIONS:
        raise ValueError(
            f"lift_dependency.correction must be one of {sorted(_CORRECTIONS)}"
        )
    return {
        "link": link,
        "joint_type": joint_type,
        "axis_base": list(axis),
        "limits_m": list(limits),
        "q_lift_assumed": assumed,
        "correction": correction,
    }


def validate_camera_extrinsics_record(record: Mapping[str, Any]) -> CameraExtrinsics:
    """Validate one measured extrinsics record with no implicit defaults.

    The record is a physical claim, so every part of it is checked: the frame
    pair, the axis convention the numbers were solved in, ``SO(3)`` membership
    including ``det = +1``, the unit, and the lift position the calibration
    assumed.  ``method``/``provenance``/``validation`` are required because a
    transform without a traceable origin cannot be audited later.
    """

    root = _object(record, "camera_extrinsics")
    present_rotation = sorted(_ROTATION_KEYS & set(root))
    if len(present_rotation) != 1:
        raise ValueError(
            "camera_extrinsics must contain exactly one of "
            f"{sorted(_ROTATION_KEYS)}, found {present_rotation}"
        )
    _exact_object(
        {key: item for key, item in root.items() if key not in _ROTATION_KEYS},
        _REQUIRED_KEYS,
        "camera_extrinsics",
    )
    if root["schema"] != CAMERA_EXTRINSICS_SCHEMA:
        raise ValueError(f"unknown camera extrinsics schema: {root['schema']!r}")
    frame_from = _required_string(root["frame_from"], "camera_extrinsics.frame_from")
    frame_to = _required_string(root["frame_to"], "camera_extrinsics.frame_to")
    if frame_from != OPTICAL_FRAME or frame_to != BASE_FRAME:
        raise ValueError(
            f"camera_extrinsics must map {OPTICAL_FRAME} to {BASE_FRAME}"
        )
    convention = _exact_object(
        root["axis_convention"], set(_AXIS_CONVENTION), "camera_extrinsics.axis_convention"
    )
    if dict(convention) != _AXIS_CONVENTION:
        raise ValueError(
            "camera_extrinsics.axis_convention must be the OpenCV optical "
            f"convention {_AXIS_CONVENTION}"
        )
    if "rotation" in root:
        raw = root["rotation"]
        if not isinstance(raw, list) or len(raw) != 3:
            raise ValueError("camera_extrinsics.rotation must be a 3x3 JSON array")
        rows = tuple(
            _number_list(row, f"camera_extrinsics.rotation[{index}]", length=3)
            for index, row in enumerate(raw)
        )
    else:
        rows = _rotation_from_quaternion(
            root["quaternion_xyzw"], "camera_extrinsics.quaternion_xyzw"
        )
    rotation = _validated_rotation(rows, "camera_extrinsics.rotation")
    if root["translation_unit"] != "meter":
        raise ValueError("camera_extrinsics.translation_unit must be meter")
    translation = _number_list(
        root["translation"], "camera_extrinsics.translation", length=3
    )
    lift = _validated_lift(root["lift_dependency"])
    _required_string(root["method"], "camera_extrinsics.method")
    provenance = _exact_object(
        root["provenance"], _PROVENANCE_KEYS, "camera_extrinsics.provenance"
    )
    _required_string(provenance["calibrated_at"], "provenance.calibrated_at")
    _required_string(provenance["operator"], "provenance.operator")
    _references(provenance["source_refs"], "provenance.source_refs")
    validation = _exact_object(
        root["validation"], _VALIDATION_KEYS, "camera_extrinsics.validation"
    )
    angle = _finite_number(
        validation["table_normal_angle_deg"], "validation.table_normal_angle_deg"
    )
    if angle < 0.0:
        raise ValueError("validation.table_normal_angle_deg must be non-negative")
    _finite_number(
        validation["table_height_residual_m"], "validation.table_height_residual_m"
    )
    _references(validation["evidence_refs"], "validation.evidence_refs")

    return CameraExtrinsics(
        rotation=tuple(tuple(row) for row in rotation),
        translation=translation,
        frame_from=frame_from,
        frame_to=frame_to,
        lift_axis_base=tuple(lift["axis_base"]),
        lift_limits_m=(lift["limits_m"][0], lift["limits_m"][1]),
        q_lift_assumed=lift["q_lift_assumed"],
        lift_correction=lift["correction"],
        record=dict(root),
    )


def load_camera_extrinsics(path: str | Path) -> CameraExtrinsics:
    """Read and validate one extrinsics record, remembering where it came from."""

    import json

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"camera extrinsics record does not exist: {resolved}")

    def reject_constant(value: str):
        raise ValueError(f"non-finite JSON number {value!r} is not allowed")

    record = json.loads(
        resolved.read_text("utf-8"), parse_constant=reject_constant
    )
    extrinsics = validate_camera_extrinsics_record(record)
    return CameraExtrinsics(
        rotation=extrinsics.rotation,
        translation=extrinsics.translation,
        frame_from=extrinsics.frame_from,
        frame_to=extrinsics.frame_to,
        lift_axis_base=extrinsics.lift_axis_base,
        lift_limits_m=extrinsics.lift_limits_m,
        q_lift_assumed=extrinsics.q_lift_assumed,
        lift_correction=extrinsics.lift_correction,
        record=extrinsics.record,
        ref=str(resolved),
    )


def _camera_vector(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, Mapping) or isinstance(value, (str, bytes)):
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    if len(items) != 3:
        return None
    numbers = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        numbers.append(number)
    return (numbers[0], numbers[1], numbers[2])


def _rotate(extrinsics: CameraExtrinsics, vector) -> tuple[float, float, float]:
    rows = extrinsics.rotation
    return tuple(
        rows[index][0] * vector[0]
        + rows[index][1] * vector[1]
        + rows[index][2] * vector[2]
        for index in range(3)
    )


def lift_offset(
    extrinsics: CameraExtrinsics, q_lift: float | None
) -> FrameValue:
    """Resolve the base-frame translation the current lift position adds.

    This is the whole rejection rule in one place.  A missing ``q_lift`` and a
    ``q_lift`` the record cannot correct for are both refusals, never a zero
    offset: silently assuming the calibration pose is exactly how a pose ends up
    wrong by the lift travel without anything in the record showing it.
    """

    if q_lift is None:
        return FrameValue(UNKNOWN, None, "q_lift_unavailable")
    if isinstance(q_lift, bool) or not isinstance(q_lift, (int, float)):
        return FrameValue(UNKNOWN, None, "q_lift_unavailable")
    value = float(q_lift)
    if not math.isfinite(value):
        return FrameValue(UNKNOWN, None, "q_lift_unavailable")
    low, high = extrinsics.lift_limits_m
    if not low - _LIMIT_SLACK_M <= value <= high + _LIMIT_SLACK_M:
        return FrameValue(UNKNOWN, None, "q_lift_out_of_limits")
    delta = value - extrinsics.q_lift_assumed
    if extrinsics.lift_correction == "none":
        if abs(delta) > LIFT_TOLERANCE_M:
            return FrameValue(UNKNOWN, None, "q_lift_correction_unavailable")
        return FrameValue(PASS, (0.0, 0.0, 0.0), "lift_within_calibration_tolerance")
    axis = extrinsics.lift_axis_base
    return FrameValue(
        PASS,
        (axis[0] * delta, axis[1] * delta, axis[2] * delta),
        "lift_corrected_from_calibration_pose",
    )


def point_to_base(
    point_cam, extrinsics: CameraExtrinsics, q_lift: float | None
) -> FrameValue:
    """Transform one optical-frame point into ``robot_base``.

    ``t_eff = t + axis * (q_lift - q_lift_assumed)``.  With the 8/6 calibration
    baseline ``q_lift_assumed = 0`` this is exactly ``t + [0, 0, q_lift]``.
    """

    vector = _camera_vector(point_cam)
    if vector is None:
        return FrameValue(UNKNOWN, None, "invalid_camera_point")
    offset = lift_offset(extrinsics, q_lift)
    if offset.status != PASS:
        return offset
    rotated = _rotate(extrinsics, vector)
    translation = extrinsics.translation
    return FrameValue(
        PASS,
        tuple(
            rotated[index] + translation[index] + offset.value[index]
            for index in range(3)
        ),
        "transformed_with_lift_corrected_translation",
    )


def direction_to_base(direction_cam, extrinsics: CameraExtrinsics) -> FrameValue:
    """Rotate one optical-frame direction into ``robot_base``.

    A direction has no origin, so it takes ``R`` alone: adding ``t`` (or any lift
    term) would turn a unit axis into a point-like vector whose length and
    heading both depend on where the camera happens to sit.  This function
    therefore needs no ``q_lift`` at all, and axis holes stay usable at any lift
    position.  The result is renormalized because ``R`` is only orthonormal to
    the tolerance the record was checked against.
    """

    vector = _camera_vector(direction_cam)
    if vector is None:
        return FrameValue(UNKNOWN, None, "invalid_camera_direction")
    norm = math.sqrt(sum(item * item for item in vector))
    if norm < _UNIT_TOLERANCE:
        return FrameValue(UNKNOWN, None, "degenerate_camera_direction")
    rotated = _rotate(extrinsics, vector)
    rotated_norm = math.sqrt(sum(item * item for item in rotated))
    if rotated_norm < _UNIT_TOLERANCE:
        return FrameValue(UNKNOWN, None, "degenerate_camera_direction")
    return FrameValue(
        PASS,
        tuple(item / rotated_norm for item in rotated),
        "rotated_without_translation",
    )
