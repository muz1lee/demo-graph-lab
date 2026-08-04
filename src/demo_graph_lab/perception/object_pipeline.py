"""Trusted local object-mask and RGB-D geometry stages.

This module contains no model transport and no controller access.  Grounding and
segmentation outputs enter only as referenced evidence: the caller records which
graph anchor was requested, without claiming that the model proved its identity.
Masks are checked against their frozen record, and every 3-D value is recomputed
from RGB-D locally.  NumPy stays an optional, function-local dependency so
importing the normal offline package remains cheap.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any

from ..graph import vocab
from .operators import OperatorError, fit_plane, intersect_ray_plane


MASK_SCHEMA = "demo_graph_lab.object_mask.v1"
OBJECT_ASSIGNMENT_SCHEMA = "demo_graph_lab.object_assignment.v1"
OBJECT_POINT_CLOUD_SCHEMA = "demo_graph_lab.object_point_cloud.v1"
OPENING_GEOMETRY_SCHEMA = "demo_graph_lab.opening_geometry.v2"

_MASK_KEYS = {
    "schema",
    "observation_id",
    "image_ref",
    "grounding_ref",
    "proposal_id",
    "mask_ref",
    "shape",
    "encoding",
    "foreground_pixels",
}
_ASSIGNMENT_KEYS = {
    "schema",
    "observation_id",
    "identity_status",
    "graph_object",
    "grounding_ref",
    "mask_ref",
    "cloud_ref",
    "cloud_manifest_ref",
    "frame",
    "unit",
    "calibration_ref",
}
_GRAPH_OBJECT_KEYS = {"object_id", "part", "instance", "selection"}
_INTRINSIC_KEYS = {"width", "height", "fx", "fy", "cx", "cy", "baseline"}
_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _numpy():
    try:
        import numpy as np
    except ImportError as error:  # pragma: no cover - exercised in minimal installs
        raise RuntimeError(
            "object RGB-D processing requires the optional 'live' dependency"
        ) from error
    return np


def _exact_object(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be an object")
    actual = set(value)
    missing = sorted(keys - actual)
    extra = sorted(actual - keys)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        raise ValueError(f"{path} has invalid fields: {', '.join(details)}")
    return value


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _identifier(value: Any, path: str) -> str:
    value = _required_string(value, path)
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"{path} must be snake_case")
    return value


def _positive_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


def _finite_number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"{path} must be {qualifier}")
    return number


def _graph_object(value: Any, path: str = "graph_object") -> dict[str, Any]:
    record = _exact_object(value, _GRAPH_OBJECT_KEYS, path)
    object_id = _identifier(record["object_id"], f"{path}.object_id")
    part = _identifier(record["part"], f"{path}.part")
    instance = record["instance"]
    if instance is not None:
        instance = _identifier(instance, f"{path}.instance")
    selection = record["selection"]
    if selection is not None:
        selection = _required_string(selection, f"{path}.selection")
    anchor_errors = vocab.anchor_rule_errors(
        part,
        has_instance=instance is not None,
        has_selection=selection is not None,
    )
    if anchor_errors:
        raise ValueError(f"{path}.{anchor_errors[0]}")
    return {
        "object_id": object_id,
        "part": part,
        "instance": instance,
        "selection": selection,
    }


def _mask_metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    root = _exact_object(record, _MASK_KEYS, "mask_record")
    if root["schema"] != MASK_SCHEMA:
        raise ValueError(f"unsupported mask schema: {root['schema']!r}")
    _required_string(root["observation_id"], "mask_record.observation_id")
    for field in ("image_ref", "grounding_ref", "proposal_id", "mask_ref"):
        _required_string(root[field], f"mask_record.{field}")
    shape = root["shape"]
    if not isinstance(shape, list) or len(shape) != 2:
        raise ValueError("mask_record.shape must be [height, width]")
    _positive_integer(shape[0], "mask_record.shape[0]")
    _positive_integer(shape[1], "mask_record.shape[1]")
    if root["encoding"] != "bool":
        raise ValueError("mask_record.encoding must be 'bool'")
    _positive_integer(root["foreground_pixels"], "mask_record.foreground_pixels")
    return root


def validate_mask_record(
    record: Mapping[str, Any],
    mask,
    *,
    expected_observation_id: str | None = None,
) -> dict[str, Any]:
    """Validate an exact mask record and its HxW boolean payload.

    A mask is only segmentation evidence.  The schema intentionally contains no
    graph object id and no 3-D pose, so a model response cannot assign identity or
    inject geometry through this boundary.
    """

    np = _numpy()
    root = _mask_metadata(record)
    if expected_observation_id is not None:
        expected = _required_string(
            expected_observation_id, "expected_observation_id"
        )
        if root["observation_id"] != expected:
            raise ValueError("mask_record belongs to another observation")
    if not isinstance(mask, np.ndarray) or mask.ndim != 2:
        raise TypeError("mask payload must be a two-dimensional NumPy array")
    if mask.dtype != np.dtype(np.bool_):
        raise TypeError("mask payload must use boolean dtype")
    expected_shape = tuple(root["shape"])
    if mask.shape != expected_shape:
        raise ValueError(
            f"mask payload shape {mask.shape} does not match record {expected_shape}"
        )
    foreground = int(np.count_nonzero(mask))
    if foreground != root["foreground_pixels"]:
        raise ValueError("mask foreground count does not match mask_record")
    if foreground == mask.size:
        raise ValueError("full-frame mask is not accepted as object evidence")
    return {
        **dict(root),
        "shape": list(root["shape"]),
    }


def validate_object_assignment_record(
    record: Mapping[str, Any],
    *,
    expected_observation_id: str | None = None,
    expected_graph_object: Mapping[str, Any] | None = None,
    mask_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one model-proposed graph-anchor binding and its lineage."""

    root = _exact_object(record, _ASSIGNMENT_KEYS, "object_assignment")
    if root["schema"] != OBJECT_ASSIGNMENT_SCHEMA:
        raise ValueError(
            f"unsupported object assignment schema: {root['schema']!r}"
        )
    observation_id = _required_string(
        root["observation_id"], "object_assignment.observation_id"
    )
    if root["identity_status"] != "MODEL_PROPOSED":
        raise ValueError("object_assignment.identity_status must be 'MODEL_PROPOSED'")
    graph_object = _graph_object(
        root["graph_object"], "object_assignment.graph_object"
    )
    for field in (
        "grounding_ref",
        "mask_ref",
        "cloud_ref",
        "cloud_manifest_ref",
        "frame",
        "calibration_ref",
    ):
        _required_string(root[field], f"object_assignment.{field}")
    if root["unit"] != "meter":
        raise ValueError("object_assignment.unit must be 'meter'")

    if expected_observation_id is not None:
        expected = _required_string(
            expected_observation_id, "expected_observation_id"
        )
        if observation_id != expected:
            raise ValueError("object_assignment belongs to another observation")
    if expected_graph_object is not None:
        expected = _graph_object(expected_graph_object, "expected_graph_object")
        if graph_object != expected:
            raise ValueError("object_assignment does not match the graph anchor")
    if mask_record is not None:
        mask_meta = _mask_metadata(mask_record)
        if observation_id != mask_meta["observation_id"]:
            raise ValueError("object_assignment and mask observation do not match")
        if root["grounding_ref"] != mask_meta["grounding_ref"]:
            raise ValueError("object_assignment grounding_ref does not match mask")
        if root["mask_ref"] != mask_meta["mask_ref"]:
            raise ValueError("object_assignment mask_ref does not match mask")

    return {
        **dict(root),
        "graph_object": graph_object,
    }


