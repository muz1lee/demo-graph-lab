"""Narrow live readers for recording non-privileged perception evidence.

The module owns transport and sensor decoding only.  It does not write
artifacts, normalize grasp proposals into candidates, or expose a generic
pipeline call surface.  Optional simulator and NumPy imports stay inside the
functions that need them so the normal offline package remains importable.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
import ipaddress
import json
import math
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


_HEAD_INTRINSIC_KEYS = {"width", "height", "fx", "fy", "cx", "cy", "baseline"}
_HEALTH_KEYS = {
    "ok",
    "schema",
    "backend",
    "backend_ready",
    "backend_error",
    "source_policy",
}
_HEALTH_SCHEMA = "kw_independent.graspnet.health.v1"
_PREDICT_SCHEMA = "kw_independent.graspnet.raw_response.v1"
_PREDICT_REQUEST_KEYS = {
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
_PREDICT_RESPONSE_REQUIRED = {
    "ok",
    "schema",
    "backend",
    "coordinate_frame",
    "grasps",
    "input_reference",
}
_PREDICT_RESPONSE_ALLOWED = _PREDICT_RESPONSE_REQUIRED | {
    "checkpoint_path",
    "checkpoint_epoch",
    "error",
    "source_policy",
}
_BACKEND = "graspnet_baseline"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _urlopen_no_redirect(request, *, timeout):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)


class LiveSourceError(RuntimeError):
    """A live transport or response-contract failure with preserved evidence."""

    def __init__(
        self,
        message: str,
        *,
        payload: Mapping[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = None if payload is None else dict(payload)
        self.status_code = status_code


def _positive_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_s must be a number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError("timeout_s must be finite and positive")
    return timeout


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveSourceError(f"{path} must be a non-empty string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, path)


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveSourceError(f"{path} must be a JSON object")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise LiveSourceError(f"{path} keys must be non-empty strings")
    actual = set(value)
    missing = sorted(keys - actual)
    extra = sorted(actual - keys)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        raise LiveSourceError(f"{path} has invalid fields: {', '.join(details)}")
    return dict(value)


def _json_object(raw: bytes, context: str, status_code: int) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveSourceError(
            f"{context} returned invalid JSON: {exc}",
            status_code=status_code,
        ) from exc
    if not isinstance(value, dict):
        raise LiveSourceError(
            f"{context} response must be a JSON object",
            status_code=status_code,
        )
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise LiveSourceError(
            f"{context} response keys must be non-empty strings",
            payload=value,
            status_code=status_code,
        )
    return value


def _error_payload(raw: bytes) -> Mapping[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _http_json(
    url: str,
    *,
    timeout_s: float,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if body is not None:
        try:
            data = json.dumps(
                dict(body), allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise LiveSourceError(f"request is not finite JSON: {exc}") from exc
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _urlopen_no_redirect(request, timeout=timeout_s) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise LiveSourceError(
            f"{method} {url} returned HTTP {exc.code}",
            payload=_error_payload(raw),
            status_code=exc.code,
        ) from exc
    except (OSError, TimeoutError) as exc:
        raise LiveSourceError(f"{method} {url} transport failed: {exc}") from exc

    if isinstance(status, bool) or not isinstance(status, int):
        raise LiveSourceError(f"{method} {url} returned no integer HTTP status")
    payload = _json_object(raw, f"{method} {url}", status)
    if status != 200:
        raise LiveSourceError(
            f"{method} {url} returned HTTP {status}",
            payload=payload,
            status_code=status,
        )
    return payload


def _base_url(value: str) -> str:
    url = _required_string(value, "base_url").rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise LiveSourceError("base_url must be an HTTP origin without credentials")
    return url


def _loopback_base_url(value: str) -> str:
    url = _base_url(value)
    host = urllib.parse.urlsplit(url).hostname
    if host == "localhost":
        return url
    try:
        is_loopback = ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise LiveSourceError("GraspNet base_url must use a loopback host")
    return url


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveSourceError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise LiveSourceError(f"{path} must be finite")
    return number


def _finite_vector(value: Any, length: int, path: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise LiveSourceError(f"{path} must contain exactly {length} values")
    return [
        _finite_number(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]


def _normalized_intrinsics(
    value: Any,
    path: str,
) -> dict[str, int | float]:
    record = _exact_object(value, _HEAD_INTRINSIC_KEYS, path)
    width = record["width"]
    height = record["height"]
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or width <= 0
        or isinstance(height, bool)
        or not isinstance(height, int)
        or height <= 0
    ):
        raise LiveSourceError(f"{path} width and height must be positive integers")
    fx = _finite_number(record["fx"], f"{path}.fx")
    fy = _finite_number(record["fy"], f"{path}.fy")
    cx = _finite_number(record["cx"], f"{path}.cx")
    cy = _finite_number(record["cy"], f"{path}.cy")
    baseline = _finite_number(record["baseline"], f"{path}.baseline")
    if fx <= 0.0 or fy <= 0.0 or baseline <= 0.0:
        raise LiveSourceError(f"{path} focal lengths and baseline must be positive")
    if not 0.0 <= cx < width or not 0.0 <= cy < height:
        raise LiveSourceError(f"{path} principal point must lie inside the image")
    return {
        "width": width,
        "height": height,
        "fx": fx,
        "fy": fy,
        "cx": cx,
        "cy": cy,
        "baseline": baseline,
    }


def _decoded_info_result(value: Any, path: str) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        raise LiveSourceError(f"{path} must not be empty")
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(stripped)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            continue
    raise LiveSourceError(f"{path} is not a serialized vector")


def capture_head(
    *,
    socket_path: str | None = None,
    timeout_s: float = 10.0,
):
    """Request one synchronous head stereo snapshot from the capture bridge."""

    timeout = _positive_timeout(timeout_s)
    if socket_path is not None:
        _required_string(socket_path, "socket_path")

    from sim.camera.capture_bridge import request_stereo_snapshot

    snapshot = request_stereo_snapshot(
        "head",
        socket_path=socket_path,
        timeout=timeout,
    )
    if snapshot is None:
        raise LiveSourceError("head capture returned no snapshot")

    import numpy as np

    frame_id = getattr(snapshot, "frame_id", None)
    timestamp_s = getattr(snapshot, "timestamp_s", None)
    if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
        raise LiveSourceError("head snapshot frame_id must be a non-negative integer")
    timestamp = _finite_number(timestamp_s, "head snapshot timestamp_s")
    if timestamp < 0.0:
        raise LiveSourceError("head snapshot timestamp_s must be non-negative")
    left = getattr(snapshot, "left_bgr", None)
    right = getattr(snapshot, "right_bgr", None)
    depth = getattr(snapshot, "depth_m", None)
    if (
        not isinstance(left, np.ndarray)
        or left.dtype != np.uint8
        or left.ndim != 3
        or left.shape[2] != 3
    ):
        raise LiveSourceError("head snapshot left_bgr must be an HxWx3 uint8 array")
    if (
        not isinstance(right, np.ndarray)
        or right.dtype != np.uint8
        or right.shape != left.shape
    ):
        raise LiveSourceError("head snapshot right_bgr must match left_bgr")
    if (
        not isinstance(depth, np.ndarray)
        or depth.dtype.kind != "f"
        or depth.ndim != 2
        or depth.shape != left.shape[:2]
    ):
        raise LiveSourceError("head snapshot depth_m must be a matching float HxW array")
    return snapshot


def load_head_intrinsics(path: str | Path) -> dict[str, int | float]:
    """Load the exact head calibration fields used for point-cloud projection."""

    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveSourceError(f"cannot read head intrinsics {target}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise LiveSourceError("intrinsics root must be a JSON object")
    return _normalized_intrinsics(value.get("head"), "intrinsics.head")


def depth_to_point_cloud(depth_m, intrinsics: Mapping[str, Any]):
    """Project meter-valued depth into OpenCV head-optical XYZ points."""

    import numpy as np

    if not isinstance(depth_m, np.ndarray) or depth_m.ndim != 2:
        raise LiveSourceError("depth_m must be a two-dimensional NumPy array")
    if depth_m.dtype.kind != "f":
        raise LiveSourceError("depth_m must use a floating-point dtype in meters")
    values = _normalized_intrinsics(intrinsics, "intrinsics")
    width = values["width"]
    height = values["height"]
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or depth_m.shape != (height, width)
    ):
        raise LiveSourceError("depth_m shape does not match intrinsics")
    fx = values["fx"]
    fy = values["fy"]
    cx = values["cx"]
    cy = values["cy"]

    z_image = depth_m.astype(np.float32, copy=False)
    valid = np.isfinite(z_image) & (z_image > 0.0)
    rows, columns = np.nonzero(valid)
    z = z_image[valid]
    x = (columns.astype(np.float32) - np.float32(cx)) * z / np.float32(fx)
    y = (rows.astype(np.float32) - np.float32(cy)) * z / np.float32(fy)
    return np.column_stack((x, y, z)).astype(np.float32, copy=False)


class ReadOnlyProprioClient:
    """Read exactly two arm joint vectors and two end-effector poses."""

    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self.base_url = _base_url(base_url)
        self.timeout_s = _positive_timeout(timeout_s)

    def _info(self, name: str, arm_id: int) -> Any:
        if name not in {"get_qpos", "get_xquat"} or arm_id not in {0, 1}:
            raise LiveSourceError("unsupported proprioception read")
        kwargs = {"arm_id": arm_id}
        query = urllib.parse.urlencode(
            {
                "action": "info",
                "name": name,
                "kwargs": json.dumps(kwargs, separators=(",", ":")),
            }
        )
        payload = _http_json(
            f"{self.base_url}/run?{query}",
            timeout_s=self.timeout_s,
        )
        if set(payload) - {"ok", "result", "error"}:
            raise LiveSourceError(
                f"info:{name} response has unexpected fields",
                payload=payload,
                status_code=200,
            )
        if payload.get("ok") is not True:
            raise LiveSourceError(
                f"info:{name} failed",
                payload=payload,
                status_code=200,
            )
        if "result" not in payload:
            raise LiveSourceError(
                f"info:{name} response is missing result",
                payload=payload,
                status_code=200,
            )
        return _decoded_info_result(payload["result"], f"info:{name}.result")

    def read(self) -> dict[str, Any]:
        joint_by_arm = {
            arm_id: _finite_vector(
                self._info("get_qpos", arm_id), 7, f"get_qpos[{arm_id}]"
            )
            for arm_id in (0, 1)
        }
        pose_by_arm = {
            arm_id: _finite_vector(
                self._info("get_xquat", arm_id), 7, f"get_xquat[{arm_id}]"
            )
            for arm_id in (0, 1)
        }
        for arm_id, pose in pose_by_arm.items():
            norm = math.sqrt(sum(component * component for component in pose[3:]))
            if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
                raise LiveSourceError(
                    f"get_xquat[{arm_id}] quaternion must be unit length"
                )
        calls = [
            {
                "endpoint": "/run",
                "action": "info",
                "name": name,
                "kwargs": {"arm_id": arm_id},
            }
            for name in ("get_qpos", "get_xquat")
            for arm_id in (0, 1)
        ]
        return {
            "joint_positions": joint_by_arm[0] + joint_by_arm[1],
            "gripper_positions": [],
            "end_effector_frame": "robot_base",
            "end_effector_poses": {
                "left": pose_by_arm[0],
                "right": pose_by_arm[1],
            },
            "calls": calls,
        }


class GraspNetReadClient:
    """Read one configured GraspNet service through its two fixed endpoints."""

    def __init__(self, base_url: str, timeout_s: float = 300.0) -> None:
        self.base_url = _loopback_base_url(base_url)
        self.timeout_s = _positive_timeout(timeout_s)

    def health(self) -> dict[str, Any]:
        payload = _http_json(
            f"{self.base_url}/health",
            timeout_s=self.timeout_s,
        )
        try:
            root = _exact_object(payload, _HEALTH_KEYS, "graspnet health")
            if root["schema"] != _HEALTH_SCHEMA:
                raise LiveSourceError("unknown GraspNet health schema")
            backend = _required_string(root["backend"], "graspnet health backend")
            if backend == "fixture":
                raise LiveSourceError("fixture GraspNet backend is not real evidence")
            if backend != _BACKEND:
                raise LiveSourceError(f"unsupported GraspNet backend: {backend!r}")
            backend_error = root["backend_error"]
            if backend_error is not None and (
                not isinstance(backend_error, str) or not backend_error.strip()
            ):
                raise LiveSourceError(
                    "graspnet health backend_error must be null or a non-empty string"
                )
            _required_string(root["source_policy"], "graspnet health source_policy")
            if root["ok"] is not True or root["backend_ready"] is not True:
                raise LiveSourceError("GraspNet backend is not ready")
            if backend_error is not None:
                raise LiveSourceError("GraspNet health reports a backend error")
        except LiveSourceError as exc:
            raise LiveSourceError(
                str(exc), payload=payload, status_code=200
            ) from exc
        return payload

    def predict(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_record = _exact_object(
            request, _PREDICT_REQUEST_KEYS, "GraspNet predict request"
        )
        for name in ("image_path", "depth_path", "mask_path", "object_hint"):
            _optional_string(
                request_record[name], f"GraspNet predict request {name}"
            )
        _required_string(
            request_record["point_cloud_path"],
            "GraspNet predict request point_cloud_path",
        )
        _required_string(
            request_record["frame_id"],
            "GraspNet predict request frame_id",
        )
        frame = _required_string(
            request_record["coordinate_frame"],
            "GraspNet predict request coordinate_frame",
        )
        if frame.lower() == "unknown":
            raise LiveSourceError("GraspNet predict coordinate_frame must be known")
        camera_intrinsics = request_record["camera_intrinsics"]
        if camera_intrinsics is not None and not isinstance(
            camera_intrinsics, Mapping
        ):
            raise LiveSourceError(
                "GraspNet predict request camera_intrinsics must be null or an object"
            )
        if not isinstance(request_record["extra"], Mapping):
            raise LiveSourceError("GraspNet predict request extra must be an object")
        try:
            request_record = json.loads(json.dumps(
                request_record, allow_nan=False, separators=(",", ":")
            ))
        except (TypeError, ValueError) as exc:
            raise LiveSourceError(
                f"GraspNet predict request is not finite JSON: {exc}"
            ) from exc

        payload = _http_json(
            f"{self.base_url}/predict",
            timeout_s=self.timeout_s,
            body=request_record,
        )
        if payload.get("ok") is not True:
            raise LiveSourceError(
                "GraspNet prediction failed", payload=payload, status_code=200
            )
        try:
            if not isinstance(payload, Mapping):
                raise LiveSourceError("GraspNet predict response must be an object")
            root = dict(payload)
            missing = sorted(_PREDICT_RESPONSE_REQUIRED - set(root))
            extra = sorted(set(root) - _PREDICT_RESPONSE_ALLOWED)
            if missing or extra:
                raise LiveSourceError(
                    f"GraspNet predict response has invalid fields: "
                    f"missing={missing}, extra={extra}"
                )
            if root["schema"] != _PREDICT_SCHEMA:
                raise LiveSourceError("unknown GraspNet predict schema")
            backend = _required_string(root["backend"], "GraspNet predict backend")
            if backend == "fixture":
                raise LiveSourceError("fixture GraspNet backend is not real evidence")
            if backend != _BACKEND:
                raise LiveSourceError(f"unsupported GraspNet backend: {backend!r}")
            response_frame = _required_string(
                root["coordinate_frame"], "GraspNet predict response coordinate_frame"
            )
            if response_frame != frame:
                raise LiveSourceError(
                    "GraspNet response coordinate_frame does not match the request"
                )
            if not isinstance(root["grasps"], list):
                raise LiveSourceError("GraspNet response grasps must be an array")
            if root["input_reference"] != request_record:
                raise LiveSourceError("GraspNet input_reference does not echo the request")
        except LiveSourceError as exc:
            raise LiveSourceError(
                str(exc), payload=payload, status_code=200
            ) from exc
        return payload
