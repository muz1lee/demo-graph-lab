from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import json

import numpy as np

from .client import call_grasp_service
from .contract import GraspNetConfig, build_request, write_json


POINTCLOUD_MANIFEST_SCHEMA = "kw_independent.graspnet.pointcloud_manifest.v1"
FRAME_PROBE_SCHEMA = "kw_independent.graspnet.real_frame_probe.v1"


@dataclass
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_camera_intrinsics(path_or_payload: str | Path | dict[str, Any]) -> CameraIntrinsics:
    """Load camera intrinsics without guessing missing calibration values."""

    payload = _load_json_or_payload(path_or_payload)
    if not isinstance(payload, dict):
        raise ValueError("camera intrinsics must be a JSON object")

    matrix = payload.get("K") or payload.get("camera_matrix") or payload.get("intrinsic_matrix")
    if matrix is not None:
        rows = np.asarray(matrix, dtype=float)
        if rows.shape == (9,):
            rows = rows.reshape(3, 3)
        if rows.shape != (3, 3):
            raise ValueError("intrinsic matrix must be 3x3 or length 9")
        fx, fy, cx, cy = float(rows[0, 0]), float(rows[1, 1]), float(rows[0, 2]), float(rows[1, 2])
    else:
        missing = [key for key in ["fx", "fy", "cx", "cy"] if key not in payload]
        if missing:
            raise ValueError(f"missing camera intrinsics: {', '.join(missing)}")
        fx, fy, cx, cy = (float(payload[key]) for key in ["fx", "fy", "cx", "cy"])

    if fx == 0 or fy == 0:
        raise ValueError("fx and fy must be non-zero")

    width = _optional_int(payload.get("width"))
    height = _optional_int(payload.get("height"))
    return CameraIntrinsics(fx=fx, fy=fy, cx=cx, cy=cy, width=width, height=height)


