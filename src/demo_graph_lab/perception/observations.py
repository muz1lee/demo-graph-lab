"""Small data contract for non-privileged online observations.

The packet contains sensor references, calibrated perception results, and robot
state supplied by an external perception layer.  It deliberately has no
simulator client or task-success probe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


def _vector(name: str, value, length: int) -> None:
    if value is None:
        return
    if not isinstance(value, tuple) or len(value) != length:
        raise ValueError(f"{name} must be a tuple containing {length} values")
    if any(isinstance(item, bool) or not isinstance(item, (int, float))
           or not math.isfinite(item) for item in value):
        raise ValueError(f"{name} must contain only finite numbers")


def _unit(name: str, value) -> None:
    norm = math.sqrt(sum(float(item) ** 2 for item in value))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
        raise ValueError(f"{name} must be unit length")


@dataclass(frozen=True)
class Proprioception:
    """Explicit robot-owned measurements allowed on the method path.

    Keeping this contract closed prevents a provider from attaching arbitrary
    simulator state, exact object poses, or task-success probes under a generic
    ``robot_state`` mapping.
    """

    joint_positions: tuple[float, ...]
    end_effector_frame: str
    gripper_positions: tuple[float, ...] = ()
    end_effector_poses: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.joint_positions, tuple) or not self.joint_positions:
            raise ValueError("joint_positions must be a non-empty tuple")
        if not isinstance(self.gripper_positions, tuple):
            raise ValueError("gripper_positions must be a tuple")
        if (not isinstance(self.end_effector_frame, str)
                or not self.end_effector_frame.strip()):
            raise ValueError("end_effector_frame must be a non-empty string")
        if not isinstance(self.end_effector_poses, Mapping):
            raise TypeError("end_effector_poses must be a mapping")
        if any(isinstance(item, bool) or not isinstance(item, (int, float))
               or not math.isfinite(item) for item in self.joint_positions):
            raise ValueError("joint_positions must contain only finite numbers")
        if any(isinstance(item, bool) or not isinstance(item, (int, float))
               or not math.isfinite(item) for item in self.gripper_positions):
            raise ValueError("gripper_positions must contain only finite numbers")
        if (not isinstance(self.evidence_ref, str)
                or not self.evidence_ref.strip()):
            raise ValueError("proprioception must include a non-empty evidence_ref")
        for arm, pose in self.end_effector_poses.items():
            if not isinstance(arm, str) or not arm:
                raise ValueError("end_effector_poses keys must be non-empty arm names")
            _vector(f"end_effector_poses[{arm!r}]", pose, 7)
            _unit(f"end_effector_poses[{arm!r}].quaternion_xyzw", pose[3:7])
        object.__setattr__(self, "end_effector_poses", MappingProxyType(
            dict(self.end_effector_poses)))

    def to_record(self) -> dict:
        return {
            "evidence_ref": self.evidence_ref,
            "joint_count": len(self.joint_positions),
            "gripper_count": len(self.gripper_positions),
            "end_effector_frame": self.end_effector_frame,
            "end_effectors": sorted(self.end_effector_poses),
        }


@dataclass(frozen=True)
class ObjectObservation:
    """One perceived object expressed in an explicitly named frame.

    ``extent`` is a frame-space axis-aligned bounding box, not a local object
    extent.  Pose, axis, and extent therefore share exactly the same ``frame``.
    """

    object_id: str
    frame: str
    pose: tuple[float, ...] | None = None
    axis: tuple[float, ...] | None = None
    extent: Mapping[str, tuple[float, ...]] | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id.strip():
            raise ValueError("object_id must be a non-empty string")
        if not isinstance(self.frame, str) or not self.frame.strip():
            raise ValueError("object frame must be a non-empty string")
        _vector("pose", self.pose, 7)
        _vector("axis", self.axis, 3)
        if self.pose is not None:
            _unit("pose.quaternion_xyzw", self.pose[3:7])
        if self.axis is not None:
            _unit("axis", self.axis)
        if self.extent is not None:
            if not isinstance(self.extent, Mapping):
                raise TypeError("extent must be a mapping")
            if set(self.extent) != {"min", "max"}:
                raise ValueError("extent must contain exactly min and max")
            _vector("extent.min", self.extent.get("min"), 3)
            _vector("extent.max", self.extent.get("max"), 3)
            if any(
                minimum > maximum
                for minimum, maximum in zip(
                    self.extent["min"], self.extent["max"]
                )
            ):
                raise ValueError("extent min values must not exceed max values")
            object.__setattr__(self, "extent", MappingProxyType(dict(self.extent)))
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("object observation must include an evidence_refs tuple")
        if (any(not isinstance(ref, str) or not ref.strip()
                for ref in self.evidence_refs)
                or len(self.evidence_refs) != len(set(self.evidence_refs))):
            raise ValueError(
                "object evidence_refs must be unique non-empty artifact references"
            )

    def to_record(self) -> dict:
        return {
            "object_id": self.object_id,
            "frame": self.frame,
            "evidence_refs": list(self.evidence_refs),
            "has_pose": self.pose is not None,
            "has_axis": self.axis is not None,
            "has_extent": self.extent is not None,
        }


@dataclass(frozen=True)
class ObservationPacket:
    """Inputs allowed on the first online method path.

    Large RGB-D arrays stay outside the packet and are referenced by artifact
    IDs.  Geometry is usable only together with ``frame`` and
    ``calibration_ref`` so a caller cannot silently mix coordinate systems.
    """

    observation_id: str
    captured_at_s: float
    frame: str
    calibration_ref: str
    sensor_refs: tuple[str, ...]
    robot_state: Proprioception
    objects: tuple[ObjectObservation, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.observation_id, str)
                or not self.observation_id.strip()):
            raise ValueError("observation_id must be a non-empty string")
        if (isinstance(self.captured_at_s, bool)
                or not isinstance(self.captured_at_s, (int, float))
                or not math.isfinite(self.captured_at_s)
                or self.captured_at_s < 0):
            raise ValueError("captured_at_s must be a finite non-negative number")
        if not isinstance(self.frame, str) or not self.frame.strip():
            raise ValueError("observation frame must be a non-empty string")
        if (not isinstance(self.calibration_ref, str)
                or not self.calibration_ref.strip()):
            raise ValueError("calibration_ref must be a non-empty string")
        if not isinstance(self.sensor_refs, tuple) or not self.sensor_refs:
            raise ValueError("sensor_refs must be a non-empty tuple")
        if (any(not isinstance(ref, str) or not ref.strip()
                for ref in self.sensor_refs)
                or len(self.sensor_refs) != len(set(self.sensor_refs))):
            raise ValueError("sensor_refs must be unique non-empty artifact references")
        if not isinstance(self.robot_state, Proprioception):
            raise TypeError("robot_state must be Proprioception")
        if not isinstance(self.objects, tuple):
            raise TypeError("objects must be a tuple")
        if any(not isinstance(obj, ObjectObservation) for obj in self.objects):
            raise TypeError("objects must contain ObjectObservation values")
        object_ids = [obj.object_id for obj in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("object observations must have unique object_id values")

    def to_record(self) -> dict:
        """Return provenance needed to audit a decision, without duplicating arrays."""
        return {
            "observation_id": self.observation_id,
            "captured_at_s": self.captured_at_s,
            "frame": self.frame,
            "calibration_ref": self.calibration_ref,
            "sensor_refs": list(self.sensor_refs),
            "robot_state": self.robot_state.to_record(),
            "objects": [item.to_record() for item in self.objects],
        }