def make_object_assignment_record(
    *,
    observation_id: str,
    object_id: str,
    part: str,
    instance: str | None,
    selection: str | None = None,
    grounding_ref: str,
    mask_ref: str,
    cloud_ref: str,
    cloud_manifest_ref: str,
    frame: str,
    calibration_ref: str,
) -> dict[str, Any]:
    """Construct, then revalidate, one closed object-assignment record."""

    record = {
        "schema": OBJECT_ASSIGNMENT_SCHEMA,
        "observation_id": observation_id,
        "identity_status": "MODEL_PROPOSED",
        "graph_object": {
            "object_id": object_id,
            "part": part,
            "instance": instance,
            "selection": selection,
        },
        "grounding_ref": grounding_ref,
        "mask_ref": mask_ref,
        "cloud_ref": cloud_ref,
        "cloud_manifest_ref": cloud_manifest_ref,
        "frame": frame,
        "unit": "meter",
        "calibration_ref": calibration_ref,
    }
    return validate_object_assignment_record(record)


def _intrinsics(value: Mapping[str, Any]) -> dict[str, int | float]:
    record = _exact_object(value, _INTRINSIC_KEYS, "intrinsics")
    width = _positive_integer(record["width"], "intrinsics.width")
    height = _positive_integer(record["height"], "intrinsics.height")
    fx = _finite_number(record["fx"], "intrinsics.fx", positive=True)
    fy = _finite_number(record["fy"], "intrinsics.fy", positive=True)
    cx = _finite_number(record["cx"], "intrinsics.cx")
    cy = _finite_number(record["cy"], "intrinsics.cy")
    baseline = _finite_number(
        record["baseline"], "intrinsics.baseline", positive=True
    )
    if not 0.0 <= cx < width or not 0.0 <= cy < height:
        raise ValueError("intrinsics principal point must lie inside the image")
    return {
        "width": width,
        "height": height,
        "fx": fx,
        "fy": fy,
        "cx": cx,
        "cy": cy,
        "baseline": baseline,
    }


