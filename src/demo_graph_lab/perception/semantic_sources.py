"""Narrow semantic readers for one already-frozen JPEG observation.

The clients accept immutable JPEG bytes and explicit service configuration.
They own transport and response validation only; artifact writing and mask
decoding belong to the caller.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
import json
import math
import struct
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import zlib


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _urlopen_no_redirect(request, *, timeout):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)


class SemanticSourceError(RuntimeError):
    """Transport or response-contract failure with preserved source evidence."""

    def __init__(
        self,
        message: str,
        *,
        payload: Mapping[str, Any] | None = None,
        raw_body: bytes | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = None if payload is None else dict(payload)
        self.raw_body = raw_body
        self.status_code = status_code


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticSourceError(f"{path} must be a non-empty string")
    return value.strip()


def _positive_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_s must be a number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError("timeout_s must be finite and positive")
    return timeout


def _positive_integer(value: Any, path: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SemanticSourceError(f"{path} must be a positive integer")
    if maximum is not None and value > maximum:
        raise SemanticSourceError(f"{path} must be at most {maximum}")
    return value


def _endpoint(value: Any) -> str:
    endpoint = _required_text(value, "endpoint")
    parsed = urllib.parse.urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SemanticSourceError("endpoint has an invalid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port <= 0)
    ):
        raise SemanticSourceError(
            "endpoint must be an explicit HTTP URL without credentials, query, or fragment"
        )
    return endpoint


def _jpeg_bytes(value: Any) -> bytes:
    if not isinstance(value, bytes):
        raise SemanticSourceError("jpeg_bytes must be immutable bytes")
    if len(value) < 4 or not value.startswith(b"\xff\xd8") or not value.endswith(b"\xff\xd9"):
        raise SemanticSourceError("jpeg_bytes must contain one complete JPEG byte stream")
    return value


def _image_size(width: Any, height: Any) -> tuple[int, int]:
    return (
        _positive_integer(width, "image_width"),
        _positive_integer(height, "image_height"),
    )


def _image_metadata(jpeg: bytes, width: int, height: int) -> dict[str, Any]:
    return {
        "mime_type": "image/jpeg",
        "width": width,
        "height": height,
        "byte_length": len(jpeg),
    }


def _reject_json_constant(value: str):
    raise ValueError(f"non-finite JSON constant: {value}")


def _json_object(raw: bytes, context: str, status_code: int) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SemanticSourceError(
            f"{context} returned invalid finite JSON: {exc}",
            raw_body=raw,
            status_code=status_code,
        ) from exc
    if not isinstance(value, dict):
        raise SemanticSourceError(
            f"{context} response must be a JSON object",
            raw_body=raw,
            status_code=status_code,
        )
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise SemanticSourceError(
            f"{context} response keys must be non-empty strings",
            payload=value,
            status_code=status_code,
        )
    return value


def _optional_error_payload(raw: bytes) -> Mapping[str, Any] | None:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _post_json(
    endpoint: str,
    payload: Mapping[str, Any],
    *,
    timeout_s: float,
    authorization: str | None = None,
) -> dict[str, Any]:
    try:
        request_body = json.dumps(
            dict(payload),
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SemanticSourceError(f"request is not finite JSON: {exc}") from exc
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    request = urllib.request.Request(
        endpoint,
        data=request_body,
        headers=headers,
        method="POST",
    )
    try:
        with _urlopen_no_redirect(request, timeout=timeout_s) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise SemanticSourceError(
            f"POST {endpoint} returned HTTP {exc.code}",
            payload=_optional_error_payload(raw),
            raw_body=raw,
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SemanticSourceError(f"POST {endpoint} transport failed: {exc}") from exc

    if isinstance(status, bool) or not isinstance(status, int):
        raise SemanticSourceError(f"POST {endpoint} returned no integer HTTP status")
    if status != 200:
        raise SemanticSourceError(
            f"POST {endpoint} returned HTTP {status}",
            payload=_optional_error_payload(raw),
            raw_body=raw,
            status_code=status,
        )
    return _json_object(raw, f"POST {endpoint}", status)


def _finite_bbox(value: Any, path: str) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 4
    ):
        raise SemanticSourceError(f"{path} must contain four coordinates")
    bbox = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise SemanticSourceError(f"{path}[{index}] must be a number")
        number = float(item)
        if not math.isfinite(number):
            raise SemanticSourceError(f"{path}[{index}] must be finite")
        bbox.append(number)
    x1, y1, x2, y2 = bbox
    if not all(0.0 <= coordinate <= 1000.0 for coordinate in bbox):
        raise SemanticSourceError(f"{path} coordinates must lie in [0, 1000]")
    if x1 >= x2 or y1 >= y2:
        raise SemanticSourceError(f"{path} must have positive area")
    return bbox


def _bbox_1000_to_pixel(
    bbox: Sequence[float],
    width: int,
    height: int,
) -> list[int]:
    x1, y1, x2, y2 = bbox
    pixel = [
        math.floor(x1 * width / 1000.0),
        math.floor(y1 * height / 1000.0),
        math.ceil(x2 * width / 1000.0),
        math.ceil(y2 * height / 1000.0),
    ]
    pixel[0] = min(max(pixel[0], 0), width - 1)
    pixel[1] = min(max(pixel[1], 0), height - 1)
    pixel[2] = min(max(pixel[2], 1), width)
    pixel[3] = min(max(pixel[3], 1), height)
    if pixel[0] >= pixel[2] or pixel[1] >= pixel[3]:
        raise SemanticSourceError("normalized bbox becomes empty in pixel coordinates")
    return pixel


def _pixel_bbox(value: Any, width: int, height: int) -> list[int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise SemanticSourceError("bbox_pixel must contain four integer coordinates")
    bbox = list(value)
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise SemanticSourceError(
            "bbox_pixel must be a positive-area half-open box inside the image"
        )
    return bbox


def _qwen_references(
    response: Mapping[str, Any],
    *,
    width: int,
    height: int,
    top_k: int,
) -> list[dict[str, Any]]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise SemanticSourceError("Qwen response must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise SemanticSourceError("Qwen choice must be an object")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise SemanticSourceError("Qwen choice.message must be an object")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise SemanticSourceError("Qwen message content must be a non-empty string")
    try:
        value = json.loads(content, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SemanticSourceError(
            f"Qwen message content is not strict finite JSON: {exc}"
        ) from exc
    if not isinstance(value, Mapping) or set(value) != {"references"}:
        raise SemanticSourceError(
            "Qwen message content must contain only the references field"
        )
    raw_references = value["references"]
    if not isinstance(raw_references, list):
        raise SemanticSourceError("Qwen references must be an array")

    references = []
    for index, item in enumerate(raw_references):
        if not isinstance(item, Mapping) or set(item) != {"bbox"}:
            raise SemanticSourceError(
                f"Qwen references[{index}] must contain only bbox"
            )
        bbox = _finite_bbox(item["bbox"], f"Qwen references[{index}].bbox")
        references.append({
            "rank": index + 1,
            "bbox_1000": bbox,
            "bbox_pixel": _bbox_1000_to_pixel(bbox, width, height),
        })
    return references[:top_k]


def _png_metadata(value: bytes) -> dict[str, Any]:
    if not value.startswith(_PNG_SIGNATURE):
        raise SemanticSourceError("SAM3 mask is not a PNG byte stream")
    offset = len(_PNG_SIGNATURE)
    chunk_types = []
    width = height = bit_depth = color_type = None
    saw_idat = False
    saw_iend = False
    while offset < len(value):
        if len(value) - offset < 12:
            raise SemanticSourceError("SAM3 mask PNG has a truncated chunk")
        length = struct.unpack(">I", value[offset : offset + 4])[0]
        chunk_type = value[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(value):
            raise SemanticSourceError("SAM3 mask PNG has a truncated chunk payload")
        chunk_data = value[data_start:data_end]
        expected_crc = struct.unpack(">I", value[data_end:crc_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise SemanticSourceError("SAM3 mask PNG has an invalid chunk checksum")
        try:
            name = chunk_type.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SemanticSourceError("SAM3 mask PNG has a non-ASCII chunk type") from exc
        chunk_types.append(name)
        if len(chunk_types) == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise SemanticSourceError("SAM3 mask PNG must begin with one IHDR chunk")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if width <= 0 or height <= 0:
                raise SemanticSourceError("SAM3 mask PNG dimensions must be positive")
            if compression != 0 or filtering != 0 or interlace not in {0, 1}:
                raise SemanticSourceError("SAM3 mask PNG uses unsupported header values")
        elif chunk_type == b"IHDR":
            raise SemanticSourceError("SAM3 mask PNG contains multiple IHDR chunks")
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0:
                raise SemanticSourceError("SAM3 mask PNG IEND chunk must be empty")
            saw_iend = True
            offset = crc_end
            break
        offset = crc_end
    if not saw_idat or not saw_iend or offset != len(value):
        raise SemanticSourceError("SAM3 mask PNG is incomplete or has trailing bytes")
    return {
        "encoding": "png",
        "semantic": "binary_mask",
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "byte_length": len(value),
        "chunk_types": chunk_types,
    }


class QwenGroundingClient:
    """Ground one textual target in immutable JPEG evidence."""

    def __init__(
        self,
        endpoint: str,
        *,
        token: str,
        model: str,
        timeout_s: float = 60.0,
    ) -> None:
        self.endpoint = _endpoint(endpoint)
        self.token = _required_text(token, "token")
        self.model = _required_text(model, "model")
        self.timeout_s = _positive_timeout(timeout_s)

    def ground(
        self,
        jpeg_bytes: bytes,
        *,
        prompt: str,
        image_width: int,
        image_height: int,
        top_k: int = 5,
    ) -> dict[str, Any]:
        jpeg = _jpeg_bytes(jpeg_bytes)
        width, height = _image_size(image_width, image_height)
        query = _required_text(prompt, "prompt")
        count = _positive_integer(top_k, "top_k", maximum=50)
        output_contract = '{"references":[{"bbox":[x1,y1,x2,y2]}]}'
        instruction = (
            f"Locate up to {count} visual references matching this target: {query}\n"
            "Order the most likely reference first. Coordinates are xyxy values "
            "normalized to the inclusive range 0..1000. Return strict JSON only, "
            f"with this exact shape: {output_contract}. Return an empty references "
            "array when the target is absent."
        )
        encoded = base64.b64encode(jpeg).decode("ascii")
        request_payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a visual grounding reader. Follow the exact JSON "
                        "output contract and do not add markdown or explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{encoded}",
                            },
                        },
                    ],
                },
            ],
            "temperature": 0.0,
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        response = _post_json(
            self.endpoint,
            request_payload,
            timeout_s=self.timeout_s,
            authorization=f"Bearer {self.token}",
        )
        try:
            references = _qwen_references(
                response,
                width=width,
                height=height,
                top_k=count,
            )
        except SemanticSourceError as exc:
            raise SemanticSourceError(
                str(exc),
                payload=response,
                status_code=200,
            ) from exc
        return {
            "schema": "demo_graph_lab.qwen_grounding.v1",
            "endpoint": self.endpoint,
            "model": self.model,
            "prompt": query,
            "top_k": count,
            "image": _image_metadata(jpeg, width, height),
            "references": references,
            "raw_response": response,
        }


class Sam3SegmentationClient:
    """Segment one pixel bbox in immutable JPEG evidence."""

    def __init__(
        self,
        endpoint: str,
        *,
        token: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.endpoint = _endpoint(endpoint)
        self.token = None if token is None else _required_text(token, "token")
        self.timeout_s = _positive_timeout(timeout_s)

    def segment(
        self,
        jpeg_bytes: bytes,
        *,
        bbox_pixel: Sequence[int],
        image_width: int,
        image_height: int,
    ) -> dict[str, Any]:
        jpeg = _jpeg_bytes(jpeg_bytes)
        width, height = _image_size(image_width, image_height)
        bbox = _pixel_bbox(bbox_pixel, width, height)
        request_payload = {
            "image": base64.b64encode(jpeg).decode("ascii"),
            "box_prompts": [bbox],
        }
        authorization = None if self.token is None else f"Bearer {self.token}"
        response = _post_json(
            self.endpoint,
            request_payload,
            timeout_s=self.timeout_s,
            authorization=authorization,
        )
        try:
            if "success" in response and response["success"] is not True:
                raise SemanticSourceError("SAM3 response reports failure")
            detections = response.get("detections")
            if not isinstance(detections, list) or len(detections) != 1:
                raise SemanticSourceError(
                    "SAM3 response must contain exactly one detection"
                )
            detection = detections[0]
            if not isinstance(detection, Mapping):
                raise SemanticSourceError("SAM3 detection must be an object")
            if any(
                not isinstance(key, str) or not key.strip()
                for key in detection
            ):
                raise SemanticSourceError(
                    "SAM3 detection keys must be non-empty strings"
                )
            encoded_mask = detection.get("mask")
            if not isinstance(encoded_mask, str) or not encoded_mask:
                raise SemanticSourceError(
                    "SAM3 detection.mask must be non-empty base64"
                )
            try:
                mask_bytes = base64.b64decode(encoded_mask, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise SemanticSourceError(
                    "SAM3 detection.mask is not valid base64"
                ) from exc
            mask_metadata = _png_metadata(mask_bytes)
            if (
                mask_metadata["width"] != width
                or mask_metadata["height"] != height
            ):
                raise SemanticSourceError(
                    "SAM3 mask dimensions do not match the frozen image"
                )
        except SemanticSourceError as exc:
            raise SemanticSourceError(
                str(exc),
                payload=response,
                status_code=200,
            ) from exc
        return {
            "schema": "demo_graph_lab.sam3_segmentation.v1",
            "endpoint": self.endpoint,
            "image": _image_metadata(jpeg, width, height),
            "bbox_pixel": bbox,
            "mask_bytes": mask_bytes,
            "mask": mask_metadata,
            "detection_metadata": {
                key: value for key, value in detection.items() if key != "mask"
            },
            "raw_response": response,
        }
