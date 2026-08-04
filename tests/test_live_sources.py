"""Offline tests for the narrow live perception readers."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import types
import urllib.parse

import pytest

from demo_graph_lab.perception import live_sources
from demo_graph_lab.perception.live_sources import (
    GraspNetReadClient,
    LiveSourceError,
    ReadOnlyProprioClient,
    capture_head,
    depth_to_point_cloud,
    load_head_intrinsics,
)


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


def _intrinsics() -> dict:
    return {
        "width": 3,
        "height": 2,
        "fx": 1.0,
        "fy": 1.0,
        "cx": 1.0,
        "cy": 0.0,
        "baseline": 0.08,
    }


def _predict_request() -> dict:
    return {
        "image_path": None,
        "depth_path": None,
        "mask_path": None,
        "point_cloud_path": "/records/pointcloud.npz",
        "object_hint": None,
        "frame_id": "obs-head-0001",
        "coordinate_frame": "camera_head_optical",
        "camera_intrinsics": _intrinsics(),
        "extra": {"max_grasps": 20},
    }


def _health(**updates) -> dict:
    value = {
        "ok": True,
        "schema": "kw_independent.graspnet.health.v1",
        "backend": "graspnet_baseline",
        "backend_ready": True,
        "backend_error": None,
        "source_policy": "service_health_not_task_evidence",
    }
    value.update(updates)
    return value


def _predict_response(request: dict, **updates) -> dict:
    value = {
        "ok": True,
        "schema": "kw_independent.graspnet.raw_response.v1",
        "backend": "graspnet_baseline",
        "checkpoint_path": "/weights/checkpoint.tar",
        "checkpoint_epoch": 10,
        "coordinate_frame": "camera_head_optical",
        "grasps": [],
        "input_reference": request,
    }
    value.update(updates)
    return value


def test_module_import_does_not_load_optional_runtime_packages() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    statement = (
        "import sys; "
        "import demo_graph_lab.perception.live_sources; "
        "assert 'numpy' not in sys.modules; "
        "assert 'sim.camera.capture_bridge' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", statement],
        env={"PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_capture_head_uses_fixed_namespace_and_returns_raw_snapshot(monkeypatch) -> None:
    np = pytest.importorskip("numpy")
    calls = []
    snapshot = types.SimpleNamespace(
        frame_id=17,
        left_bgr=np.zeros((2, 3, 3), dtype=np.uint8),
        right_bgr=np.zeros((2, 3, 3), dtype=np.uint8),
        depth_m=np.ones((2, 3), dtype=np.float32),
        timestamp_s=1.25,
    )

    def request(namespace, *, socket_path, timeout):
        calls.append((namespace, socket_path, timeout))
        return snapshot

    sim = types.ModuleType("sim")
    sim.__path__ = []
    camera = types.ModuleType("sim.camera")
    camera.__path__ = []
    bridge = types.ModuleType("sim.camera.capture_bridge")
    bridge.request_stereo_snapshot = request
    monkeypatch.setitem(sys.modules, "sim", sim)
    monkeypatch.setitem(sys.modules, "sim.camera", camera)
    monkeypatch.setitem(sys.modules, "sim.camera.capture_bridge", bridge)

    result = capture_head(socket_path="/tmp/camera.sock", timeout_s=2.5)

    assert result is snapshot
    assert calls == [("head", "/tmp/camera.sock", 2.5)]


def test_capture_head_rejects_missing_or_malformed_snapshot(monkeypatch) -> None:
    np = pytest.importorskip("numpy")
    results = iter([
        None,
        types.SimpleNamespace(
            frame_id=1,
            left_bgr=np.zeros((2, 3, 3), dtype=np.uint8),
            right_bgr=np.zeros((2, 3, 3), dtype=np.uint8),
            depth_m=np.ones((2, 2), dtype=np.float32),
            timestamp_s=1.0,
        ),
    ])
    bridge = types.ModuleType("sim.camera.capture_bridge")
    bridge.request_stereo_snapshot = lambda *_args, **_kwargs: next(results)
    sim = types.ModuleType("sim")
    sim.__path__ = []
    camera = types.ModuleType("sim.camera")
    camera.__path__ = []
    monkeypatch.setitem(sys.modules, "sim", sim)
    monkeypatch.setitem(sys.modules, "sim.camera", camera)
    monkeypatch.setitem(sys.modules, "sim.camera.capture_bridge", bridge)

    with pytest.raises(LiveSourceError, match="no snapshot"):
        capture_head()
    with pytest.raises(LiveSourceError, match="depth_m"):
        capture_head()


def test_head_intrinsics_and_meter_depth_project_to_optical_xyz(tmp_path) -> None:
    np = pytest.importorskip("numpy")
    path = tmp_path / "intrinsics.json"
    path.write_text(json.dumps({
        "head": _intrinsics(),
        "left_hand": {"ignored": True},
    }))

    intrinsics = load_head_intrinsics(path)
    depth = np.array([
        [1.0, np.nan, 2.0],
        [0.0, 1.0, np.inf],
    ], dtype=np.float32)
    points = depth_to_point_cloud(depth, intrinsics)

    assert intrinsics == _intrinsics()
    assert points.dtype == np.float32
    assert points.shape == (3, 3)
    np.testing.assert_allclose(points, np.array([
        [-1.0, 0.0, 1.0],
        [2.0, 0.0, 2.0],
        [0.0, 1.0, 1.0],
    ], dtype=np.float32))


def test_intrinsics_and_depth_reject_schema_drift_and_wrong_units(tmp_path) -> None:
    np = pytest.importorskip("numpy")
    path = tmp_path / "intrinsics.json"
    invalid = _intrinsics()
    invalid["model"] = "pinhole"
    path.write_text(json.dumps({"head": invalid}))
    with pytest.raises(LiveSourceError, match="extra"):
        load_head_intrinsics(path)

    with pytest.raises(LiveSourceError, match="floating-point"):
        depth_to_point_cloud(np.ones((2, 3), dtype=np.uint16), _intrinsics())
    with pytest.raises(LiveSourceError, match="shape"):
        depth_to_point_cloud(np.ones((1, 3), dtype=np.float32), _intrinsics())


def test_proprio_client_makes_only_the_four_fixed_info_reads(monkeypatch) -> None:
    seen = []

    def urlopen(request, *, timeout):
        parsed = urllib.parse.urlsplit(request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        kwargs = json.loads(query["kwargs"][0])
        name = query["name"][0]
        arm_id = kwargs["arm_id"]
        seen.append((request.get_method(), parsed.path, query["action"][0], name, arm_id, timeout))
        if name == "get_qpos":
            result = json.dumps([arm_id + index / 10 for index in range(7)])
        else:
            result = [float(arm_id), 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]
        return _Response({"ok": True, "result": result})

    monkeypatch.setattr(live_sources, "_urlopen_no_redirect", urlopen)

    client = ReadOnlyProprioClient("http://sim.example:8000", timeout_s=4.0)
    record = client.read()

    assert not hasattr(client, "call")
    assert seen == [
        ("GET", "/run", "info", "get_qpos", 0, 4.0),
        ("GET", "/run", "info", "get_qpos", 1, 4.0),
        ("GET", "/run", "info", "get_xquat", 0, 4.0),
        ("GET", "/run", "info", "get_xquat", 1, 4.0),
    ]
    assert record["joint_positions"] == [
        *[index / 10 for index in range(7)],
        *[1 + index / 10 for index in range(7)],
    ]
    assert record["gripper_positions"] == []
    assert record["end_effector_frame"] == "robot_base"
    assert set(record["end_effector_poses"]) == {"left", "right"}
    assert record["calls"] == [
        {"endpoint": "/run", "action": "info", "name": name, "kwargs": {"arm_id": arm_id}}
        for name in ("get_qpos", "get_xquat")
        for arm_id in (0, 1)
    ]


def test_proprio_client_rejects_failed_or_malformed_read(monkeypatch) -> None:
    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda *_args, **_kwargs: _Response({"ok": False, "error": "offline"}),
    )
    with pytest.raises(LiveSourceError) as caught:
        ReadOnlyProprioClient("http://sim.example:8000").read()
    assert caught.value.payload == {"ok": False, "error": "offline"}


def test_graspnet_health_accepts_only_ready_real_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda request, **_kwargs: _Response(_health()),
    )
    client = GraspNetReadClient("http://127.0.0.1:8088")

    assert client.health() == _health()

    fixture = _health(backend="fixture")
    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda request, **_kwargs: _Response(fixture),
    )
    with pytest.raises(LiveSourceError, match="fixture") as caught:
        client.health()
    assert caught.value.payload == fixture
    assert caught.value.status_code == 200


def test_graspnet_health_preserves_not_ready_payload(monkeypatch) -> None:
    failed = _health(
        ok=False,
        backend_ready=False,
        backend_error="extension missing",
    )
    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda request, **_kwargs: _Response(failed),
    )

    with pytest.raises(LiveSourceError, match="not ready") as caught:
        GraspNetReadClient("http://127.0.0.1:8088").health()

    assert caught.value.payload == failed


def test_graspnet_predict_posts_exact_request_and_checks_echo(monkeypatch) -> None:
    expected = _predict_request()
    seen = []

    def urlopen(request, *, timeout):
        body = json.loads(request.data)
        seen.append((request.get_method(), request.full_url, body, timeout))
        return _Response(_predict_response(body))

    monkeypatch.setattr(live_sources, "_urlopen_no_redirect", urlopen)
    client = GraspNetReadClient("http://127.0.0.1:8088", timeout_s=12.0)

    result = client.predict(expected)

    assert result == _predict_response(expected)
    assert seen == [
        ("POST", "http://127.0.0.1:8088/predict", expected, 12.0)
    ]


def test_graspnet_predict_preserves_unsuccessful_json(monkeypatch) -> None:
    failed = {
        "ok": False,
        "schema": "kw_independent.graspnet.raw_response.v1",
        "error": "out of memory",
        "input_reference": _predict_request(),
        "source_policy": "prediction_error_not_route_decision",
    }
    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda request, **_kwargs: _Response(failed),
    )

    with pytest.raises(LiveSourceError, match="prediction failed") as caught:
        GraspNetReadClient("http://127.0.0.1:8088").predict(
            _predict_request()
        )

    assert caught.value.payload == failed
    assert caught.value.status_code == 200


@pytest.mark.parametrize(
    "updates, message",
    [
        ({"backend": "fixture"}, "fixture"),
        ({"schema": "unknown"}, "schema"),
        ({"coordinate_frame": "robot_base"}, "coordinate_frame"),
        ({"input_reference": {**_predict_request(), "frame_id": "other"}}, "echo"),
    ],
)
def test_graspnet_predict_rejects_contract_drift_with_payload(
    monkeypatch,
    updates,
    message,
) -> None:
    payload = _predict_response(_predict_request(), **updates)
    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda request, **_kwargs: _Response(payload),
    )

    with pytest.raises(LiveSourceError, match=message) as caught:
        GraspNetReadClient("http://127.0.0.1:8088").predict(
            _predict_request()
        )
    assert caught.value.payload == payload


def test_non_200_and_invalid_json_are_explicit(monkeypatch) -> None:
    failure = {"detail": "unavailable"}
    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda request, **_kwargs: _Response(failure, status=503),
    )
    with pytest.raises(LiveSourceError, match="HTTP 503") as caught:
        GraspNetReadClient("http://127.0.0.1:8088").health()
    assert caught.value.payload == failure
    assert caught.value.status_code == 503

    monkeypatch.setattr(
        live_sources,
        "_urlopen_no_redirect",
        lambda request, **_kwargs: _Response(b"not-json"),
    )
    with pytest.raises(LiveSourceError, match="invalid JSON"):
        GraspNetReadClient("http://127.0.0.1:8088").health()


def test_graspnet_client_rejects_non_loopback_origin() -> None:
    with pytest.raises(LiveSourceError, match="loopback"):
        GraspNetReadClient("http://grasp.example:8088")


def test_live_http_opener_disables_proxy_and_redirect(monkeypatch) -> None:
    captured = []
    sentinel = object()

    class Opener:
        def open(self, request, *, timeout):
            captured.append((request, timeout))
            return sentinel

    def build_opener(*handlers):
        captured.extend(handlers)
        return Opener()

    monkeypatch.setattr(live_sources.urllib.request, "build_opener", build_opener)
    request = live_sources.urllib.request.Request("http://127.0.0.1:8092/health")

    assert live_sources._urlopen_no_redirect(request, timeout=3.0) is sentinel
    proxy = next(
        item for item in captured
        if isinstance(item, live_sources.urllib.request.ProxyHandler)
    )
    redirect = next(
        item for item in captured
        if isinstance(item, live_sources._NoRedirectHandler)
    )
    assert proxy.proxies == {}
    assert redirect.redirect_request(None, None, 307, None, {}, None) is None
    assert captured[-1] == (request, 3.0)


def test_live_source_module_has_no_privileged_or_motion_dependencies() -> None:
    source = Path(live_sources.__file__).read_text(encoding="utf-8")
    forbidden = (
        "/st" + "ate",
        "re" + "set",
        "con" + "trol",
        "robot" + "_api",
        "Pipeline" + "Client",
        "common" + ".llm",
    )

    assert all(token not in source for token in forbidden)