def _rgbd_arrays(depth_m, mask, intrinsics: Mapping[str, Any]):
    np = _numpy()
    values = _intrinsics(intrinsics)
    shape = (values["height"], values["width"])
    if not isinstance(depth_m, np.ndarray) or depth_m.ndim != 2:
        raise TypeError("depth_m must be a two-dimensional NumPy array")
    if depth_m.dtype.kind != "f":
        raise TypeError("depth_m must use floating-point meters")
    if depth_m.shape != shape:
        raise ValueError("depth_m shape does not match intrinsics")
    if not isinstance(mask, np.ndarray) or mask.ndim != 2:
        raise TypeError("mask must be a two-dimensional NumPy array")
    if mask.dtype != np.dtype(np.bool_):
        raise TypeError("mask must use boolean dtype")
    if mask.shape != shape:
        raise ValueError("mask shape does not match depth and intrinsics")
    return np, values


def project_masked_depth(
    depth_m,
    mask,
    intrinsics: Mapping[str, Any],
    *,
    min_points: int = 1,
):
    """Project only masked, finite, positive-depth pixels into optical XYZ.

    Returns ``(points, pixels_rc)`` in identical row order.  Applying the mask to
    the depth image before ``nonzero`` is intentional: no pixel association is
    reconstructed after flattening a full-scene point cloud.
    """

    np, values = _rgbd_arrays(depth_m, mask, intrinsics)
    minimum = _positive_integer(min_points, "min_points")
    z_image = depth_m.astype(np.float32, copy=False)
    valid = mask & np.isfinite(z_image) & (z_image > 0.0)
    rows, columns = np.nonzero(valid)
    if len(rows) < minimum:
        raise ValueError(
            f"masked depth has {len(rows)} valid points; requires at least {minimum}"
        )
    z = z_image[rows, columns]
    x = (
        (columns.astype(np.float32) - np.float32(values["cx"]))
        * z
        / np.float32(values["fx"])
    )
    y = (
        (rows.astype(np.float32) - np.float32(values["cy"]))
        * z
        / np.float32(values["fy"])
    )
    points = np.column_stack((x, y, z)).astype(np.float32, copy=False)
    pixels_rc = np.column_stack((rows, columns)).astype(np.int32, copy=False)
    points.setflags(write=False)
    pixels_rc.setflags(write=False)
    return points, pixels_rc


@dataclass(frozen=True)
class ObjectPointCloud:
    """Object-only optical points and their exact source pixels."""

    points: Any
    pixels_rc: Any
    manifest: Mapping[str, Any]

    def __post_init__(self) -> None:
        graph_object = MappingProxyType(dict(self.manifest["graph_object"]))
        manifest = {**dict(self.manifest), "graph_object": graph_object}
        object.__setattr__(self, "manifest", MappingProxyType(manifest))

    def manifest_record(self) -> dict[str, Any]:
        return {
            **dict(self.manifest),
            "graph_object": dict(self.manifest["graph_object"]),
        }


