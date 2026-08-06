"""Candidate-dependent downstream geometry and motion-plan compatibility.

The generated CaP still says only ``require_future(constraint_ref)``.  This
module is the trusted implementation behind that call: it keeps the selected
grasp rigidly attached to the perceived object, moves the object to an observed
opening, checks insertion/clearance geometry, and asks a motion planner whether
the resulting grasp -> pre-insert -> insert wrist path exists.

No result is read from ``CandidateBundle.features``.  Features may contain raw
measurements such as detector score or height fraction, but PASS/FAIL/UNKNOWN is
created here from geometry and planner evidence for the current observation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import time
from typing import Callable, Mapping, Sequence

from ..perception import ObservationPacket
from ..selection.candidates import (
    CandidateBundle,
    CheckCertificate,
    CheckStatus,
    HardCheck,
)
from . import robot_api


_EPS = 1e-9


class GeometryUnavailable(ValueError):
    """Raised when observed downstream geometry cannot answer a query."""


def _finite_vector(name: str, value: Sequence[float], length: int) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain finite numbers")
    return result


def _positive(name: str, value: float, *, allow_zero: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or (result < 0.0 if allow_zero else result <= 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be finite and {qualifier}")
    return result


def _unit(name: str, value: Sequence[float]) -> tuple[float, float, float]:
    vector = _finite_vector(name, value, 3)
    norm = math.sqrt(sum(item * item for item in vector))
    if norm <= _EPS:
        raise ValueError(f"{name} must be non-zero")
    return tuple(item / norm for item in vector)  # type: ignore[return-value]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _add(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return tuple(x + y for x, y in zip(a, b))  # type: ignore[return-value]


def _sub(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return tuple(x - y for x, y in zip(a, b))  # type: ignore[return-value]


def _scale(value: Sequence[float], factor: float) -> tuple[float, float, float]:
    return tuple(factor * item for item in value)  # type: ignore[return-value]


def _matvec(matrix, value: Sequence[float]) -> tuple[float, float, float]:
    return tuple(_dot(row, value) for row in matrix)  # type: ignore[return-value]


def _matmul(first, second):
    columns = tuple(zip(*second))
    return tuple(tuple(_dot(row, column) for column in columns) for row in first)


def _transpose(matrix):
    return tuple(tuple(row[index] for row in matrix) for index in range(3))


def _rotation_from_quaternion(value: Sequence[float]):
    x, y, z, w = _finite_vector("grasp quaternion", value, 4)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
        raise GeometryUnavailable("candidate grasp quaternion is not unit length")
    x, y, z, w = (item / norm for item in (x, y, z, w))
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def _quaternion_from_rotation(matrix) -> tuple[float, float, float, float]:
    """Convert a proper rotation matrix to a normalized xyzw quaternion."""

    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (matrix[2][1] - matrix[1][2]) / scale
        y = (matrix[0][2] - matrix[2][0]) / scale
        z = (matrix[1][0] - matrix[0][1]) / scale
    else:
        diagonal = [matrix[0][0], matrix[1][1], matrix[2][2]]
        index = max(range(3), key=diagonal.__getitem__)
        if index == 0:
            scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2.0
            w = (matrix[2][1] - matrix[1][2]) / scale
            x = 0.25 * scale
            y = (matrix[0][1] + matrix[1][0]) / scale
            z = (matrix[0][2] + matrix[2][0]) / scale
        elif index == 1:
            scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2.0
            w = (matrix[0][2] - matrix[2][0]) / scale
            x = (matrix[0][1] + matrix[1][0]) / scale
            y = 0.25 * scale
            z = (matrix[1][2] + matrix[2][1]) / scale
        else:
            scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2.0
            w = (matrix[1][0] - matrix[0][1]) / scale
            x = (matrix[0][2] + matrix[2][0]) / scale
            y = (matrix[1][2] + matrix[2][1]) / scale
            z = 0.25 * scale
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    return (x / norm, y / norm, z / norm, w / norm)


def _align_rotation(source: Sequence[float], target: Sequence[float]):
    """Return the shortest proper rotation taking one unit axis to another."""

    source_u, target_u = _unit("object_axis", source), _unit("target_axis", target)
    cosine = max(-1.0, min(1.0, _dot(source_u, target_u)))
    if cosine > 1.0 - 1e-9:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    if cosine < -1.0 + 1e-9:
        trial = (1.0, 0.0, 0.0) if abs(source_u[0]) < 0.9 else (0.0, 1.0, 0.0)
        axis = _unit("antiparallel_rotation_axis", _cross(source_u, trial))
        x, y, z = axis
        return (
            (2 * x * x - 1, 2 * x * y, 2 * x * z),
            (2 * x * y, 2 * y * y - 1, 2 * y * z),
            (2 * x * z, 2 * y * z, 2 * z * z - 1),
        )
    axis = _cross(source_u, target_u)
    sine = math.sqrt(_dot(axis, axis))
    x, y, z = (item / sine for item in axis)
    one_minus = 1.0 - cosine
    return (
        (cosine + x * x * one_minus, x * y * one_minus - z * sine, x * z * one_minus + y * sine),
        (y * x * one_minus + z * sine, cosine + y * y * one_minus, y * z * one_minus - x * sine),
        (z * x * one_minus - y * sine, z * y * one_minus + x * sine, cosine + z * z * one_minus),
    )


@dataclass(frozen=True)
class InsertionGeometry:
    """Observed geometry and calibrated dimensions for one future insertion."""

    object_id: str
    object_center: tuple[float, float, float]
    object_axis: tuple[float, float, float]
    object_length_m: float
    object_radius_m: float
    target_center: tuple[float, float, float]
    target_axis: tuple[float, float, float]
    opening_radius_m: float
    insertion_depth_m: float
    preinsert_gap_m: float
    gripper_axial_extent_m: float
    clearance_margin_m: float
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id.strip():
            raise ValueError("object_id must be a non-empty string")
        object.__setattr__(self, "object_center", _finite_vector(
            "object_center", self.object_center, 3))
        object.__setattr__(self, "object_axis", _unit("object_axis", self.object_axis))
        object.__setattr__(self, "target_center", _finite_vector(
            "target_center", self.target_center, 3))
        target_axis = _unit("target_axis", self.target_axis)
        # Cylindrical openings are sign-symmetric.  Use the upward sign so
        # pre-insert and clearance distances have one physical meaning.
        if _dot(target_axis, (0.0, 0.0, 1.0)) < 0.0:
            target_axis = _scale(target_axis, -1.0)
        object.__setattr__(self, "target_axis", target_axis)
        for name in (
            "object_length_m", "object_radius_m", "opening_radius_m",
            "insertion_depth_m", "gripper_axial_extent_m",
        ):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        for name in ("preinsert_gap_m", "clearance_margin_m"):
            object.__setattr__(self, name, _positive(
                name, getattr(self, name), allow_zero=True))
        if self.insertion_depth_m > self.object_length_m:
            raise ValueError("insertion_depth_m cannot exceed object_length_m")
        if (not isinstance(self.evidence_refs, tuple) or not self.evidence_refs
                or any(not isinstance(ref, str) or not ref.strip()
                       for ref in self.evidence_refs)):
            raise ValueError("insertion geometry requires evidence_refs")


@dataclass(frozen=True)
class ContinuationRequest:
    constraint_stage_index: int
    grasp_pose: tuple[float, ...]
    preinsert_pose: tuple[float, ...]
    inserted_pose: tuple[float, ...]
    grasp_item: Mapping[str, object]


@dataclass(frozen=True)
class ContinuationResult:
    status: CheckStatus
    reason: str
    evidence_refs: tuple[str, ...] = ()
    planning_calls: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, CheckStatus):
            raise TypeError("continuation status must be CheckStatus")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("continuation result needs a reason")
        if self.status is not CheckStatus.UNKNOWN and not self.evidence_refs:
            raise ValueError("PASS/FAIL continuation needs evidence_refs")
        if self.planning_calls < 0:
            raise ValueError("planning_calls must be non-negative")


GeometryProvider = Callable[
    [Mapping, ObservationPacket, CandidateBundle], InsertionGeometry
]
ContinuationPlanner = Callable[
    [ContinuationRequest, CandidateBundle, ObservationPacket], ContinuationResult
]


@dataclass(frozen=True)
class InsertionTarget:
    """Observed opening pose plus measured task/tool dimensions."""

    center: tuple[float, float, float]
    axis: tuple[float, float, float]
    opening_radius_m: float
    insertion_depth_m: float
    preinsert_gap_m: float
    clearance_margin_m: float
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "center", _finite_vector("target.center", self.center, 3))
        object.__setattr__(self, "axis", _unit("target.axis", self.axis))
        for name in ("opening_radius_m", "insertion_depth_m"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        for name in ("preinsert_gap_m", "clearance_margin_m"):
            object.__setattr__(self, name, _positive(
                name, getattr(self, name), allow_zero=True))
        if (not isinstance(self.evidence_refs, tuple) or not self.evidence_refs
                or any(not isinstance(ref, str) or not ref.strip()
                       for ref in self.evidence_refs)):
            raise ValueError("insertion target requires evidence_refs")


class CandidateInsertionGeometry:
    """Join normalized grasp-object geometry with observed target geometry."""

    def __init__(
        self,
        targets_by_stage: Mapping[int, InsertionTarget],
        *,
        gripper_axial_extent_m: float,
        gripper_evidence_refs: tuple[str, ...],
    ) -> None:
        self._targets = dict(targets_by_stage)
        if (not self._targets
                or any(isinstance(index, bool) or not isinstance(index, int)
                       for index in self._targets)
                or any(not isinstance(value, InsertionTarget)
                       for value in self._targets.values())):
            raise ValueError("targets_by_stage must map stage indexes to InsertionTarget")
        self._gripper_axial_extent = _positive(
            "gripper_axial_extent_m", gripper_axial_extent_m)
        if (not isinstance(gripper_evidence_refs, tuple)
                or not gripper_evidence_refs
                or any(not isinstance(ref, str) or not ref.strip()
                       for ref in gripper_evidence_refs)):
            raise ValueError("gripper geometry requires evidence_refs")
        self._gripper_refs = gripper_evidence_refs

    def __call__(
        self,
        future_stage: Mapping,
        observation: ObservationPacket,
        candidate: CandidateBundle,
    ) -> InsertionGeometry:
        index = future_stage.get("index")
        target = self._targets.get(index)
        if target is None:
            raise GeometryUnavailable(f"no insertion target for stage {index}")
        manipulated = (future_stage.get("stage_objects") or {}).get("manipulated")
        values = candidate.features
        try:
            center = _finite_vector(
                "candidate.object_center_base", values["object_center_base"], 3)
            axis = _unit("candidate.object_axis_base", values["object_axis_base"])
            length = _positive("candidate.object_length_m", values["object_length_m"])
            radius = _positive("candidate.object_radius_m", values["object_radius_m"])
        except (KeyError, TypeError, ValueError) as error:
            raise GeometryUnavailable(f"candidate_object_geometry:{error}") from error
        return InsertionGeometry(
            object_id=str(manipulated),
            object_center=center,
            object_axis=axis,
            object_length_m=length,
            object_radius_m=radius,
            target_center=target.center,
            target_axis=target.axis,
            opening_radius_m=target.opening_radius_m,
            insertion_depth_m=target.insertion_depth_m,
            preinsert_gap_m=target.preinsert_gap_m,
            gripper_axial_extent_m=self._gripper_axial_extent,
            clearance_margin_m=target.clearance_margin_m,
            evidence_refs=tuple(dict.fromkeys((
                *target.evidence_refs, *self._gripper_refs,
            ))),
        )


class InsertionCompatibility:
    """Evaluate graph constraints against a rigid-grasp insertion continuation."""

    _SUPPORTED = {
        "axis_vertical", "axis_parallel", "inside", "center_align",
        "above", "approach_direction", "clearance",
    }

    def __init__(
        self,
        geometry_provider: GeometryProvider,
        planner: ContinuationPlanner,
        *,
        axis_tolerance_deg: float = 10.0,
    ) -> None:
        self._geometry_provider = geometry_provider
        self._planner = planner
        self._axis_tolerance_deg = _positive(
            "axis_tolerance_deg", axis_tolerance_deg)
        self._plan_cache: dict[tuple[str, str, int], ContinuationResult] = {}

    @staticmethod
    def _grasp_pose(candidate: CandidateBundle, current_stage: Mapping) -> tuple[float, ...]:
        holes = [
            hole["name"] for hole in current_stage.get("holes", [])
            if isinstance(hole, Mapping) and hole.get("resolver") == "grasp_candidate"
        ]
        bound = [name for name in holes if name in candidate.hole_values]
        if len(bound) != 1:
            raise GeometryUnavailable(
                "current stage must bind exactly one grasp_candidate pose"
            )
        envelope = candidate.hole_values[bound[0]]
        if not isinstance(envelope, Mapping):
            raise GeometryUnavailable("grasp candidate value is not an envelope")
        if envelope.get("frame") != "robot_base":
            raise GeometryUnavailable("grasp pose is not in robot_base")
        pose = _finite_vector("grasp_pose", envelope.get("value", ()), 7)
        _rotation_from_quaternion(pose[3:])
        return pose

    @staticmethod
    def _request(
        grasp_pose: tuple[float, ...],
        geometry: InsertionGeometry,
        future_stage: Mapping,
    ) -> ContinuationRequest:
        object_axis = geometry.object_axis
        target_axis = geometry.target_axis
        # A tube axis has no semantic sign.  Align the nearer sign to avoid an
        # artificial 180-degree wrist rotation.
        if _dot(object_axis, target_axis) < 0.0:
            object_axis = _scale(object_axis, -1.0)
        alignment = _align_rotation(object_axis, target_axis)
        grasp_rotation = _rotation_from_quaternion(grasp_pose[3:])
        final_rotation = _matmul(alignment, grasp_rotation)
        final_quaternion = _quaternion_from_rotation(final_rotation)
        grasp_offset = _sub(grasp_pose[:3], geometry.object_center)
        aligned_offset = _matvec(alignment, grasp_offset)

        preinsert_center = _add(
            geometry.target_center,
            _scale(target_axis, geometry.object_length_m / 2.0 + geometry.preinsert_gap_m),
        )
        inserted_center = _sub(
            preinsert_center,
            _scale(target_axis, geometry.preinsert_gap_m + geometry.insertion_depth_m),
        )
        preinsert_grasp = _add(preinsert_center, aligned_offset)
        inserted_grasp = _add(inserted_center, aligned_offset)

        # The planning service defines a grasped capsule by T_tcp_to_object and
        # uses the object's local +z as the capsule axis. Recover that rigid
        # transform from this candidate's TCP pose and the observed tube. A
        # capsule is rotationally symmetric, so roll about its own axis is free.
        rotation_tcp_base = _transpose(grasp_rotation)
        center_tcp = _matvec(
            rotation_tcp_base,
            _sub(geometry.object_center, grasp_pose[:3]),
        )
        axis_tcp = _unit(
            "object_axis_tcp",
            _matvec(rotation_tcp_base, geometry.object_axis),
        )
        polar = math.acos(max(-1.0, min(1.0, axis_tcp[2])))
        azimuth = math.atan2(axis_tcp[1], axis_tcp[0])
        return ContinuationRequest(
            constraint_stage_index=int(future_stage["index"]),
            grasp_pose=tuple(grasp_pose),
            preinsert_pose=(*preinsert_grasp, *final_quaternion),
            inserted_pose=(*inserted_grasp, *final_quaternion),
            grasp_item={
                "item_type": "capsule",
                "length": geometry.object_length_m,
                "radius": geometry.object_radius_m,
                "offset_xyz": list(center_tcp),
                "euler_xyz": [0.0, polar, azimuth],
            },
        )

    def __call__(
        self,
        candidate: CandidateBundle,
        observation: ObservationPacket,
        current_stage: Mapping,
        future_stage: Mapping,
        constraint_ref: str,
        constraint: Mapping,
    ) -> CheckCertificate:
        name = constraint.get("name")
        if name not in self._SUPPORTED:
            return CheckCertificate(
                check=constraint_ref,
                status=CheckStatus.UNKNOWN,
                reason=f"unsupported_downstream_constraint:{name}",
            )
        try:
            geometry = self._geometry_provider(future_stage, observation, candidate)
            manipulated = (future_stage.get("stage_objects") or {}).get("manipulated")
            if manipulated != geometry.object_id:
                raise GeometryUnavailable(
                    "geometry object does not match future manipulated object"
                )
            grasp_pose = self._grasp_pose(candidate, current_stage)
            request = self._request(grasp_pose, geometry, future_stage)
        except (GeometryUnavailable, KeyError, TypeError, ValueError) as error:
            return CheckCertificate(
                check=constraint_ref,
                status=CheckStatus.UNKNOWN,
                reason=f"geometry_unavailable:{error}",
            )

        target_axis = geometry.target_axis
        evidence = tuple(dict.fromkeys((
            *candidate.evidence_refs,
            *geometry.evidence_refs,
            observation.calibration_ref,
        )))
        geometric_reason = ""
        if name == "axis_vertical":
            angle = math.degrees(math.acos(max(-1.0, min(1.0, abs(target_axis[2])))))
            if angle > self._axis_tolerance_deg:
                return CheckCertificate(
                    check=constraint_ref,
                    status=CheckStatus.FAIL,
                    reason=f"target_axis_not_vertical:angle_deg={angle:.4f}",
                    evidence_refs=evidence,
                )
            geometric_reason = f"axis_vertical:angle_deg={angle:.4f}"
        elif name == "axis_parallel":
            geometric_reason = "rigid_alignment_target_angle_deg=0.0000"
        elif name == "inside":
            radial_margin = (
                geometry.opening_radius_m
                - geometry.object_radius_m
                - geometry.clearance_margin_m
            )
            if radial_margin < 0.0:
                return CheckCertificate(
                    check=constraint_ref,
                    status=CheckStatus.FAIL,
                    reason=f"opening_too_narrow:radial_margin_m={radial_margin:.6f}",
                    evidence_refs=evidence,
                )
            geometric_reason = f"inside_radial_margin_m={radial_margin:.6f}"
        elif name == "center_align":
            geometric_reason = "constructed_radial_center_residual_m=0.000000"
        elif name == "above":
            geometric_reason = f"preinsert_gap_m={geometry.preinsert_gap_m:.6f}"
        elif name == "approach_direction":
            cone = (constraint.get("args") or {}).get("cone")
            if cone != "top_down":
                return CheckCertificate(
                    check=constraint_ref,
                    status=CheckStatus.UNKNOWN,
                    reason=f"unsupported_insertion_approach_cone:{cone}",
                )
            descent = _scale(target_axis, -1.0)
            angle = math.degrees(math.acos(max(-1.0, min(1.0, -descent[2]))))
            if angle > self._axis_tolerance_deg:
                return CheckCertificate(
                    check=constraint_ref,
                    status=CheckStatus.FAIL,
                    reason=f"insertion_not_top_down:angle_deg={angle:.4f}",
                    evidence_refs=evidence,
                )
            geometric_reason = f"top_down_insertion_angle_deg={angle:.4f}"
        elif name == "clearance":
            args = constraint.get("args") or {}
            if args.get("obj_a") != "gripper":
                return CheckCertificate(
                    check=constraint_ref,
                    status=CheckStatus.UNKNOWN,
                    reason="clearance_only_implemented_for_gripper",
                )
            axial_distance = _dot(
                _sub(request.inserted_pose[:3], geometry.target_center),
                target_axis,
            )
            required = (
                geometry.gripper_axial_extent_m + geometry.clearance_margin_m
            )
            clearance = axial_distance - required
            if clearance < 0.0:
                return CheckCertificate(
                    check=constraint_ref,
                    status=CheckStatus.FAIL,
                    reason=f"gripper_intersects_opening_plane:clearance_m={clearance:.6f}",
                    evidence_refs=evidence,
                )
            geometric_reason = f"gripper_plane_clearance_m={clearance:.6f}"

        key = (
            observation.observation_id,
            candidate.candidate_id,
            request.constraint_stage_index,
        )
        result = self._plan_cache.get(key)
        if result is None:
            try:
                result = self._planner(request, candidate, observation)
            except Exception as error:
                result = ContinuationResult(
                    status=CheckStatus.UNKNOWN,
                    reason=f"planner_error:{type(error).__name__}:{error}",
                )
            self._plan_cache[key] = result
        combined_evidence = tuple(dict.fromkeys((*evidence, *result.evidence_refs)))
        return CheckCertificate(
            check=constraint_ref,
            status=result.status,
            reason=(
                f"{geometric_reason};continuation={result.reason};"
                f"planning_calls={result.planning_calls}"
            ),
            evidence_refs=combined_evidence,
        )


class _PlannerRuntime:
    def __init__(self, pipe, arm_id: int) -> None:
        self.pipe = pipe
        self.arm_id = arm_id


class MotionPlanningGraspChecks:
    """One live grasp plan shared by reachability and collision hard checks."""

    def __init__(self, pipe, *, arm_id: int, artifact_dir: str | Path) -> None:
        if arm_id not in {0, 1}:
            raise ValueError("arm_id must be 0 or 1")
        self._runtime = _PlannerRuntime(pipe, arm_id)
        self._arm_id = arm_id
        self._artifact_dir = Path(artifact_dir)
        self._cache: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _candidate_pose(candidate: CandidateBundle) -> tuple[float, ...]:
        poses = []
        for envelope in candidate.hole_values.values():
            if not isinstance(envelope, Mapping):
                continue
            value = envelope.get("value")
            if isinstance(value, (list, tuple)) and len(value) == 7:
                poses.append(_finite_vector("candidate grasp pose", value, 7))
        if len(poses) != 1:
            raise GeometryUnavailable("candidate must bind exactly one pose_se3")
        return poses[0]

    def _plan(self, candidate: CandidateBundle, observation: ObservationPacket) -> dict:
        key = observation.observation_id, candidate.candidate_id
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        started = time.perf_counter()
        pose = None
        plan_record = None
        reachability = CheckStatus.UNKNOWN
        collision = CheckStatus.UNKNOWN
        reason = "planner_not_run"
        try:
            pose = self._candidate_pose(candidate)
            plan = robot_api.plan_joint_path(
                self._runtime,
                self._arm_id,
                pose,
                planning_mode="cartesian_goal",
                data=pose,
                scene_input="live",
                scene_camera="head",
            )
            plan_record = {
                "n_waypoints": plan.n_waypoints,
                "text_out": list(plan.text_out),
                "terminal_q": list(plan.waypoints[-1]),
                "waypoints": [list(item) for item in plan.waypoints],
            }
            reachability = collision = CheckStatus.PASS
            reason = "live_scene_grasp_trajectory_returned"
        except robot_api.PlanFailed as error:
            message = str(error)
            lowered = message.lower()
            if any(token in lowered for token in ("no ik", "ik failed", "unreachable")):
                reachability = CheckStatus.FAIL
                reason = f"unreachable:{message}"
            elif "collision" in lowered:
                collision = CheckStatus.FAIL
                reason = f"collision:{message}"
            else:
                reason = f"plan_failed:{error.reason}:{message}"
        except Exception as error:
            reason = f"planner_error:{type(error).__name__}:{error}"

        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self._artifact_dir / (
            f"{MotionPlanningContinuation._safe_name(observation.observation_id)}__"
            f"{MotionPlanningContinuation._safe_name(candidate.candidate_id)}__grasp.json"
        )
        path.write_text(json.dumps({
            "schema": "demo_graph_lab.grasp_plan_check.v1",
            "observation_id": observation.observation_id,
            "candidate_id": candidate.candidate_id,
            "target_pose_robot_base": list(pose) if pose is not None else None,
            "reachability": reachability.value,
            "collision_free": collision.value,
            "reason": reason,
            "elapsed_s": time.perf_counter() - started,
            "plan": plan_record,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = {
            "reachability": reachability,
            "collision_free": collision,
            "reason": reason,
            "evidence_ref": str(path),
        }
        self._cache[key] = result
        return result

    def _check(self, name: str) -> HardCheck:
        def evaluate(candidate: CandidateBundle, observation: ObservationPacket):
            result = self._plan(candidate, observation)
            return CheckCertificate(
                check=name,
                status=result[name],
                reason=result["reason"],
                evidence_refs=tuple(dict.fromkeys((
                    result["evidence_ref"], *candidate.evidence_refs,
                    observation.calibration_ref,
                ))),
            )

        return HardCheck(name=name, evaluate=evaluate)

    def checks(self) -> tuple[HardCheck, HardCheck]:
        return self._check("reachability"), self._check("collision_free")


class MotionPlanningContinuation:
    """Use the live motion planner for grasp -> pre-insert -> insert feasibility."""

    def __init__(self, pipe, *, arm_id: int, artifact_dir: str | Path) -> None:
        if arm_id not in {0, 1}:
            raise ValueError("arm_id must be 0 or 1")
        self._runtime = _PlannerRuntime(pipe, arm_id)
        self._arm_id = arm_id
        self._artifact_dir = Path(artifact_dir)

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)

    def __call__(
        self,
        request: ContinuationRequest,
        candidate: CandidateBundle,
        observation: ObservationPacket,
    ) -> ContinuationResult:
        started = time.perf_counter()
        calls = []
        status = CheckStatus.UNKNOWN
        reason = "planner_not_run"
        try:
            pipe = self._runtime.pipe
            q_current = pipe.call("info", "get_qpos", {"arm_id": self._arm_id})
            q_other = pipe.call("info", "get_qpos", {"arm_id": 1 - self._arm_id})
            for label, pose in (
                ("grasp", request.grasp_pose),
                ("preinsert", request.preinsert_pose),
                ("inserted", request.inserted_pose),
            ):
                scene_input = "live" if label == "grasp" else "cache"
                plan = robot_api.plan_joint_path(
                    self._runtime,
                    self._arm_id,
                    pose,
                    planning_mode="cartesian_goal",
                    q_current=q_current,
                    q_other_arm=q_other,
                    data=pose,
                    scene_input=scene_input,
                    scene_camera=("head" if scene_input == "live" else None),
                    grasp_item=(
                        None if label == "grasp" else request.grasp_item
                    ),
                )
                q_current = plan.waypoints[-1]
                calls.append({
                    "segment": label,
                    "target_pose_robot_base": list(pose),
                    "n_waypoints": plan.n_waypoints,
                    "text_out": list(plan.text_out),
                    "terminal_q": list(q_current),
                    "waypoints": [list(item) for item in plan.waypoints],
                })
            status = CheckStatus.PASS
            reason = "three_segment_trajectory_returned"
        except robot_api.PlanFailed as error:
            message = str(error)
            known_infeasible = any(
                token in message.lower()
                for token in ("collision", "unreachable", "infeasible", "no ik", "ik failed")
            )
            status = CheckStatus.FAIL if known_infeasible else CheckStatus.UNKNOWN
            reason = f"plan_failed:{error.reason}:{message}"
        except Exception as error:
            status = CheckStatus.UNKNOWN
            reason = f"planner_error:{type(error).__name__}:{error}"

        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self._artifact_dir / (
            f"{self._safe_name(observation.observation_id)}__"
            f"{self._safe_name(candidate.candidate_id)}__"
            f"s{request.constraint_stage_index}.json"
        )
        record = {
            "schema": "demo_graph_lab.continuation_plan.v1",
            "observation_id": observation.observation_id,
            "candidate_id": candidate.candidate_id,
            "future_stage_index": request.constraint_stage_index,
            "status": status.value,
            "reason": reason,
            "planning_calls": len(calls),
            "grasp_item": dict(request.grasp_item),
            "elapsed_s": time.perf_counter() - started,
            "segments": calls,
        }
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return ContinuationResult(
            status=status,
            reason=reason,
            evidence_refs=(str(path),),
            planning_calls=len(calls),
        )
