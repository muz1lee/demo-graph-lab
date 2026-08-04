"""Offline tests for the explicit read-only planning-record workflow."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from demo_graph_lab import cli
from demo_graph_lab.common import artifacts
from demo_graph_lab.execution.planning_record import (
    capture_record,
    plan_record,
)


def _write(path: Path, value) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _graph(path: Path) -> Path:
    return _write(path, {
        "task": "insert_tubes",
        "stages": [{
            "index": 0,
            "name": "grasp_tube",
            "stage_objects": {"manipulated": "tube_left", "target": None},
            "holes": [{
                "name": "tube_grasp_pose",
                "type": "pose_se3",
                "frame": "robot_base",
                "solver_hint": "candidate grasp pose",
                "resolver": "grasp_candidate",
                "anchor": {"object_id": "tube_left", "part": "whole"},
            }],
        }],
    })


def _objects(path: Path) -> Path:
    return _write(path, [{
        "id": "tube_left",
        "category": "test tube",
        "distinguishers": "white tube with orange cap on the left",
        "trace_aliases": ["left tube"],
        "first_seen_frame": 0,
    }])


def _intrinsics(path: Path) -> Path:
    return _write(path, {
        "head": {
            "resolution": [3, 2],
            "fx": 2.0,
            "fy": 2.0,
            "cx": 1.0,
            "cy": 0.5,
        }
    })


def _plan(tmp_path: Path) -> Path:
    record_dir = tmp_path / "record"
    plan_record(
        graph_path=_graph(tmp_path / "graph.json"),
        objects_path=_objects(tmp_path / "objects.json"),
        stage_index=0,
        record_dir=record_dir,
        intrinsics_path=_intrinsics(tmp_path / "intrinsics.json"),
        hole_name=None,
        pipeline_url="http://127.0.0.1:8000",
        graspnet_url="http://127.0.0.1:8092",
        qwen_url="https://qwen.example/v1/chat/completions",
        qwen_model="qwen-vl",
        sam3_url="https://sam3.example/segment",
        camera_socket="/tmp/fake-camera.sock",
        timeout_s=2.0,
        max_grasps=4,
    )
    return record_dir


class FakeSources:
    calls = []

    @staticmethod
    def load_head_intrinsics(path):
        FakeSources.calls.append(("load_head_intrinsics", path))
        return {"width": 3, "height": 2, "fx": 2.0, "fy": 2.0, "cx": 1.0, "cy": 0.5}

    @staticmethod
    def capture_head(*, socket_path, timeout_s):
        FakeSources.calls.append(("capture_head", socket_path, timeout_s))
        return SimpleNamespace(
            frame_id=11,
            timestamp_s=12.5,
            left_bgr=np.zeros((2, 3, 3), dtype=np.uint8),
            right_bgr=np.ones((2, 3, 3), dtype=np.uint8),
            depth_m=np.array([[1.0, 2.0, np.nan], [0.0, 3.0, -1.0]], dtype=np.float32),
        )

    @staticmethod
    def depth_to_point_cloud(depth_m, intrinsics):
        FakeSources.calls.append(("depth_to_point_cloud", intrinsics))
        return np.array([
            [-0.5, -0.25, 1.0],
            [0.0, -0.5, 2.0],
            [0.0, 0.75, 3.0],
        ], dtype=np.float32)

    class ReadOnlyProprioClient:
        def __init__(self, base_url, timeout_s):
            FakeSources.calls.append(("proprio_client", base_url, timeout_s))

        def read(self):
            calls = [
                {"interface": "pipeline_info", "name": "get_qpos", "arm_id": 0},
                {"interface": "pipeline_info", "name": "get_qpos", "arm_id": 1},
                {"interface": "pipeline_info", "name": "get_xquat", "arm_id": 0},
                {"interface": "pipeline_info", "name": "get_xquat", "arm_id": 1},
            ]
            return {
                "joint_positions": [0.0] * 14,
                "gripper_positions": [],
                "end_effector_frame": "robot_base",
                "end_effector_poses": {
                    "left": [0.4, 0.2, 0.8, 0.0, 0.0, 0.0, 1.0],
                    "right": [0.4, -0.2, 0.8, 0.0, 0.0, 0.0, 1.0],
                },
                "calls": calls,
            }

def test_plan_is_local_only_and_records_stop_boundary(tmp_path, monkeypatch) -> None:
    graph = _graph(tmp_path / "graph.json")
    objects = _objects(tmp_path / "objects.json")
    intrinsics = _intrinsics(tmp_path / "intrinsics.json")
    record_dir = tmp_path / "record"

    def forbidden_env_load():
        raise AssertionError("planning-record must not load backend environment")

    monkeypatch.setattr(artifacts, "load_env", forbidden_env_load)
    assert cli.main([
        "planning-record",
        "--record-dir", str(record_dir),
        "--graph", str(graph),
        "--objects", str(objects),
        "--intrinsics", str(intrinsics),
        "--qwen-url", "https://qwen.example/v1/chat/completions",
        "--qwen-model", "qwen-vl",
        "--sam3-url", "https://sam3.example/segment",
    ]) == 0

    manifest = json.loads((record_dir / "manifest.json").read_text())
    plan = json.loads((record_dir / "plan.json").read_text())
    assert manifest["status"] == "PLANNED"
    assert manifest["backend_model_enabled"] is False
    assert manifest["execution_enabled"] is False
    assert plan["stage"]["holes"][0]["frame"] == "robot_base"
    assert plan["perception_request"]["anchor"] == {
        "object_id": "tube_left",
        "part": "whole",
        "instance": None,
        "selection": None,
    }
    assert plan["perception_request"]["resolver"] == "grasp_candidate"
    assert "robot control" in plan["stops_before"]
    assert {item["code"] for item in manifest["normalization_blockers"]} >= {
        "camera_to_requested_frame_missing",
        "object_identity_unverified",
        "physical_hard_checks_missing",
    }


def test_live_steps_fail_before_loading_transport_without_opt_in(tmp_path) -> None:
    class Poison:
        def __getattr__(self, name):
            raise AssertionError(f"transport accessed: {name}")

    with pytest.raises(PermissionError, match="allow-live-read"):
        capture_record(tmp_path / "missing", source_module=Poison())


def test_capture_revalidates_plan_before_live_read(tmp_path) -> None:
    record_dir = _plan(tmp_path)
    plan = json.loads((record_dir / "plan.json").read_text())
    plan["perception_request"]["prompt"] = "drifted prompt"
    _write(record_dir / "plan.json", plan)
    FakeSources.calls = []

    with pytest.raises(ValueError, match="no longer matches"):
        capture_record(
            record_dir,
            allow_live_read=True,
            source_module=FakeSources,
        )

    manifest = json.loads((record_dir / "manifest.json").read_text())
    assert manifest["status"] == "PLANNED"
    assert FakeSources.calls == []
    assert not (record_dir / "observation.json").exists()


def test_capture_freezes_optical_observation_without_object_claims(tmp_path) -> None:
    FakeSources.calls = []
    record_dir = _plan(tmp_path)
    manifest = capture_record(
        record_dir,
        allow_live_read=True,
        source_module=FakeSources,
    )

    assert manifest["status"] == "OBSERVATION_RECORDED"
    observation = json.loads((record_dir / "observation.json").read_text())
    point_manifest = json.loads(
        (record_dir / "sensor/pointcloud_manifest.json").read_text()
    )
    projection = json.loads(
        (record_dir / "sensor/projection_manifest.json").read_text()
    )
    points = np.load(record_dir / "sensor/head_pointcloud_camera.npz")["points"]
    assert observation["frame"] == "camera_head_optical"
    assert observation["objects"] == []
    assert len(observation["robot_state"]["joint_positions"]) == 14
    assert point_manifest["unit"] == "meter"
    assert point_manifest["frame"] == "camera_head_optical"
    assert point_manifest["evidence_ref"] == str(
        (record_dir / "sensor/projection_manifest.json").resolve()
    )
    assert projection["valid_point_count"] == 3
    assert points.dtype == np.float32
    assert points.shape == (3, 3)
    assert not (record_dir / "candidates.json").exists()


def test_capture_preserves_structured_source_error(tmp_path) -> None:
    class CaptureFailure(RuntimeError):
        status_code = 503
        payload = {"ok": False, "error": "camera unavailable"}

    class FailingSources(FakeSources):
        @staticmethod
        def capture_head(*, socket_path, timeout_s):
            raise CaptureFailure("capture failed")

    record_dir = _plan(tmp_path)
    with pytest.raises(CaptureFailure, match="capture failed"):
        capture_record(
            record_dir,
            allow_live_read=True,
            source_module=FailingSources,
        )

    manifest = json.loads((record_dir / "manifest.json").read_text())
    call = json.loads((record_dir / "sensor/call.json").read_text())
    payload = json.loads((record_dir / "sensor/error_payload.json").read_text())
    assert manifest["status"] == "PLANNED"
    assert manifest["last_error"]["step"] == "capture"
    assert manifest["artifacts"]["capture_error_payload"] == (
        "sensor/error_payload.json"
    )
    assert call["error"]["http_status"] == 503
    assert payload["error"] == "camera unavailable"


def test_planning_record_has_no_control_or_backend_imports() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src/demo_graph_lab/execution/planning_record.py"
    ).read_text()
    for forbidden in (
        "common.llm",
        "oracle_runtime",
        "robot_api",
        "PipelineClient",
        ".ctrl(",
        "'/state'",
        '"/state"',
        ".reset(",
    ):
        assert forbidden not in source