def rgbd_to_pointcloud(
    *,
    depth_path: str | Path,
    intrinsics: str | Path | dict[str, Any],
    output_path: str | Path,
    image_path: str | Path | None = None,
    mask_path: str | Path | None = None,
    depth_scale: float = 0.001,
    min_depth_m: float | None = None,
    max_depth_m: float | None = None,
    coordinate_frame: str | None = None,
    max_points: int | None = None,
    sample_seed: int = 0,
    manifest_path: str | Path | None = None,
    evidence_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project RGB-D evidence into a point cloud.

    Coordinates are left in the input camera frame. The function records the
    depth scale, intrinsics, mask, and source paths; it does not infer object
    identity, grasp quality, or robot/world transforms.
    """

    depth_file = Path(depth_path)
    output_file = Path(output_path)
    camera = load_camera_intrinsics(intrinsics)
    depth_raw = _load_array(depth_file)
    if depth_raw.ndim != 2:
        raise ValueError("depth input must be a 2D array or single-channel image")
    depth_m = np.asarray(depth_raw, dtype=np.float32) * float(depth_scale)
    height, width = depth_m.shape

    mask = _load_mask(mask_path) if mask_path else None
    if mask is not None and mask.shape != depth_m.shape:
        raise ValueError(f"mask shape {mask.shape} does not match depth shape {depth_m.shape}")

    colors = _load_rgb(image_path) if image_path else None
    if colors is not None and colors.shape[:2] != depth_m.shape:
        raise ValueError(f"image shape {colors.shape[:2]} does not match depth shape {depth_m.shape}")

    valid = np.isfinite(depth_m) & (depth_m > 0)
    num_valid_depth = int(valid.sum())
    if min_depth_m is not None:
        valid &= depth_m >= float(min_depth_m)
    if max_depth_m is not None:
        valid &= depth_m <= float(max_depth_m)
    if mask is not None:
        valid &= mask

    rows, cols = np.nonzero(valid)
    z = depth_m[rows, cols]
    x = (cols.astype(np.float32) - float(camera.cx)) * z / float(camera.fx)
    y = (rows.astype(np.float32) - float(camera.cy)) * z / float(camera.fy)
    points = np.stack([x, y, z], axis=1).astype(np.float32)
    pixel_xy = np.stack([cols, rows], axis=1).astype(np.int32)
    selected_indices = np.arange(points.shape[0], dtype=np.int64)

    sampling: dict[str, Any] = {
        "requested_max_points": max_points,
        "sample_seed": sample_seed,
        "sampled": False,
    }
    if max_points is not None and points.shape[0] > int(max_points):
        rng = np.random.default_rng(int(sample_seed))
        selected_indices = np.sort(rng.choice(points.shape[0], int(max_points), replace=False))
        points = points[selected_indices]
        pixel_xy = pixel_xy[selected_indices]
        sampling["sampled"] = True

    output_file.parent.mkdir(parents=True, exist_ok=True)
    saved_fields = ["points"]
    if output_file.suffix == ".npy":
        np.save(output_file, points)
        warnings = ["output is .npy, so pixel/color provenance is only recorded in the manifest"]
    else:
        payload: dict[str, Any] = {
            "points": points,
            "pixel_xy": pixel_xy,
            "depth_m": z[selected_indices] if max_points is not None and z.shape[0] > len(points) else z,
        }
        saved_fields.extend(["pixel_xy", "depth_m"])
        if colors is not None:
            payload["colors"] = colors[pixel_xy[:, 1], pixel_xy[:, 0]]
            saved_fields.append("colors")
        np.savez_compressed(output_file, **payload)
        warnings = []

    manifest = {
        "schema": POINTCLOUD_MANIFEST_SCHEMA,
        "source_policy": "rgbd_projection_evidence_no_route_decision",
        "output_point_cloud_path": str(output_file),
        "coordinate_frame": coordinate_frame or "unknown",
        "saved_fields": saved_fields,
        "input_reference": {
            "image_path": str(image_path) if image_path else None,
            "depth_path": str(depth_file),
            "mask_path": str(mask_path) if mask_path else None,
            "camera_intrinsics": camera.to_dict(),
            "depth_scale": float(depth_scale),
            "min_depth_m": min_depth_m,
            "max_depth_m": max_depth_m,
        },
        "stats": {
            "depth_shape_hw": [int(height), int(width)],
            "num_pixels": int(height * width),
            "num_valid_depth_pixels": num_valid_depth,
            "num_after_mask_and_filters": int(valid.sum()),
            "num_output_points": int(points.shape[0]),
            "mask_applied": mask is not None,
            "sampling": sampling,
        },
        "evidence_source": evidence_source or {},
        "warnings": warnings,
    }
    if manifest_path:
        manifest["manifest_path"] = str(write_json(manifest_path, manifest))
    return manifest


def mask_pointcloud(
    *,
    point_cloud_path: str | Path,
    mask_path: str | Path,
    output_path: str | Path,
    manifest_path: str | Path | None = None,
    evidence_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a 2D mask to a point cloud that preserves `pixel_xy` provenance."""

    cloud_path = Path(point_cloud_path)
    cloud = np.load(cloud_path)
    if not isinstance(cloud, np.lib.npyio.NpzFile):
        raise ValueError("mask_pointcloud requires an .npz point cloud with pixel_xy provenance")
    if "points" not in cloud or "pixel_xy" not in cloud:
        raise ValueError("point cloud .npz must contain points and pixel_xy")

    points = np.asarray(cloud["points"], dtype=np.float32)
    pixel_xy = np.asarray(cloud["pixel_xy"], dtype=np.int32)
    mask = _load_mask(mask_path)
    if pixel_xy.ndim != 2 or pixel_xy.shape[1] != 2:
        raise ValueError("pixel_xy must be an Nx2 array")

    cols = pixel_xy[:, 0]
    rows = pixel_xy[:, 1]
    inside = (rows >= 0) & (rows < mask.shape[0]) & (cols >= 0) & (cols < mask.shape[1])
    keep = np.zeros(points.shape[0], dtype=bool)
    keep[inside] = mask[rows[inside], cols[inside]]

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "points": points[keep],
        "pixel_xy": pixel_xy[keep],
    }
    for key in ["colors", "depth_m"]:
        if key in cloud:
            payload[key] = cloud[key][keep]
    np.savez_compressed(output_file, **payload)

    manifest = {
        "schema": POINTCLOUD_MANIFEST_SCHEMA,
        "source_policy": "mask_filter_evidence_no_route_decision",
        "input_point_cloud_path": str(cloud_path),
        "output_point_cloud_path": str(output_file),
        "input_reference": {"mask_path": str(mask_path)},
        "stats": {
            "num_input_points": int(points.shape[0]),
            "num_output_points": int(keep.sum()),
            "mask_shape_hw": [int(mask.shape[0]), int(mask.shape[1])],
        },
        "evidence_source": evidence_source or {},
        "warnings": [],
    }
    if manifest_path:
        manifest["manifest_path"] = str(write_json(manifest_path, manifest))
    return manifest