def build_object_point_cloud(
    depth_m,
    mask,
    intrinsics: Mapping[str, Any],
    *,
    mask_record: Mapping[str, Any],
    assignment_record: Mapping[str, Any],
    assignment_ref: str,
    depth_ref: str,
    pixel_lineage_ref: str,
    expected_graph_object: Mapping[str, Any],
    min_points: int = 1,
) -> ObjectPointCloud:
    """Build an object-only cloud and a provenance-complete manifest."""

    mask_meta = validate_mask_record(mask_record, mask)
    assignment = validate_object_assignment_record(
        assignment_record,
        expected_observation_id=mask_meta["observation_id"],
        expected_graph_object=expected_graph_object,
        mask_record=mask_meta,
    )
    assignment_ref = _required_string(assignment_ref, "assignment_ref")
    depth_ref = _required_string(depth_ref, "depth_ref")
    pixel_lineage_ref = _required_string(
        pixel_lineage_ref, "pixel_lineage_ref"
    )
    if pixel_lineage_ref in {
        assignment["cloud_ref"],
        assignment["mask_ref"],
        depth_ref,
    }:
        raise ValueError("pixel_lineage_ref must identify a separate artifact")

    points, pixels_rc = project_masked_depth(
        depth_m,
        mask,
        intrinsics,
        min_points=min_points,
    )
    manifest = {
        "schema": OBJECT_POINT_CLOUD_SCHEMA,
        "observation_id": assignment["observation_id"],
        "identity_status": assignment["identity_status"],
        "graph_object": dict(assignment["graph_object"]),
        "artifact_ref": assignment["cloud_ref"],
        "assignment_ref": assignment_ref,
        "source_image_ref": mask_meta["image_ref"],
        "source_depth_ref": depth_ref,
        "mask_ref": assignment["mask_ref"],
        "pixel_lineage_ref": pixel_lineage_ref,
        "pixel_layout": "row_col",
        "point_layout": "x_y_z",
        "point_count": int(len(points)),
        "masked_pixel_count": int(mask_meta["foreground_pixels"]),
        "frame": assignment["frame"],
        "unit": "meter",
        "calibration_ref": assignment["calibration_ref"],
    }
    return ObjectPointCloud(points, pixels_rc, manifest)


class GeometryStatus(str, Enum):
    PASS = "PASS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PlanarOpeningGeometry:
    """Conservative RGB-D estimate of one planar opening in the camera frame."""

    status: GeometryStatus
    reason: str
    observation_id: str
    frame: str
    calibration_ref: str
    evidence_refs: tuple[str, ...]
    center: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    metrics: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.status, GeometryStatus):
            raise TypeError("status must be GeometryStatus")
        _required_string(self.reason, "reason")
        _required_string(self.observation_id, "observation_id")
        _required_string(self.frame, "frame")
        _required_string(self.calibration_ref, "calibration_ref")
        if (
            not isinstance(self.evidence_refs, tuple)
            or not self.evidence_refs
            or len(self.evidence_refs) != len(set(self.evidence_refs))
            or any(not isinstance(ref, str) or not ref.strip()
                   for ref in self.evidence_refs)
        ):
            raise ValueError("evidence_refs must be unique non-empty strings")
        if self.status is GeometryStatus.PASS:
            if self.center is None or self.axis is None:
                raise ValueError("PASS geometry requires center and axis")
        elif self.center is not None or self.axis is not None:
            raise ValueError("UNKNOWN geometry must not contain center or axis")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": OPENING_GEOMETRY_SCHEMA,
            "status": self.status.value,
            "reason": self.reason,
            "observation_id": self.observation_id,
            "frame": self.frame,
            "unit": "meter",
            "calibration_ref": self.calibration_ref,
            "evidence_refs": list(self.evidence_refs),
            "center": None if self.center is None else list(self.center),
            "axis": None if self.axis is None else list(self.axis),
            "metrics": dict(self.metrics),
        }


def _single_component(mask) -> bool:
    np = _numpy()
    coordinates = np.argwhere(mask)
    if len(coordinates) == 0:
        return False
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=np.bool_)
    start = tuple(int(item) for item in coordinates[0])
    stack = [start]
    visited[start] = True
    count = 0
    while stack:
        row, column = stack.pop()
        count += 1
        for next_row, next_column in (
            (row - 1, column),
            (row + 1, column),
            (row, column - 1),
            (row, column + 1),
        ):
            if (
                0 <= next_row < height
                and 0 <= next_column < width
                and mask[next_row, next_column]
                and not visited[next_row, next_column]
            ):
                visited[next_row, next_column] = True
                stack.append((next_row, next_column))
    return count == len(coordinates)


