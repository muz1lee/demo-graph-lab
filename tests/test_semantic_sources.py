"""Offline tests for semantic readers over frozen JPEG bytes."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import struct
import subprocess
import sys
import urllib.parse
import zlib

import pytest

from demo_graph_lab.perception import semantic_sources
from demo_graph_lab.perception.semantic_sources import (
    QwenGroundingClient,
    Sam3SegmentationClient,
    SemanticSourceError,
)


_JPEG = b"\xff\xd8frozen-jpeg-evidence\xff\xd9"


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self.status = status
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _mask_png(width: int, height: int) -> bytes:
    rows = b"".join(
        b"\x00" + bytes(255 if (x + y) % 2 else 0 for x in range(width))
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def _qwen_response(references) -> dict:
    return {
        "id": "request-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"references": references}),
                }
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def test_module_imports_only_standard_library_image_support() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    statement = (
        "import sys; "
        "import demo_graph_lab.perception.semantic_sources; "
        "assert 'numpy' not in sys.modules; "
        "assert 'PIL' not in sys.modules; "
        "assert 'cv2' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", statement],
        env={"PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_qwen_posts_frozen_jpeg_and_returns_ranked_pixel_references(monkeypatch) -> None:
    seen = []
    response = _qwen_response([
        {"bbox": [100, 200, 500, 800]},
        {"bbox": [0, 0, 1000, 1000]},
        {"bbox": [700, 100, 900, 300]},
    ])

    def urlopen(request, *, timeout):
        payload = json.loads(request.data)
        seen.append((request, payload, timeout))
        return _Response(response)

    monkeypatch.setattr(semantic_sources, "_urlopen_no_redirect", urlopen)
    client = QwenGroundingClient(
        "https://qwen.example/v1/chat/completions",
        token="secret-token",
        model="qwen-vl",
        timeout_s=7.5,
    )

    result = client.ground(
        _JPEG,
        prompt="blue tube",
        image_width=1280,
        image_height=720,
        top_k=2,
    )

    request, payload, timeout = seen[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://qwen.example/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert timeout == 7.5
    assert payload["model"] == "qwen-vl"
    assert payload["temperature"] == 0.0
    assert payload["enable_thinking"] is False
    data_url = payload["messages"][1]["content"][1]["image_url"]["url"]
    assert base64.b64decode(data_url.split(",", 1)[1]) == _JPEG
    assert result["references"] == [
        {
            "rank": 1,
            "bbox_1000": [100.0, 200.0, 500.0, 800.0],
            "bbox_pixel": [128, 144, 640, 576],
        },
        {
            "rank": 2,
            "bbox_1000": [0.0, 0.0, 1000.0, 1000.0],
            "bbox_pixel": [0, 0, 1280, 720],
        },
    ]
    assert result["image"] == {
        "mime_type": "image/jpeg",
        "width": 1280,
        "height": 720,
        "byte_length": len(_JPEG),
    }
    assert result["raw_response"] == response
    assert "secret-token" not in repr(result)


@pytest.mark.parametrize(
    "references, message",
    [
        ([{"bbox": [-1, 0, 100, 100]}], r"\[0, 1000\]"),
        ([{"bbox": [100, 0, 100, 100]}], "positive area"),
        ([{"bbox": [0, 0, float("nan"), 100]}], "finite"),
        ([{"bbox": [0, 0, 100, 100], "score": 1}], "only bbox"),
    ],
)
def test_qwen_rejects_bad_grounding_contract_and_preserves_payload(
    monkeypatch,
    references,
    message,
) -> None:
    response = _qwen_response(references)
    monkeypatch.setattr(
        semantic_sources,
        "_urlopen_no_redirect",
        lambda *_args, **_kwargs: _Response(response),
    )

    with pytest.raises(SemanticSourceError, match=message) as caught:
        QwenGroundingClient(
            "http://qwen.example/v1/chat/completions",
            token="token",
            model="model",
        ).ground(
            _JPEG,
            prompt="target",
            image_width=100,
            image_height=100,
        )

    assert caught.value.payload == response
    assert caught.value.status_code == 200


def test_qwen_requires_strict_json_content(monkeypatch) -> None:
    response = _qwen_response([])
    response["choices"][0]["message"]["content"] = "```json\n{}\n```"
    monkeypatch.setattr(
        semantic_sources,
        "_urlopen_no_redirect",
        lambda *_args, **_kwargs: _Response(response),
    )

    with pytest.raises(SemanticSourceError, match="strict finite JSON") as caught:
        QwenGroundingClient(
            "http://qwen.example/v1/chat/completions",
            token="token",
            model="model",
        ).ground(
            _JPEG,
            prompt="target",
            image_width=100,
            image_height=100,
        )
    assert caught.value.payload == response


def test_sam3_posts_one_box_and_returns_validated_png_bytes(monkeypatch) -> None:
    png = _mask_png(4, 3)
    response = {
        "success": True,
        "detections": [
            {
                "mask": base64.b64encode(png).decode(),
                "score": 0.9,
                "label": "target",
            }
        ],
    }
    seen = []

    def urlopen(request, *, timeout):
        seen.append((request, json.loads(request.data), timeout))
        return _Response(response)

    monkeypatch.setattr(semantic_sources, "_urlopen_no_redirect", urlopen)
    client = Sam3SegmentationClient(
        "http://sam.example/segment",
        timeout_s=9.0,
    )

    result = client.segment(
        _JPEG,
        bbox_pixel=[1, 0, 4, 3],
        image_width=4,
        image_height=3,
    )

    request, payload, timeout = seen[0]
    assert request.get_method() == "POST"
    assert request.full_url == "http://sam.example/segment"
    assert request.get_header("Authorization") is None
    assert timeout == 9.0
    assert base64.b64decode(payload["image"]) == _JPEG
    assert payload["box_prompts"] == [[1, 0, 4, 3]]
    assert result["mask_bytes"] == png
    assert result["mask"] == {
        "encoding": "png",
        "semantic": "binary_mask",
        "width": 4,
        "height": 3,
        "bit_depth": 8,
        "color_type": 0,
        "byte_length": len(png),
        "chunk_types": ["IHDR", "IDAT", "IEND"],
    }
    assert result["detection_metadata"] == {"score": 0.9, "label": "target"}
    assert result["raw_response"] == response


@pytest.mark.parametrize(
    "response, message",
    [
        ({"success": False, "error": "offline", "detections": []}, "failure"),
        ({"success": True, "detections": []}, "exactly one"),
        ({"success": True, "detections": [{"mask": "not-base64"}]}, "base64"),
    ],
)
def test_sam3_rejects_bad_contract_and_preserves_payload(
    monkeypatch,
    response,
    message,
) -> None:
    monkeypatch.setattr(
        semantic_sources,
        "_urlopen_no_redirect",
        lambda *_args, **_kwargs: _Response(response),
    )

    with pytest.raises(SemanticSourceError, match=message) as caught:
        Sam3SegmentationClient("http://sam.example/segment").segment(
            _JPEG,
            bbox_pixel=[0, 0, 4, 3],
            image_width=4,
            image_height=3,
        )
    assert caught.value.payload == response


def test_sam3_rejects_mask_from_another_image(monkeypatch) -> None:
    response = {
        "detections": [
            {"mask": base64.b64encode(_mask_png(5, 3)).decode()}
        ]
    }
    monkeypatch.setattr(
        semantic_sources,
        "_urlopen_no_redirect",
        lambda *_args, **_kwargs: _Response(response),
    )

    with pytest.raises(SemanticSourceError, match="dimensions") as caught:
        Sam3SegmentationClient("http://sam.example/segment").segment(
            _JPEG,
            bbox_pixel=[0, 0, 4, 3],
            image_width=4,
            image_height=3,
        )
    assert caught.value.payload == response


def test_http_and_json_failures_preserve_available_evidence(monkeypatch) -> None:
    failure = {"error": "unavailable"}
    monkeypatch.setattr(
        semantic_sources,
        "_urlopen_no_redirect",
        lambda *_args, **_kwargs: _Response(failure, status=503),
    )
    client = Sam3SegmentationClient("http://sam.example/segment")
    with pytest.raises(SemanticSourceError, match="HTTP 503") as caught:
        client.segment(
            _JPEG,
            bbox_pixel=[0, 0, 4, 3],
            image_width=4,
            image_height=3,
        )
    assert caught.value.payload == failure
    assert caught.value.status_code == 503

    monkeypatch.setattr(
        semantic_sources,
        "_urlopen_no_redirect",
        lambda *_args, **_kwargs: _Response(b"not-json"),
    )
    with pytest.raises(SemanticSourceError, match="invalid finite JSON") as caught:
        client.segment(
            _JPEG,
            bbox_pixel=[0, 0, 4, 3],
            image_width=4,
            image_height=3,
        )
    assert caught.value.raw_body == b"not-json"


@pytest.mark.parametrize(
    "endpoint",
    [
        "sam.example/segment",
        "http://user:password@sam.example/segment",
        "http://sam.example/segment?mode=unsafe",
        "http://sam.example/segment#fragment",
        "http://sam.example/",
    ],
)
def test_clients_require_an_explicit_clean_endpoint(endpoint) -> None:
    with pytest.raises(SemanticSourceError, match="endpoint"):
        Sam3SegmentationClient(endpoint)


def test_semantic_http_opener_disables_proxy_and_redirect(monkeypatch) -> None:
    captured = []
    sentinel = object()

    class Opener:
        def open(self, request, *, timeout):
            captured.append((request, timeout))
            return sentinel

    def build_opener(*handlers):
        captured.extend(handlers)
        return Opener()

    monkeypatch.setattr(
        semantic_sources.urllib.request,
        "build_opener",
        build_opener,
    )
    request = semantic_sources.urllib.request.Request(
        "http://sam.example/segment"
    )

    assert semantic_sources._urlopen_no_redirect(request, timeout=3.0) is sentinel
    proxy = next(
        item for item in captured
        if isinstance(item, semantic_sources.urllib.request.ProxyHandler)
    )
    redirect = next(
        item for item in captured
        if isinstance(item, semantic_sources._NoRedirectHandler)
    )
    assert proxy.proxies == {}
    assert redirect.redirect_request(None, None, 307, None, {}, None) is None
    assert captured[-1] == (request, 3.0)


def test_semantic_source_has_no_runtime_or_motion_dependencies() -> None:
    source = Path(semantic_sources.__file__).read_text(encoding="utf-8")
    forbidden = (
        "sim.camera",
        "Pipeline" + "Client",
        "robot" + "_api",
        "common" + ".llm",
        "requests",
        "numpy",
        "cv2",
        "PIL",
    )

    assert all(token not in source for token in forbidden)
