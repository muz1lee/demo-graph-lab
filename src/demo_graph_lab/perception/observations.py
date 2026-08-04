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


@dataclass(frozen=True)
class Proprioception:
    """Explicit robot-owned measurements allowed on the method path.

    Keeping this contract closed prevents a provider from attaching arbitrary
    simulator state, exact object poses, or task-success probes under a generic
    ``robot_state`` mapping.
    """

    joint_positions: tuple[float, ...]
    gripper_positions: tuple[float, ...] = ()
    end_effector_poses: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.joint_positions, tuple) or not self.joint_positions:
            raise ValueError("joint_positions must be a non-empty tuple")
        if not isinstance(self.gripper_positions, tuple):
            raise ValueError("gripper_positions must be a tuple")
        if any(isinstance(item, bool) or not isinstance(item, (int, float))
               or not math.isfinite(item) for item in self.joint_positions):
            raise ValueError("joint_positions must contain only finite numbers")
        if any(isinstance(item, bool) or not isinstance(item, (int, float))
               or not math.isfinite(item) for item in self.gripper_positions):
            raise ValueError("gripper_positions must contain only finite numbers")
        if not self.evidence_ref:
            raise ValueError("proprioception must include an evidence_ref")
        for arm, pose in self.end_effector_poses.items():
            if not isinstance(arm, str) or not arm:
                raise ValueError("end_effector_poses keys must be non-empty arm names")
            _vector(f"end_effector_poses[{arm!r}]", pose, 7)
        object.__setattr__(self, "end_effector_poses", MappingProxyType(
            dict(self.end_effector_poses)))

    def to_record(self) -> dict:
        return {
            "evidence_ref": self.evidence_ref,
            "joint_count": len(self.joint_positions),
            "gripper_count": len(self.gripper_positions),
            "end_effectors": sorted(self.end_effector_poses),
        }


@dataclass(frozen=True)
class ObjectObservation:
    """One perceived object expressed in an explicitly named frame."""

    object_id: str
    frame: str
    pose: tuple[float, ...] | None = None
    axis: tuple[float, ...] | None = None
    extent: Mapping[str, tuple[float, ...]] | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.object_id:
            raise ValueError("object_id must not be empty")
        if not self.frame:
            raise ValueError("object frame must not be empty")
        _vector("pose", self.pose, 7)
        _vector("axis", self.axis, 3)
        if self.extent is not None:
            if set(self.extent) != {"min", "max"}:
                raise ValueError("extent must contain exactly min and max")
            _vector("extent.min", self.extent.get("min"), 3)
            _vector("extent.max", self.extent.get("max"), 3)
            object.__setattr__(self, "extent", MappingProxyType(dict(self.extent)))
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("object observation must include an evidence_refs tuple")

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
        if not self.observation_id:
            raise ValueError("observation_id must not be empty")
        if not self.frame:
            raise ValueError("observation frame must not be empty")
        if not self.calibration_ref:
            raise ValueError("calibration_ref must not be empty")
        if not isinstance(self.sensor_refs, tuple) or not self.sensor_refs:
            raise ValueError("sensor_refs must be a non-empty tuple")
        if (any(not isinstance(ref, str) or not ref for ref in self.sensor_refs)
                or len(self.sensor_refs) != len(set(self.sensor_refs))):
            raise ValueError("sensor_refs must be unique non-empty artifact references")
        if not isinstance(self.robot_state, Proprioception):
            raise TypeError("robot_state must be Proprioception")
        if not isinstance(self.objects, tuple):
            raise TypeError("objects must be a tuple")
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