def _dilate(mask, iterations: int):
    expanded = mask.copy()
    for _ in range(iterations):
        source = expanded
        target = source.copy()
        target[1:, :] |= source[:-1, :]
        target[:-1, :] |= source[1:, :]
        target[:, 1:] |= source[:, :-1]
        target[:, :-1] |= source[:, 1:]
        target[1:, 1:] |= source[:-1, :-1]
        target[1:, :-1] |= source[:-1, 1:]
        target[:-1, 1:] |= source[1:, :-1]
        target[:-1, :-1] |= source[1:, 1:]
        expanded = target
    return expanded


# Geometry operators fail with their own generic codes; this function publishes
# its own reason vocabulary, so every operator code is translated explicitly and
# an unmapped one raises instead of leaking into the artifact.
_OPERATOR_REASONS = {
    "plane_fit_failed": "plane_fit_failed",
    "plane_points_are_degenerate": "ring_geometry_is_degenerate",
    "ray_parallel_to_plane": "ray_parallel_to_support_plane",
    "plane_intersection_behind_camera": "plane_intersection_behind_camera",
}


def estimate_planar_opening_geometry(
    rgb,
    depth_m,
    roi_mask,
    intrinsics: Mapping[str, Any],
    *,
    observation_id: str,
    frame: str,
    calibration_ref: str,
    rgb_ref: str,
    depth_ref: str,
    roi_record: Mapping[str, Any],
    min_hole_pixels: int = 9,
    min_ring_points: int = 12,
    ring_width_px: int = 2,
    min_depth_contrast_m: float = 0.005,
    min_rgb_contrast: float = 10.0,
    max_plane_rmse_m: float = 0.003,
) -> PlanarOpeningGeometry:
    """Estimate an opening center/axis from an ROI plus local RGB-D evidence.

    The ROI supplies only a 2-D opening hypothesis.  The center is recomputed as
    the ROI-centroid ray intersected with a plane fitted to the surrounding
    support surface; the axis is that plane normal.  The estimate is ``UNKNOWN``
    unless the ROI is a single interior component, the surrounding depth is
    planar, and both RGB and depth contrast support an actual opening.  Nothing
    here assumes the opening is round or belongs to a particular fixture, and no
    model pose is an input to this function.
    """

    np, values = _rgbd_arrays(depth_m, roi_mask, intrinsics)
    observation_id = _required_string(observation_id, "observation_id")
    frame = _required_string(frame, "frame")
    calibration_ref = _required_string(calibration_ref, "calibration_ref")
    rgb_ref = _required_string(rgb_ref, "rgb_ref")
    depth_ref = _required_string(depth_ref, "depth_ref")
    roi_meta = validate_mask_record(
        roi_record,
        roi_mask,
        expected_observation_id=observation_id,
    )
    if roi_meta["image_ref"] != rgb_ref:
        raise ValueError("opening ROI does not belong to the supplied RGB image")
    evidence_refs = (
        rgb_ref,
        depth_ref,
        roi_meta["grounding_ref"],
        roi_meta["mask_ref"],
    )
    if len(evidence_refs) != len(set(evidence_refs)):
        raise ValueError("RGB-D geometry evidence refs must be distinct")
    min_hole_pixels = _positive_integer(min_hole_pixels, "min_hole_pixels")
    min_ring_points = _positive_integer(min_ring_points, "min_ring_points")
    ring_width_px = _positive_integer(ring_width_px, "ring_width_px")
    min_depth_contrast_m = _finite_number(
        min_depth_contrast_m, "min_depth_contrast_m", positive=True
    )
    min_rgb_contrast = _finite_number(
        min_rgb_contrast, "min_rgb_contrast", positive=True
    )
    max_plane_rmse_m = _finite_number(
        max_plane_rmse_m, "max_plane_rmse_m", positive=True
    )
    if (
        not isinstance(rgb, np.ndarray)
        or rgb.dtype != np.dtype(np.uint8)
        or rgb.ndim != 3
        or rgb.shape != (*roi_mask.shape, 3)
    ):
        raise TypeError("rgb must be an HxWx3 uint8 array matching depth")

    hole_pixels = int(np.count_nonzero(roi_mask))
    metrics: dict[str, Any] = {"hole_pixels": hole_pixels}

    def unknown(reason: str) -> PlanarOpeningGeometry:
        return PlanarOpeningGeometry(
            GeometryStatus.UNKNOWN,
            reason,
            observation_id,
            frame,
            calibration_ref,
            evidence_refs,
            metrics=metrics,
        )

    if hole_pixels < min_hole_pixels:
        return unknown("insufficient_hole_pixels")
    rows, columns = np.nonzero(roi_mask)
    if (
        rows.min() == 0
        or columns.min() == 0
        or rows.max() == roi_mask.shape[0] - 1
        or columns.max() == roi_mask.shape[1] - 1
    ):
        return unknown("hole_roi_touches_image_border")
    if not _single_component(roi_mask):
        return unknown("hole_roi_has_multiple_components")

    ring_mask = _dilate(roi_mask, ring_width_px) & ~roi_mask
    valid_hole = roi_mask & np.isfinite(depth_m) & (depth_m > 0.0)
    valid_ring = ring_mask & np.isfinite(depth_m) & (depth_m > 0.0)
    hole_depth_count = int(np.count_nonzero(valid_hole))
    ring_depth_count = int(np.count_nonzero(valid_ring))
    metrics.update({
        "hole_depth_points": hole_depth_count,
        "ring_depth_points": ring_depth_count,
    })
    if hole_depth_count < min_hole_pixels or ring_depth_count < min_ring_points:
        return unknown("insufficient_rgbd_support")

    hole_depth = float(np.median(depth_m[valid_hole]))
    ring_depth = float(np.median(depth_m[valid_ring]))
    depth_contrast = hole_depth - ring_depth
    gray = rgb.astype(np.float32).mean(axis=2)
    rgb_contrast = abs(
        float(np.median(gray[roi_mask])) - float(np.median(gray[ring_mask]))
    )
    metrics.update({
        "hole_depth_median_m": hole_depth,
        "ring_depth_median_m": ring_depth,
        "depth_contrast_m": depth_contrast,
        "rgb_contrast": rgb_contrast,
    })
    # The gate only asks that the ROI stand off its surroundings in depth; a
    # recessed opening and a protruding one are both acceptable evidence.  The
    # signed value stays in metrics because the sign is what tells them apart.
    if abs(depth_contrast) < min_depth_contrast_m:
        return unknown("insufficient_depth_contrast")
    if rgb_contrast < min_rgb_contrast:
        return unknown("insufficient_rgb_contrast")

    ring_points, _ = project_masked_depth(
        depth_m,
        valid_ring,
        intrinsics,
        min_points=min_ring_points,
    )
    try:
        normal, plane_center, plane_rmse = fit_plane(ring_points)
    except OperatorError as error:
        return unknown(_OPERATOR_REASONS[error.reason])
    metrics["plane_rmse_m"] = plane_rmse
    if plane_rmse > max_plane_rmse_m:
        return unknown("support_surface_not_planar")

    center_row = float(rows.mean())
    center_column = float(columns.mean())
    try:
        center = intersect_ray_plane(
            [
                (center_column - values["cx"]) / values["fx"],
                (center_row - values["cy"]) / values["fy"],
                1.0,
            ],
            normal=normal,
            plane_point=plane_center,
        )
    except OperatorError as error:
        return unknown(_OPERATOR_REASONS[error.reason])
    if float(normal @ center) < 0.0:
        normal = -normal
    metrics.update({
        "roi_center_row": center_row,
        "roi_center_column": center_column,
    })
    return PlanarOpeningGeometry(
        status=GeometryStatus.PASS,
        reason="estimated_from_rgbd_roi_and_local_support_plane",
        observation_id=observation_id,
        frame=frame,
        calibration_ref=calibration_ref,
        evidence_refs=evidence_refs,
        center=tuple(float(item) for item in center),
        axis=tuple(float(item) for item in normal),
        metrics=metrics,
    )
