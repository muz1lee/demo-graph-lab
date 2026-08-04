"""Explicit record stages for one graph-anchored object perception chain.

The generated policy never calls this module.  Qwen and SAM3 produce reviewable
proposals; local code records the requested graph anchor and recomputes geometry
without claiming that the models verified instance identity.  The final stage
preserves raw GraspNet rows and deliberately stops before candidate normalization.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import os
from pathlib import Path
import time
from typing import Any

from ..perception.adapters import (
    observation_from_record,
    validate_graspnet_response_record,
    validate_point_cloud_manifest_record,
)
from ..perception.object_pipeline import (
    GeometryStatus,
    MASK_SCHEMA,
    OBJECT_POINT_CLOUD_SCHEMA,
    build_object_point_cloud,
    estimate_rack_hole_geometry,
    make_object_assignment_record,
    validate_mask_record,
    validate_object_assignment_record,
)
from ..perception.operators import fit_principal_axis
from .planning_record import (
    _load_manifest,
    _read_json,
    _record_error,
    _revalidate_record_plan,
    _required_string,
    _utc_now,
    _write_json,
)


_GROUND_RESULT_SCHEMA = "demo_graph_lab.object_grounding_record.v1"
_SEGMENT_RESULT_SCHEMA = "demo_graph_lab.object_segmentation_record.v1"
_PROJECT_RESULT_SCHEMA = "demo_graph_lab.object_projection_record.v1"

_PERCEPTION_REQUEST_KEYS = {"hole_name", "resolver", "anchor", "prompt"}
_ANCHOR_KEYS = {"object_id", "part", "instance", "selection"}

_GROUND_ARTIFACTS = {
    "object_grounding_image": "grounding/input.jpg",
    "object_grounding_request": "grounding/request.json",
    "object_grounding_raw": "grounding/raw.json",
    "object_grounding_result": "grounding/result.json",
    "object_grounding_call": "grounding/call.json",
}
_SEGMENT_ARTIFACTS = {
    "object_segmentation_request": "segmentation/request.json",
    "object_segmentation_raw": "segmentation/raw.json",
    "object_mask_png": "segmentation/mask.png",
    "object_mask": "segmentation/mask.npy",
    "object_mask_record": "segmentation/mask_record.json",
    "object_segmentation_result": "segmentation/result.json",
    "object_segmentation_call": "segmentation/call.json",
}
_PROJECT_ARTIFACTS = {
    "object_projection_request": "object/request.json",
    "object_assignment": "object/assignment.json",
    "object_point_cloud": "object/pointcloud.npz",
    "object_pixel_lineage": "object/pixels_rc.npy",
    "object_cloud_manifest_full": "object/cloud_manifest.json",
    "object_cloud_manifest": "object/pointcloud_manifest.json",
    "rack_hole_geometry": "object/hole_geometry.json",
    "object_observation": "object/observation.json",
    "object_projection_result": "object/result.json",
    "object_projection_call": "object/call.json",
}
_PREDICT_ARTIFACTS = {
    "object_graspnet_request": "object_graspnet/request.json",
    "object_graspnet_health": "object_graspnet/health.json",
    "object_graspnet_raw": "object_graspnet/raw_response.json",
    "object_graspnet_result": "object_graspnet/result.json",
    "object_graspnet_call": "object_graspnet/call.json",
    "object_graspnet_error_payload": "object_graspnet/raw.json",
}


def _exact_object(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be an object")
    missing = sorted(keys - set(value))
    extra = sorted(set(value) - keys)
    if missing or extra:
        raise ValueError(f"{path} has invalid fields: missing={missing}, extra={extra}")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, path)


def _plan_context(root: Path) -> tuple[dict, dict, Mapping[str, Any]]:
    plan, expected_request, config = _revalidate_record_plan(root)
    request = _exact_object(
        plan.get("perception_request"),
        _PERCEPTION_REQUEST_KEYS,
        "plan.perception_request",
    )
    hole_name = _required_string(request["hole_name"], "perception_request.hole_name")
    resolver = _required_string(request["resolver"], "perception_request.resolver")
    supported = {"grasp_candidate", "principal_axis", "part_center", "part_axis"}
    if resolver not in supported:
        raise ValueError(f"unsupported perception resolver: {resolver!r}")
    prompt = _required_string(request["prompt"], "perception_request.prompt")
    anchor = _exact_object(
        request["anchor"], _ANCHOR_KEYS, "perception_request.anchor"
    )
    object_id = _required_string(anchor["object_id"], "anchor.object_id")
    part = _required_string(anchor["part"], "anchor.part")
    instance = _optional_string(anchor["instance"], "anchor.instance")
    selection = _optional_string(anchor["selection"], "anchor.selection")
    if part == "whole" and (instance is not None or selection is not None):
        raise ValueError("whole-object anchor cannot contain instance or selection")
    if part == "hole" and instance is None and selection is None:
        raise ValueError("hole anchor requires instance or selection")
    if resolver in {"part_center", "part_axis"} and part != "hole":
        raise ValueError(f"resolver {resolver!r} requires a hole anchor")

    if dict(request) != expected_request:
        raise ValueError("plan.perception_request no longer matches graph/objects")
    return plan, {
        "hole_name": hole_name,
        "resolver": resolver,
        "anchor": {
            "object_id": object_id,
            "part": part,
            "instance": instance,
            "selection": selection,
        },
        "prompt": prompt,
    }, config


def _config_string(config: Mapping[str, Any], name: str) -> str:
    return _required_string(config.get(name), f"plan.config.{name}")


def _config_timeout(config: Mapping[str, Any]) -> float:
    value = config.get("timeout_s")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("plan.config.timeout_s must be finite and positive")
    return float(value)


def _config_positive_integer(config: Mapping[str, Any], name: str) -> int:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"plan.config.{name} must be a positive integer")
    return value


def _record_existing(
    root: Path,
    manifest: dict[str, Any],
    artifacts: Mapping[str, str],
) -> None:
    for name, relative in artifacts.items():
        if (root / relative).is_file():
            manifest["artifacts"][name] = relative


def _complete(
    root: Path,
    manifest: dict[str, Any],
    *,
    status: str,
    artifacts: Mapping[str, str],
) -> dict[str, Any]:
    _record_existing(root, manifest, artifacts)
    manifest["status"] = status
    manifest["updated_at"] = _utc_now()
    manifest["last_error"] = None
    _write_json(root / "manifest.json", manifest)
    return manifest


def _preserve_source_error(directory: Path, error: Exception) -> None:
    payload = getattr(error, "payload", None)
    if payload is not None:
        target = directory / (
            "raw.json" if not (directory / "raw.json").exists() else "error_payload.json"
        )
        _write_json(target, payload)
    raw_body = getattr(error, "raw_body", None)
    if isinstance(raw_body, bytes):
        (directory / "error_raw.bin").write_bytes(raw_body)


def _fail_stage(
    root: Path,
    manifest: dict[str, Any],
    *,
    step: str,
    directory: Path,
    call: dict[str, Any],
    started: float,
    error: Exception,
    artifacts: Mapping[str, str],
) -> None:
    _preserve_source_error(directory, error)
    call.update({
        "status": "error",
        "duration_s": time.monotonic() - started,
        "error": {
            "type": type(error).__name__,
            "message": str(error),
            "http_status": getattr(error, "status_code", None),
        },
    })
    _write_json(directory / "call.json", call)
    _record_existing(root, manifest, artifacts)
    _record_error(root, manifest, step=step, error=error)


def _frozen_observation(root: Path):
    record = _read_json(root / "observation.json")
    if not isinstance(record, Mapping):
        raise ValueError("observation.json must be an object")
    return record, observation_from_record(record)


def _image_array(root: Path, observation):
    import numpy as np

    path = (root / "sensor/head_left_bgr.npy").resolve()
    if str(path) not in observation.sensor_refs or not path.is_file():
        raise ValueError("frozen head image does not belong to observation")
    image = np.load(path, allow_pickle=False)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("frozen head image must be HxWx3 uint8 BGR")
    return image


def _jpeg_bytes(image) -> bytes:
    try:
        import cv2
    except ImportError as error:  # pragma: no cover - optional runtime install
        raise RuntimeError("object recording requires opencv-python") from error
    ok, encoded = cv2.imencode(
        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    if not ok:
        raise RuntimeError("failed to encode frozen observation as JPEG")
    value = encoded.tobytes()
    if not value.startswith(b"\xff\xd8") or not value.endswith(b"\xff\xd9"):
        raise RuntimeError("OpenCV returned an invalid JPEG byte stream")
    return value


def _validated_grounding_reference(
    value: Any,
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    reference = _exact_object(
        value,
        {"rank", "bbox_1000", "bbox_pixel"},
        "grounding reference",
    )
    if isinstance(reference["rank"], bool) or reference["rank"] != 1:
        raise ValueError("selected grounding reference must have rank 1")
    normalized = reference["bbox_1000"]
    if (
        not isinstance(normalized, list)
        or len(normalized) != 4
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            for item in normalized
        )
    ):
        raise ValueError("selected grounding bbox_1000 must contain four numbers")
    x1, y1, x2, y2 = (float(item) for item in normalized)
    if not (
        0.0 <= x1 < x2 <= 1000.0
        and 0.0 <= y1 < y2 <= 1000.0
    ):
        raise ValueError("selected grounding bbox_1000 is outside its contract")
    pixels = reference["bbox_pixel"]
    if (
        not isinstance(pixels, list)
        or len(pixels) != 4
        or any(isinstance(item, bool) or not isinstance(item, int) for item in pixels)
    ):
        raise ValueError("selected grounding bbox_pixel must contain four integers")
    expected_pixels = [
        max(0, min(width - 1, math.floor(x1 * width / 1000.0))),
        max(0, min(height - 1, math.floor(y1 * height / 1000.0))),
        max(1, min(width, math.ceil(x2 * width / 1000.0))),
        max(1, min(height, math.ceil(y2 * height / 1000.0))),
    ]
    if pixels != expected_pixels:
        raise ValueError("grounding pixel bbox does not match normalized bbox")
    return {
        "rank": 1,
        "bbox_1000": [x1, y1, x2, y2],
        "bbox_pixel": list(pixels),
    }


def _selected_grounding(root: Path, request: Mapping[str, Any]) -> dict[str, Any]:
    result = _read_json(root / "grounding/result.json")
    logical_request = _read_json(root / "grounding/request.json")
    if not isinstance(logical_request, Mapping):
        raise ValueError("grounding request is invalid")
    image_shape = logical_request.get("image_shape")
    if (
        not isinstance(image_shape, list)
        or len(image_shape) != 2
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in image_shape
        )
    ):
        raise ValueError("grounding request image_shape is invalid")
    result = _exact_object(
        result,
        {
            "schema",
            "status",
            "observation_id",
            "hole_name",
            "resolver",
            "anchor",
            "image_ref",
            "prompt",
            "reference_count",
            "selected_reference",
        },
        "grounding result",
    )
    if result["schema"] != _GROUND_RESULT_SCHEMA or result["status"] != "ACCEPTED":
        raise ValueError("grounding result is not accepted")
    if result["reference_count"] != 1 or not isinstance(
        result["selected_reference"], Mapping
    ):
        raise ValueError("grounding result must contain exactly one reference")
    for field in ("hole_name", "resolver", "anchor", "prompt"):
        if result[field] != request[field] or logical_request.get(field) != request[field]:
            raise ValueError(f"grounding {field} does not match plan")
    for field in ("observation_id", "image_ref", "prompt"):
        if result[field] != logical_request.get(field):
            raise ValueError(f"grounding result {field} does not match request")
    reference = _validated_grounding_reference(
        result["selected_reference"],
        width=image_shape[1],
        height=image_shape[0],
    )
    return {**dict(result), "selected_reference": reference}


def ground_record(
    record_dir: str | Path,
    *,
    allow_model_read: bool = False,
    source_module=None,
    qwen_token: str | None = None,
) -> dict[str, Any]:
    """Freeze one JPEG and accept exactly one Qwen grounding reference."""

    if allow_model_read is not True:
        raise PermissionError("ground requires --allow-model-read")
    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") != "OBSERVATION_RECORDED":
        raise ValueError("ground requires manifest status OBSERVATION_RECORDED")
    directory = root / "grounding"
    if directory.exists():
        raise FileExistsError("grounding artifacts already exist; use a new record dir")
    directory.mkdir()
    started = time.monotonic()
    call = {
        "step": "ground",
        "started_at": _utc_now(),
        "allow_model_read": True,
        "status": "running",
    }
    try:
        _, request_spec, config = _plan_context(root)
        _, observation = _frozen_observation(root)
        image = _image_array(root, observation)
        height, width = image.shape[:2]
        jpeg = _jpeg_bytes(image)
        image_path = (directory / "input.jpg").resolve()
        image_path.write_bytes(jpeg)

        endpoint = _config_string(config, "qwen_url")
        model = _config_string(config, "qwen_model")
        logical_request = {
            "schema": "demo_graph_lab.object_grounding_request.v1",
            "observation_id": observation.observation_id,
            "image_ref": str(image_path),
            "image_shape": [height, width],
            "hole_name": request_spec["hole_name"],
            "resolver": request_spec["resolver"],
            "anchor": request_spec["anchor"],
            "prompt": request_spec["prompt"],
            "endpoint": endpoint,
            "model": model,
            "top_k": 2,
        }
        _write_json(directory / "request.json", logical_request)
        call.update({"service_url": endpoint, "model": model})
        token = (
            qwen_token
            or os.environ.get("QWEN_AUTH_TOKEN")
            or os.environ.get("QWEN_API_KEY")
        )
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Qwen token is required outside plan.json")
        if source_module is None:
            from ..perception import semantic_sources as source_module
        client = source_module.QwenGroundingClient(
            endpoint,
            token=token,
            model=model,
            timeout_s=_config_timeout(config),
        )
        response = client.ground(
            jpeg,
            prompt=request_spec["prompt"],
            image_width=width,
            image_height=height,
            top_k=2,
        )
        if not isinstance(response, Mapping):
            raise TypeError("Qwen grounding result must be an object")
        raw = response.get("raw_response")
        if not isinstance(raw, Mapping):
            raise ValueError("Qwen grounding result is missing raw_response")
        _write_json(directory / "raw.json", raw)
        references = response.get("references")
        if not isinstance(references, list):
            raise ValueError("Qwen grounding references must be a list")
        accepted = len(references) == 1
        selected_reference = (
            _validated_grounding_reference(
                references[0],
                width=width,
                height=height,
            )
            if accepted else None
        )
        result = {
            "schema": _GROUND_RESULT_SCHEMA,
            "status": "ACCEPTED" if accepted else "REJECTED",
            "observation_id": observation.observation_id,
            "hole_name": request_spec["hole_name"],
            "resolver": request_spec["resolver"],
            "anchor": request_spec["anchor"],
            "image_ref": str(image_path),
            "prompt": request_spec["prompt"],
            "reference_count": len(references),
            "selected_reference": selected_reference,
        }
        _write_json(directory / "result.json", result)
        if not accepted:
            raise ValueError(
                "Qwen grounding must return exactly one reference; "
                f"received {len(references)}"
            )
        _selected_grounding(root, request_spec)
        call.update({
            "status": "ok",
            "duration_s": time.monotonic() - started,
        })
        _write_json(directory / "call.json", call)
        return _complete(
            root,
            manifest,
            status="GROUNDING_RECORDED",
            artifacts=_GROUND_ARTIFACTS,
        )
    except Exception as error:
        _fail_stage(
            root,
            manifest,
            step="ground",
            directory=directory,
            call=call,
            started=started,
            error=error,
            artifacts=_GROUND_ARTIFACTS,
        )
        raise


def _decode_binary_png(mask_bytes: bytes, *, shape: tuple[int, int]):
    import numpy as np

    try:
        import cv2
    except ImportError as error:  # pragma: no cover - optional runtime install
        raise RuntimeError("object recording requires opencv-python") from error
    raw = np.frombuffer(mask_bytes, dtype=np.uint8)
    decoded = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if decoded is None or decoded.ndim != 2 or decoded.shape != shape:
        raise ValueError("SAM3 mask PNG must decode to the frozen image shape")
    values = np.unique(decoded)
    if len(values) > 2 or 0 not in values or values[-1] <= 0:
        raise ValueError("SAM3 mask PNG must contain one strict binary channel")
    return (decoded > 0).astype(np.bool_, copy=False)


def _validated_mask_evidence(
    root: Path,
    request_spec: Mapping[str, Any],
    observation,
):
    """Rebind the frozen mask to the current plan and accepted Qwen box."""

    import numpy as np

    grounding = _selected_grounding(root, request_spec)
    image = _image_array(root, observation)
    shape = image.shape[:2]
    grounding_request = _read_json(root / "grounding/request.json")
    if (
        not isinstance(grounding_request, Mapping)
        or grounding_request.get("image_shape") != list(shape)
    ):
        raise ValueError("grounding image shape no longer matches frozen observation")

    mask_path = (root / "segmentation/mask.npy").resolve()
    png_path = (root / "segmentation/mask.png").resolve()
    mask_record_path = (root / "segmentation/mask_record.json").resolve()
    grounding_path = (root / "grounding/result.json").resolve()
    image_path = (root / "grounding/input.jpg").resolve()
    for path in (mask_path, png_path, mask_record_path, grounding_path, image_path):
        if not path.is_file():
            raise FileNotFoundError(f"record evidence no longer exists: {path}")
    if image_path.read_bytes() != _jpeg_bytes(image):
        raise ValueError("grounding JPEG no longer matches frozen head image")

    mask = np.load(mask_path, allow_pickle=False)
    mask_record = _read_json(mask_record_path)
    if not isinstance(mask_record, Mapping):
        raise ValueError("mask record is invalid")
    mask_record = validate_mask_record(
        mask_record,
        mask,
        expected_observation_id=observation.observation_id,
    )
    reference = grounding["selected_reference"]
    bbox = list(reference["bbox_pixel"])
    expected_mask_fields = {
        "image_ref": str(image_path),
        "grounding_ref": str(grounding_path),
        "proposal_id": f"qwen_reference_{reference['rank']}",
        "mask_ref": str(mask_path),
        "shape": list(shape),
    }
    for field, expected in expected_mask_fields.items():
        if mask_record[field] != expected:
            raise ValueError(f"mask record {field} no longer matches grounding")

    segment_request = _exact_object(
        _read_json(root / "segmentation/request.json"),
        {
            "schema",
            "observation_id",
            "image_ref",
            "grounding_ref",
            "proposal_id",
            "bbox_pixel",
            "endpoint",
        },
        "segmentation request",
    )
    expected_segment_request = {
        "schema": "demo_graph_lab.object_segmentation_request.v1",
        "observation_id": observation.observation_id,
        "image_ref": str(image_path),
        "grounding_ref": str(grounding_path),
        "proposal_id": expected_mask_fields["proposal_id"],
        "bbox_pixel": bbox,
    }
    for field, expected in expected_segment_request.items():
        if segment_request[field] != expected:
            raise ValueError(f"segmentation request {field} no longer matches grounding")

    segment_result = _exact_object(
        _read_json(root / "segmentation/result.json"),
        {
            "schema",
            "status",
            "observation_id",
            "image_ref",
            "grounding_ref",
            "bbox_pixel",
            "mask_png_ref",
            "mask_ref",
            "mask_record_ref",
            "mask_metadata",
            "detection_metadata",
        },
        "segmentation result",
    )
    expected_segment_result = {
        "schema": _SEGMENT_RESULT_SCHEMA,
        "status": "ACCEPTED",
        "observation_id": observation.observation_id,
        "image_ref": str(image_path),
        "grounding_ref": str(grounding_path),
        "bbox_pixel": bbox,
        "mask_png_ref": str(png_path),
        "mask_ref": str(mask_path),
        "mask_record_ref": str(mask_record_path),
    }
    for field, expected in expected_segment_result.items():
        if segment_result[field] != expected:
            raise ValueError(f"segmentation result {field} no longer matches grounding")

    png_mask = _decode_binary_png(png_path.read_bytes(), shape=shape)
    if not np.array_equal(mask, png_mask):
        raise ValueError("bool mask no longer matches the recorded SAM3 PNG")
    outside = mask.copy()
    outside[bbox[1]:bbox[3], bbox[0]:bbox[2]] = False
    if outside.any():
        raise ValueError("recorded mask no longer lies inside the accepted Qwen box")
    return grounding, mask_record, mask


def segment_record(
    record_dir: str | Path,
    *,
    allow_model_read: bool = False,
    source_module=None,
    sam3_token: str | None = None,
) -> dict[str, Any]:
    """Run SAM3 on the one accepted Qwen box and freeze a bool mask."""

    if allow_model_read is not True:
        raise PermissionError("segment requires --allow-model-read")
    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") != "GROUNDING_RECORDED":
        raise ValueError("segment requires manifest status GROUNDING_RECORDED")
    directory = root / "segmentation"
    if directory.exists():
        raise FileExistsError("segmentation artifacts already exist; use a new record dir")
    directory.mkdir()
    started = time.monotonic()
    call = {
        "step": "segment",
        "started_at": _utc_now(),
        "allow_model_read": True,
        "status": "running",
    }
    try:
        _, request_spec, config = _plan_context(root)
        _, observation = _frozen_observation(root)
        grounding = _selected_grounding(root, request_spec)
        jpeg_path = Path(grounding["image_ref"]).resolve()
        expected_jpeg = (root / "grounding/input.jpg").resolve()
        if jpeg_path != expected_jpeg or not jpeg_path.is_file():
            raise ValueError("grounding JPEG does not belong to this record")
        jpeg = jpeg_path.read_bytes()
        image = _image_array(root, observation)
        height, width = image.shape[:2]
        reference = grounding["selected_reference"]
        bbox = list(reference["bbox_pixel"])
        if not (
            0 <= bbox[0] < bbox[2] <= width
            and 0 <= bbox[1] < bbox[3] <= height
        ):
            raise ValueError("grounding bbox_pixel lies outside the frozen image")
        request = {
            "schema": "demo_graph_lab.object_segmentation_request.v1",
            "observation_id": observation.observation_id,
            "image_ref": str(jpeg_path),
            "grounding_ref": str((root / "grounding/result.json").resolve()),
            "proposal_id": f"qwen_reference_{reference['rank']}",
            "bbox_pixel": bbox,
            "endpoint": _config_string(config, "sam3_url"),
        }
        _write_json(directory / "request.json", request)
        call["service_url"] = request["endpoint"]
        if source_module is None:
            from ..perception import semantic_sources as source_module
        token = sam3_token or os.environ.get("SAM3_API_KEY")
        client = source_module.Sam3SegmentationClient(
            request["endpoint"],
            token=token,
            timeout_s=_config_timeout(config),
        )
        response = client.segment(
            jpeg,
            bbox_pixel=bbox,
            image_width=width,
            image_height=height,
        )
        if not isinstance(response, Mapping):
            raise TypeError("SAM3 segmentation result must be an object")
        raw = response.get("raw_response")
        mask_bytes = response.get("mask_bytes")
        if not isinstance(raw, Mapping) or not isinstance(mask_bytes, bytes):
            raise ValueError("SAM3 result is missing raw response or mask bytes")
        _write_json(directory / "raw.json", raw)
        png_path = (directory / "mask.png").resolve()
        png_path.write_bytes(mask_bytes)
        mask = _decode_binary_png(mask_bytes, shape=(height, width))
        outside = mask.copy()
        outside[bbox[1]:bbox[3], bbox[0]:bbox[2]] = False
        if outside.any():
            raise ValueError("SAM3 mask contains foreground outside the Qwen box")
        mask_path = (directory / "mask.npy").resolve()
        mask_record_path = (directory / "mask_record.json").resolve()
        import numpy as np

        np.save(mask_path, mask, allow_pickle=False)
        mask_record = {
            "schema": MASK_SCHEMA,
            "observation_id": observation.observation_id,
            "image_ref": str(jpeg_path),
            "grounding_ref": request["grounding_ref"],
            "proposal_id": request["proposal_id"],
            "mask_ref": str(mask_path),
            "shape": [height, width],
            "encoding": "bool",
            "foreground_pixels": int(np.count_nonzero(mask)),
        }
        mask_record = validate_mask_record(
            mask_record,
            mask,
            expected_observation_id=observation.observation_id,
        )
        _write_json(mask_record_path, mask_record)
        result = {
            "schema": _SEGMENT_RESULT_SCHEMA,
            "status": "ACCEPTED",
            "observation_id": observation.observation_id,
            "image_ref": str(jpeg_path),
            "grounding_ref": request["grounding_ref"],
            "bbox_pixel": bbox,
            "mask_png_ref": str(png_path),
            "mask_ref": str(mask_path),
            "mask_record_ref": str(mask_record_path),
            "mask_metadata": response.get("mask"),
            "detection_metadata": response.get("detection_metadata", {}),
        }
        _write_json(directory / "result.json", result)
        call.update({
            "status": "ok",
            "duration_s": time.monotonic() - started,
        })
        _write_json(directory / "call.json", call)
        return _complete(
            root,
            manifest,
            status="MASK_RECORDED",
            artifacts=_SEGMENT_ARTIFACTS,
        )
    except Exception as error:
        _fail_stage(
            root,
            manifest,
            step="segment",
            directory=directory,
            call=call,
            started=started,
            error=error,
            artifacts=_SEGMENT_ARTIFACTS,
        )
        raise


def _extent(points) -> dict[str, list[float]]:
    import numpy as np

    values = points.astype(np.float64)
    return {
        "min": [float(item) for item in values.min(axis=0)],
        "max": [float(item) for item in values.max(axis=0)],
    }


def project_record(record_dir: str | Path) -> dict[str, Any]:
    """Create a proposed anchor binding, object cloud, and derived observation."""

    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") != "MASK_RECORDED":
        raise ValueError("project requires manifest status MASK_RECORDED")
    directory = root / "object"
    if directory.exists():
        raise FileExistsError("object projection artifacts already exist")
    directory.mkdir()
    started = time.monotonic()
    call = {"step": "project", "started_at": _utc_now(), "status": "running"}
    try:
        _, request_spec, config = _plan_context(root)
        observation_record, observation = _frozen_observation(root)
        anchor = request_spec["anchor"]
        _, mask_record, mask = _validated_mask_evidence(
            root, request_spec, observation
        )
        mask_record_path = (root / "segmentation/mask_record.json").resolve()
        mask_path = (root / "segmentation/mask.npy").resolve()
        depth_path = (root / "sensor/head_depth_m.npy").resolve()
        calibration_path = (root / "calibration/bundle.json").resolve()
        expected = {
            str(mask_path), str(depth_path), str(calibration_path)
        }
        if not expected.issubset(set(observation.sensor_refs) | {str(mask_path)}):
            raise ValueError("projection inputs do not belong to the frozen observation")
        import numpy as np

        depth = np.load(depth_path, allow_pickle=False)
        calibration = _read_json(calibration_path)
        if not isinstance(calibration, Mapping):
            raise ValueError("calibration record is invalid")
        intrinsics = calibration.get("intrinsics")

        cloud_path = (directory / "pointcloud.npz").resolve()
        pixels_path = (directory / "pixels_rc.npy").resolve()
        assignment_path = (directory / "assignment.json").resolve()
        full_manifest_path = (directory / "cloud_manifest.json").resolve()
        compact_manifest_path = (directory / "pointcloud_manifest.json").resolve()
        hole_geometry_path = (directory / "hole_geometry.json").resolve()
        object_observation_path = (directory / "observation.json").resolve()
        request = {
            "schema": "demo_graph_lab.object_projection_request.v1",
            "observation_id": observation.observation_id,
            "hole_name": request_spec["hole_name"],
            "resolver": request_spec["resolver"],
            "anchor": anchor,
            "depth_ref": str(depth_path),
            "mask_record_ref": str(mask_record_path),
            "calibration_ref": str(calibration_path),
        }
        _write_json(directory / "request.json", request)
        assignment = make_object_assignment_record(
            observation_id=observation.observation_id,
            object_id=anchor["object_id"],
            part=anchor["part"],
            instance=anchor["instance"],
            selection=anchor["selection"],
            grounding_ref=str((root / "grounding/result.json").resolve()),
            mask_ref=str(mask_path),
            cloud_ref=str(cloud_path),
            cloud_manifest_ref=str(full_manifest_path),
            frame=observation.frame,
            calibration_ref=observation.calibration_ref,
        )
        cloud = build_object_point_cloud(
            depth,
            mask,
            intrinsics,
            mask_record=mask_record,
            assignment_record=assignment,
            assignment_ref=str(assignment_path),
            depth_ref=str(depth_path),
            pixel_lineage_ref=str(pixels_path),
            expected_graph_object=anchor,
            min_points=_config_positive_integer(config, "min_object_points"),
        )
        extent = _extent(cloud.points)
        axis = None
        hole_geometry = None
        if request_spec["resolver"] in {"part_center", "part_axis"}:
            jpeg_path = (root / "grounding/input.jpg").resolve()
            try:
                import cv2
            except ImportError as error:  # pragma: no cover - optional live install
                raise RuntimeError("rack-hole geometry requires opencv-python") from error
            image = cv2.imread(str(jpeg_path), cv2.IMREAD_COLOR)
            if image is None or image.shape != (*depth.shape, 3):
                raise ValueError("frozen grounding JPEG cannot be decoded at RGB-D shape")
            geometry = estimate_rack_hole_geometry(
                image,
                depth,
                mask,
                intrinsics,
                observation_id=observation.observation_id,
                frame=observation.frame,
                calibration_ref=observation.calibration_ref,
                rgb_ref=str(jpeg_path),
                depth_ref=str(depth_path),
                roi_record=mask_record,
            )
            hole_geometry = geometry.to_record()
            _write_json(hole_geometry_path, hole_geometry)
        else:
            axis = fit_principal_axis(cloud.points)
        np.savez_compressed(cloud_path, points=cloud.points)
        np.save(pixels_path, cloud.pixels_rc, allow_pickle=False)
        _write_json(full_manifest_path, cloud.manifest_record())
        _write_json(assignment_path, assignment)
        compact_manifest = {
            "artifact_ref": str(cloud_path),
            "unit": "meter",
            "frame": observation.frame,
            "calibration_ref": observation.calibration_ref,
            "evidence_ref": str(full_manifest_path),
        }
        _write_json(compact_manifest_path, compact_manifest)

        derived_refs = [
            str((root / relative).resolve())
            for relative in (
                "grounding/input.jpg",
                "grounding/result.json",
                "segmentation/mask.png",
                "segmentation/mask.npy",
                "segmentation/mask_record.json",
                "object/assignment.json",
                "object/pointcloud.npz",
                "object/pixels_rc.npy",
                "object/cloud_manifest.json",
                "object/pointcloud_manifest.json",
            )
        ]
        if hole_geometry is not None:
            derived_refs.append(str(hole_geometry_path))
        object_observation_record = {
            **dict(observation_record),
            "sensor_refs": list(dict.fromkeys([
                *observation_record["sensor_refs"], *derived_refs,
            ])),
            "objects": list(observation_record.get("objects", [])),
        }
        object_observation = observation_from_record(object_observation_record)
        validate_point_cloud_manifest_record(
            compact_manifest,
            observation=object_observation,
        )
        _write_json(object_observation_path, object_observation_record)
        hole_geometry_status = (
            hole_geometry["status"] if hole_geometry is not None else None
        )
        _write_json(directory / "result.json", {
            "schema": _PROJECT_RESULT_SCHEMA,
            # 请求了几何却估不出来时不许写 ACCEPTED。记录本身确实发生了,所以
            # manifest 状态和退出码不变;只有这个词必须如实反映几何证据。
            "status": (
                "ACCEPTED"
                if hole_geometry_status in (None, GeometryStatus.PASS.value)
                else "GEOMETRY_UNKNOWN"
            ),
            "observation_id": observation.observation_id,
            "assignment_ref": str(assignment_path),
            "point_cloud_ref": str(cloud_path),
            "point_count": len(cloud.points),
            "pixel_lineage_ref": str(pixels_path),
            "extent": extent,
            "principal_axis": axis,
            "hole_geometry_ref": (
                str(hole_geometry_path) if hole_geometry is not None else None
            ),
            "hole_geometry_status": hole_geometry_status,
            "object_observation_ref": str(object_observation_path),
        })
        call.update({
            "status": "ok",
            "duration_s": time.monotonic() - started,
        })
        _write_json(directory / "call.json", call)
        return _complete(
            root,
            manifest,
            status="OBJECT_CLOUD_RECORDED",
            artifacts=_PROJECT_ARTIFACTS,
        )
    except Exception as error:
        _fail_stage(
            root,
            manifest,
            step="project",
            directory=directory,
            call=call,
            started=started,
            error=error,
            artifacts=_PROJECT_ARTIFACTS,
        )
        raise


def predict_record(
    record_dir: str | Path,
    *,
    allow_live_read: bool = False,
    source_module=None,
) -> dict[str, Any]:
    """Run GraspNet on the object-only cloud and publish no candidates."""

    if allow_live_read is not True:
        raise PermissionError("predict requires --allow-live-read")
    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") != "OBJECT_CLOUD_RECORDED":
        raise ValueError("predict requires manifest status OBJECT_CLOUD_RECORDED")
    directory = root / "object_graspnet"
    if directory.exists():
        raise FileExistsError("object GraspNet artifacts already exist")
    directory.mkdir()
    started = time.monotonic()
    call = {
        "step": "predict_object",
        "started_at": _utc_now(),
        "allow_live_read": True,
        "status": "running",
    }
    try:
        _, request_spec, config = _plan_context(root)
        if request_spec["resolver"] != "grasp_candidate":
            raise ValueError(
                "object GraspNet prediction requires resolver='grasp_candidate'"
            )
        object_observation_record = _read_json(root / "object/observation.json")
        if not isinstance(object_observation_record, Mapping):
            raise ValueError("object observation is invalid")
        observation = observation_from_record(object_observation_record)
        assignment_path = (root / "object/assignment.json").resolve()
        full_manifest_path = (root / "object/cloud_manifest.json").resolve()
        mask_path = (root / "segmentation/mask.npy").resolve()
        depth_path = (root / "sensor/head_depth_m.npy").resolve()
        pixels_path = (root / "object/pixels_rc.npy").resolve()
        _, mask_record, mask = _validated_mask_evidence(
            root, request_spec, observation
        )
        assignment_record = _read_json(assignment_path)
        if not isinstance(assignment_record, Mapping):
            raise ValueError("object assignment is invalid")
        assignment = validate_object_assignment_record(
            assignment_record,
            expected_observation_id=observation.observation_id,
            expected_graph_object=request_spec["anchor"],
            mask_record=mask_record,
        )
        if assignment["identity_status"] != "MODEL_PROPOSED":
            raise ValueError("object identity must remain MODEL_PROPOSED")
        expected_cloud = (root / "object/pointcloud.npz").resolve()
        expected_assignment_refs = {
            "grounding_ref": str((root / "grounding/result.json").resolve()),
            "mask_ref": str(mask_path),
            "cloud_ref": str(expected_cloud),
            "cloud_manifest_ref": str(full_manifest_path),
            "frame": observation.frame,
            "calibration_ref": observation.calibration_ref,
        }
        for field, expected in expected_assignment_refs.items():
            if assignment[field] != expected:
                raise ValueError(f"object assignment {field} does not belong to record")

        full = _read_json(full_manifest_path)
        full = _exact_object(
            full,
            {
                "schema",
                "observation_id",
                "identity_status",
                "graph_object",
                "artifact_ref",
                "assignment_ref",
                "source_image_ref",
                "source_depth_ref",
                "mask_ref",
                "pixel_lineage_ref",
                "pixel_layout",
                "point_layout",
                "point_count",
                "masked_pixel_count",
                "frame",
                "unit",
                "calibration_ref",
            },
            "object cloud manifest",
        )
        expected_full = {
            "schema": OBJECT_POINT_CLOUD_SCHEMA,
            "observation_id": observation.observation_id,
            "identity_status": "MODEL_PROPOSED",
            "graph_object": request_spec["anchor"],
            "artifact_ref": str(expected_cloud),
            "assignment_ref": str(assignment_path),
            "source_image_ref": str((root / "grounding/input.jpg").resolve()),
            "source_depth_ref": str(depth_path),
            "mask_ref": str(mask_path),
            "pixel_lineage_ref": str(pixels_path),
            "pixel_layout": "row_col",
            "point_layout": "x_y_z",
            "frame": observation.frame,
            "unit": "meter",
            "calibration_ref": observation.calibration_ref,
        }
        for field, expected in expected_full.items():
            if full[field] != expected:
                raise ValueError(f"object cloud manifest {field} does not match record")
        if (
            isinstance(full["point_count"], bool)
            or not isinstance(full["point_count"], int)
            or full["point_count"] <= 0
            or full["masked_pixel_count"] != mask_record.get("foreground_pixels")
        ):
            raise ValueError("object cloud manifest has invalid point counts")

        compact = _read_json(root / "object/pointcloud_manifest.json")
        if not isinstance(compact, Mapping):
            raise ValueError("object point-cloud manifest is invalid")
        compact = validate_point_cloud_manifest_record(compact, observation=observation)
        cloud_path = Path(compact["artifact_ref"]).resolve()
        if cloud_path != expected_cloud or not cloud_path.is_file():
            raise ValueError("object cloud does not belong to this record")
        expected_compact = {
            "artifact_ref": str(expected_cloud),
            "unit": "meter",
            "frame": observation.frame,
            "calibration_ref": observation.calibration_ref,
            "evidence_ref": str(full_manifest_path),
        }
        if compact != expected_compact:
            raise ValueError("point-cloud binding does not match full object manifest")
        import numpy as np

        with np.load(cloud_path, allow_pickle=False) as archive:
            if set(archive.files) != {"points"}:
                raise ValueError("object point cloud archive must contain only points")
            points = np.asarray(archive["points"])
        pixels = np.load(pixels_path, allow_pickle=False)
        if (
            points.dtype != np.dtype(np.float32)
            or points.ndim != 2
            or points.shape[1:] != (3,)
            or not np.isfinite(points).all()
            or pixels.dtype != np.dtype(np.int32)
            or pixels.shape != (len(points), 2)
            or len(points) != full["point_count"]
        ):
            raise ValueError("object point cloud payload does not match its manifest")
        calibration = _read_json(root / "calibration/bundle.json")
        if not isinstance(calibration, Mapping):
            raise ValueError("calibration record is invalid")
        depth = np.load(depth_path, allow_pickle=False)
        recomputed = build_object_point_cloud(
            depth,
            mask,
            calibration.get("intrinsics"),
            mask_record=mask_record,
            assignment_record=assignment,
            assignment_ref=str(assignment_path),
            depth_ref=str(depth_path),
            pixel_lineage_ref=str(pixels_path),
            expected_graph_object=request_spec["anchor"],
            min_points=_config_positive_integer(config, "min_object_points"),
        )
        if (
            recomputed.manifest_record() != dict(full)
            or not np.array_equal(points, recomputed.points)
            or not np.array_equal(pixels, recomputed.pixels_rc)
        ):
            raise ValueError(
                "object cloud no longer matches frozen RGB-D lineage"
            )
        request = {
            "image_path": str((root / "grounding/input.jpg").resolve()),
            "depth_path": str((root / "sensor/head_depth_m.npy").resolve()),
            "mask_path": str((root / "segmentation/mask.npy").resolve()),
            "point_cloud_path": str(cloud_path),
            "object_hint": None,
            "frame_id": observation.observation_id,
            "coordinate_frame": observation.frame,
            "camera_intrinsics": calibration["intrinsics"],
            "extra": {
                "max_grasps": _config_positive_integer(config, "max_grasps")
            },
        }
        _write_json(directory / "request.json", request)
        endpoint = _config_string(config, "graspnet_url")
        call["service_url"] = endpoint
        if source_module is None:
            from ..perception import live_sources as source_module
        client = source_module.GraspNetReadClient(
            endpoint,
            timeout_s=_config_timeout(config),
        )
        health = client.health()
        _write_json(directory / "health.json", health)
        response = client.predict(request)
        _write_json(directory / "raw_response.json", response)
        summary = validate_graspnet_response_record(
            response,
            observation=observation,
            point_cloud_manifest=compact,
        )
        _write_json(directory / "result.json", {
            "status": "valid_object_raw_response",
            "summary": summary,
            "identity_status": assignment["identity_status"],
            "graph_object": assignment["graph_object"],
            "assignment_ref": str(assignment_path),
            "object_cloud_manifest_ref": str(full_manifest_path),
            "candidate_artifact_created": False,
            "reason": (
                "Raw proposals preserve detector ids and stop before graph candidate "
                "normalization. The graph-anchor binding remains MODEL_PROPOSED."
            ),
        })
        call.update({
            "status": "ok",
            "duration_s": time.monotonic() - started,
        })
        _write_json(directory / "call.json", call)
        return _complete(
            root,
            manifest,
            status="OBJECT_RAW_GRASPNET_RECORDED",
            artifacts=_PREDICT_ARTIFACTS,
        )
    except Exception as error:
        _fail_stage(
            root,
            manifest,
            step="predict_object",
            directory=directory,
            call=call,
            started=started,
            error=error,
            artifacts=_PREDICT_ARTIFACTS,
        )
        raise