def run_real_frame_probe(
    *,
    depth_path: str | Path,
    intrinsics: str | Path | dict[str, Any],
    service_config: GraspNetConfig | dict[str, Any],
    output_dir: str | Path,
    image_path: str | Path | None = None,
    mask_path: str | Path | None = None,
    depth_scale: float = 0.001,
    min_depth_m: float | None = None,
    max_depth_m: float | None = None,
    coordinate_frame: str | None = None,
    max_points: int | None = 20000,
    sample_seed: int = 0,
    object_hint: str | None = None,
    frame_id: str | None = None,
    max_grasps: int | None = None,
) -> dict[str, Any]:
    """Convert one RGB-D observation to point cloud and query grasp proposals."""

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    point_cloud_path = root / "point_cloud.npz"
    manifest_path = root / "point_cloud_manifest.json"
    point_manifest = rgbd_to_pointcloud(
        depth_path=depth_path,
        intrinsics=intrinsics,
        output_path=point_cloud_path,
        image_path=image_path,
        mask_path=mask_path,
        depth_scale=depth_scale,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
        coordinate_frame=coordinate_frame,
        max_points=max_points,
        sample_seed=sample_seed,
        manifest_path=manifest_path,
        evidence_source={"created_by": "independent_tools.graspnet.real_frame_probe"},
    )
    request_payload = build_request(
        image_path=str(image_path) if image_path else None,
        depth_path=str(depth_path),
        point_cloud_path=str(point_cloud_path),
        mask_path=str(mask_path) if mask_path else None,
        object_hint=object_hint,
        camera_intrinsics=point_manifest["input_reference"]["camera_intrinsics"],
        frame_id=frame_id,
        coordinate_frame=coordinate_frame,
        evidence_source={"created_by": "independent_tools.graspnet.real_frame_probe"},
        extra={"max_grasps": max_grasps} if max_grasps is not None else None,
    )
    request_path = write_json(root / "request.json", request_payload)
    call_result = call_grasp_service(
        request_payload=request_payload,
        config=service_config,
        output_dir=root,
    )
    result = {
        "schema": FRAME_PROBE_SCHEMA,
        "source_policy": "real_frame_grasp_probe_no_route_decision",
        "ok": bool(call_result.get("ok")),
        "point_cloud_manifest_path": str(manifest_path),
        "request_path": str(request_path),
        "call_result_path": str(root / "call_result.json"),
        "point_cloud_manifest": point_manifest,
        "call_result": call_result,
    }
    write_json(root / "probe_result.json", result)
    return result


def _load_json_or_payload(path_or_payload: str | Path | dict[str, Any]) -> Any:
    if isinstance(path_or_payload, dict):
        return path_or_payload
    return json.loads(Path(path_or_payload).read_text(encoding="utf-8"))


def _load_array(path: str | Path) -> np.ndarray:
    target = Path(path)
    if target.suffix in {".npy", ".npz"}:
        loaded = np.load(target)
        if isinstance(loaded, np.lib.npyio.NpzFile):
            key = "depth" if "depth" in loaded else loaded.files[0]
            return np.asarray(loaded[key])
        return np.asarray(loaded)
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError("Pillow is required for image depth inputs; use .npy/.npz or install Pillow") from exc
    return np.asarray(Image.open(target))


def _load_rgb(path: str | Path) -> np.ndarray:
    target = Path(path)
    if target.suffix in {".npy", ".npz"}:
        image = _load_array(target)
    else:
        try:
            from PIL import Image
        except ImportError as exc:
            raise ImportError("Pillow is required for RGB image inputs; use .npy/.npz or install Pillow") from exc
        image = np.asarray(Image.open(target).convert("RGB"))
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("RGB input must be HxWx3")
    return image[:, :, :3]


def _load_mask(path: str | Path) -> np.ndarray:
    mask = _load_array(path)
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    if mask.ndim != 2:
        raise ValueError("mask input must be 2D or single-channel image")
    return np.asarray(mask) > 0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None
