"""Freeze one read-only real observation for object-level perception.

The workflow is deliberately split into explicit steps.  Planning performs no
I/O outside the local artifact directory.  Live sensor/model reads require an
explicit opt-in and the workflow stops before planning or robot control.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from typing import Any

from ..perception.adapters import observation_from_record
from ..graph.validate import validate_live_hole_contract


_MANIFEST_SCHEMA = "demo_graph_lab.planning_record_manifest.v1"
_PLAN_SCHEMA = "demo_graph_lab.planning_record_plan.v1"
_PROJECTION_SCHEMA = "demo_graph_lab.head_depth_projection.v1"
_SOURCE_KIND = "recorded_real_readonly"
_OPTICAL_FRAME = "camera_head_optical"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: str | Path) -> Any:
    def reject_constant(value: str):
        raise ValueError(f"non-finite JSON number {value!r} is not allowed")

    return json.loads(
        Path(path).read_text("utf-8"),
        parse_constant=reject_constant,
    )


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(target)


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _positive_number(value: Any, path: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{path} must be a finite positive number")
    return float(value)


def _positive_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


def _load_manifest(record_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(record_dir).resolve()
    value = _read_json(root / "manifest.json")
    if not isinstance(value, dict) or value.get("schema") != _MANIFEST_SCHEMA:
        raise ValueError("record directory has no supported planning manifest")
    if value.get("source_kind") != _SOURCE_KIND:
        raise ValueError("record directory is not a recorded-real read-only run")
    if value.get("backend_model_enabled") is not False:
        raise ValueError("planning record must keep backend_model_enabled=false")
    if value.get("execution_enabled") is not False:
        raise ValueError("planning record must keep execution_enabled=false")
    return root, value


def _one_stage(graph: Mapping[str, Any], stage_index: int) -> dict[str, Any]:
    if isinstance(stage_index, bool) or not isinstance(stage_index, int):
        raise ValueError("stage_index must be an integer")
    stages = graph.get("stages")
    if not isinstance(stages, list):
        raise ValueError("graph.stages must be a list")
    matches = [
        stage for stage in stages
        if isinstance(stage, dict) and stage.get("index") == stage_index
    ]
    if len(matches) != 1:
        raise ValueError(
            f"graph must contain exactly one stage with index {stage_index}"
        )
    stage = matches[0]
    name = _required_string(stage.get("name"), "graph stage name")
    holes = stage.get("holes")
    if not isinstance(holes, list):
        raise ValueError(f"graph stage {name!r} holes must be a list")
    normalized_holes = []
    for index, hole in enumerate(holes):
        if not isinstance(hole, Mapping):
            raise ValueError(f"graph stage hole {index} must be an object")
        normalized = {
            "name": _required_string(hole.get("name"), f"stage.holes[{index}].name"),
            "type": _required_string(hole.get("type"), f"stage.holes[{index}].type"),
            "frame": _required_string(hole.get("frame"), f"stage.holes[{index}].frame"),
            "solver_hint": _required_string(
                hole.get("solver_hint"), f"stage.holes[{index}].solver_hint"
            ),
        }
        if "resolver" in hole:
            normalized["resolver"] = hole["resolver"]
        if "anchor" in hole:
            normalized["anchor"] = dict(hole["anchor"])
        normalized_holes.append(normalized)
    stage_objects = stage.get("stage_objects")
    if not isinstance(stage_objects, Mapping):
        raise ValueError(f"graph stage {name!r} stage_objects must be an object")
    return {
        "index": stage_index,
        "name": name,
        "stage_objects": dict(stage_objects),
        "holes": normalized_holes,
    }


def _registry_objects(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("objects registry must be a non-empty array")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"objects[{index}] must be an object")
        object_id = _required_string(item.get("id"), f"objects[{index}].id")
        if object_id in result:
            raise ValueError(f"duplicate registry object id: {object_id!r}")
        result[object_id] = dict(item)
    return result


def _normalized_anchor(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    allowed = {"object_id", "part", "instance", "selection"}
    extra = sorted(set(value) - allowed)
    if extra:
        raise ValueError(f"{path} has unknown fields: {extra}")
    object_id = _required_string(value.get("object_id"), f"{path}.object_id")
    part = _required_string(value.get("part"), f"{path}.part")
    anchor = {
        "object_id": object_id,
        "part": part,
        "instance": value.get("instance"),
        "selection": value.get("selection"),
    }
    for field in ("instance", "selection"):
        if anchor[field] is not None:
            anchor[field] = _required_string(anchor[field], f"{path}.{field}")
    return anchor


def _perception_request(
    stage: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
    hole_name: str | None,
) -> dict[str, Any]:
    geometric = [
        hole for hole in stage["holes"]
        if hole.get("type") in {"pose_se3", "axis_3d", "point_3d"}
    ]
    if hole_name is None:
        grasp_holes = [
            hole for hole in geometric
            if hole.get("resolver") == "grasp_candidate"
        ]
        if len(grasp_holes) != 1:
            raise ValueError(
                "--hole is required unless the stage has exactly one "
                "grasp_candidate hole"
            )
        hole = grasp_holes[0]
    else:
        requested = _required_string(hole_name, "hole_name")
        matches = [hole for hole in geometric if hole.get("name") == requested]
        if len(matches) != 1:
            raise ValueError(f"stage has no unique geometric hole {requested!r}")
        hole = matches[0]

    resolver = _required_string(hole.get("resolver"), "hole.resolver")
    if resolver == "motion_derived":
        raise ValueError(
            f"hole {hole['name']!r} is derived from execution state, not perception"
        )
    anchor = _normalized_anchor(hole.get("anchor"), "hole.anchor")
    object_spec = registry.get(anchor["object_id"])
    if object_spec is None:
        raise ValueError(
            f"hole anchor object {anchor['object_id']!r} is absent from registry"
        )
    category = _required_string(
        object_spec.get("category"), f"objects[{anchor['object_id']}].category"
    )
    distinguishers = _required_string(
        object_spec.get("distinguishers"),
        f"objects[{anchor['object_id']}].distinguishers",
    )
    if anchor["part"] == "whole":
        target = f"the complete visible {category} instance"
    elif anchor["selection"] is not None:
        target = (
            f"the {anchor['selection'].replace('_', ' ')} of the {category}"
        )
    else:
        qualifier = (
            f"{anchor['instance'].replace('_', ' ')} "
            if anchor["instance"] is not None else ""
        )
        target = f"the {qualifier}{anchor['part'].replace('_', ' ')} of the {category}"
    prompt = (
        "Return strict JSON for exactly the visible region matching this graph "
        f"anchor: {target}. Distinguishing evidence: {distinguishers}. "
        "Use {\"references\":[{\"bbox\":[x1,y1,x2,y2]}]} with coordinates "
        "normalized to 0..1000. Return an empty references array when absent; "
        "return every plausible box when ambiguous."
    )
    return {
        "hole_name": hole["name"],
        "resolver": resolver,
        "anchor": anchor,
        "prompt": prompt,
    }


def _revalidate_record_plan(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]:
    """Rebuild the selected stage/request from current graph and registry files."""

    plan = _read_json(root / "plan.json")
    if not isinstance(plan, dict) or plan.get("schema") != _PLAN_SCHEMA:
        raise ValueError("record plan is invalid")
    graph_ref = Path(
        _required_string(plan.get("graph_ref"), "plan.graph_ref")
    ).resolve()
    objects_ref = Path(
        _required_string(plan.get("objects_ref"), "plan.objects_ref")
    ).resolve()
    if not graph_ref.is_file() or not objects_ref.is_file():
        raise FileNotFoundError("plan graph_ref or objects_ref no longer exists")
    graph = _read_json(graph_ref)
    if not isinstance(graph, Mapping):
        raise ValueError("plan graph_ref must contain an object")
    registry = _registry_objects(_read_json(objects_ref))
    live_errors = validate_live_hole_contract(graph, set(registry))
    if live_errors:
        raise ValueError(
            "plan graph is no longer live-ready: " + "; ".join(live_errors[:5])
        )

    embedded_stage = plan.get("stage")
    if not isinstance(embedded_stage, Mapping):
        raise ValueError("plan.stage must be an object")
    stage = _one_stage(graph, embedded_stage.get("index"))
    if dict(embedded_stage) != stage:
        raise ValueError("plan.stage no longer matches graph_ref")
    if plan.get("graph_task") != graph.get("task"):
        raise ValueError("plan.graph_task no longer matches graph_ref")

    request = plan.get("perception_request")
    if not isinstance(request, Mapping):
        raise ValueError("plan.perception_request must be an object")
    hole_name = _required_string(
        request.get("hole_name"), "plan.perception_request.hole_name"
    )
    expected_request = _perception_request(stage, registry, hole_name)
    if dict(request) != expected_request:
        raise ValueError("plan.perception_request no longer matches graph/objects")
    config = plan.get("config")
    if not isinstance(config, Mapping):
        raise ValueError("plan.config must be an object")
    return plan, expected_request, config


def plan_record(
    *,
    graph_path: str | Path,
    objects_path: str | Path,
    stage_index: int,
    record_dir: str | Path,
    intrinsics_path: str | Path,
    hole_name: str | None,
    pipeline_url: str,
    graspnet_url: str,
    qwen_url: str,
    qwen_model: str,
    sam3_url: str,
    camera_socket: str | Path,
    timeout_s: float = 10.0,
    max_grasps: int = 20,
    min_object_points: int = 64,
) -> dict[str, Any]:
    """Create a local-only plan.  This function imports no live transport."""

    graph_ref = Path(graph_path).resolve()
    objects_ref = Path(objects_path).resolve()
    intrinsics_ref = Path(intrinsics_path).resolve()
    socket_ref = Path(camera_socket)
    if not graph_ref.is_file():
        raise FileNotFoundError(f"graph does not exist: {graph_ref}")
    if not objects_ref.is_file():
        raise FileNotFoundError(f"objects registry does not exist: {objects_ref}")
    if not intrinsics_ref.is_file():
        raise FileNotFoundError(f"intrinsics do not exist: {intrinsics_ref}")
    graph = _read_json(graph_ref)
    if not isinstance(graph, Mapping):
        raise ValueError("graph root must be an object")
    registry = _registry_objects(_read_json(objects_ref))
    live_errors = validate_live_hole_contract(graph, set(registry))
    if live_errors:
        raise ValueError(
            "graph is not ready for live perception: " + "; ".join(live_errors[:5])
        )
    stage = _one_stage(graph, stage_index)
    perception_request = _perception_request(stage, registry, hole_name)
    timeout = _positive_number(timeout_s, "timeout_s")
    count = _positive_integer(max_grasps, "max_grasps")
    minimum_points = _positive_integer(min_object_points, "min_object_points")
    pipeline = _required_string(pipeline_url, "pipeline_url").rstrip("/")
    graspnet = _required_string(graspnet_url, "graspnet_url").rstrip("/")
    qwen = _required_string(qwen_url, "qwen_url")
    qwen_model = _required_string(qwen_model, "qwen_model")
    sam3 = _required_string(sam3_url, "sam3_url")
    camera = _required_string(str(socket_ref), "camera_socket")

    root = Path(record_dir).resolve()
    if root.exists():
        raise FileExistsError(f"record directory already exists: {root}")
    root.mkdir(parents=True)

    requested_frames = sorted({hole["frame"] for hole in stage["holes"]})
    blockers = [
        {
            "code": "scene_identity_unverified",
            "detail": (
                "The read-only path does not inspect privileged simulator state; "
                "task/scene identity must be established separately."
            ),
        },
        {
            "code": "camera_to_requested_frame_missing",
            "detail": (
                f"Raw geometry is {_OPTICAL_FRAME}; requested hole frames are "
                f"{requested_frames or ['none']}. A measured lift-aware transform "
                "has not been recorded."
            ),
        },
        {
            "code": "object_identity_unverified",
            "detail": (
                "Qwen/SAM3 can propose a region for the requested graph anchor, "
                "but this one-anchor record does not independently verify instance "
                "identity. The assignment remains MODEL_PROPOSED."
            ),
        },
        {
            "code": "grasp_to_runtime_ee_calibration_missing",
            "detail": "The GraspNet grasp frame is not yet calibrated to runtime_ee.",
        },
        {
            "code": "physical_hard_checks_missing",
            "detail": (
                "Reachability, collision-free, and gripper-width checkers are not "
                "connected on this path."
            ),
        },
    ]
    plan = {
        "schema": _PLAN_SCHEMA,
        "graph_ref": str(graph_ref),
        "objects_ref": str(objects_ref),
        "graph_task": graph.get("task"),
        "stage": stage,
        "perception_request": perception_request,
        "config": {
            "intrinsics_path": str(intrinsics_ref),
            "pipeline_url": pipeline,
            "graspnet_url": graspnet,
            "qwen_url": qwen,
            "qwen_model": qwen_model,
            "sam3_url": sam3,
            "camera_socket": camera,
            "timeout_s": timeout,
            "max_grasps": count,
            "min_object_points": minimum_points,
            "capture_namespace": "head",
            "output_frame": _OPTICAL_FRAME,
        },
        "steps": [
            {
                "name": "capture",
                "requires_allow_live_read": True,
                "calls": [
                    "one head stereo snapshot",
                    "get_qpos arm 0 and 1",
                    "get_xquat arm 0 and 1",
                ],
                "known_side_effects": [
                    "one synchronous simulator render",
                    "camera snapshot cache update",
                    "head capture frame_id increment",
                ],
            },
            {
                "name": "ground",
                "requires_allow_model_read": True,
                "calls": ["Qwen grounding POST"],
            },
            {
                "name": "segment",
                "requires_allow_model_read": True,
                "calls": ["SAM3 segmentation POST"],
            },
            {
                "name": "project",
                "requires_allow_live_read": False,
                "calls": [],
            },
            {
                "name": "predict",
                "condition": "resolver == grasp_candidate",
                "requires_allow_live_read": True,
                "calls": ["GraspNet GET /health", "GraspNet POST /predict"],
            },
        ],
        "stops_before": [
            "candidate normalization",
            "candidate selection",
            "motion planning",
            "robot control",
        ],
    }
    now = _utc_now()
    manifest = {
        "schema": _MANIFEST_SCHEMA,
        "status": "PLANNED",
        "source_kind": _SOURCE_KIND,
        "backend_model_enabled": False,
        "execution_enabled": False,
        "created_at": now,
        "updated_at": now,
        "observation_id": None,
        "artifacts": {"plan": "plan.json"},
        "normalization_blockers": blockers,
        "last_error": None,
    }
    _write_json(root / "plan.json", plan)
    _write_json(root / "manifest.json", manifest)
    return manifest


def _record_error(
    root: Path,
    manifest: dict[str, Any],
    *,
    step: str,
    error: Exception,
) -> None:
    manifest["updated_at"] = _utc_now()
    manifest["last_error"] = {
        "step": step,
        "type": type(error).__name__,
        "message": str(error),
    }
    _write_json(root / "manifest.json", manifest)


def capture_record(
    record_dir: str | Path,
    *,
    allow_live_read: bool = False,
    source_module=None,
) -> dict[str, Any]:
    """Freeze one head RGB-D frame and whitelisted robot-owned measurements."""

    if allow_live_read is not True:
        raise PermissionError("capture requires --allow-live-read")
    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") != "PLANNED":
        raise ValueError("capture requires manifest status PLANNED")
    if (root / "sensor").exists() or (root / "proprioception.json").exists():
        raise FileExistsError("capture artifacts already exist; use a new record dir")
    _, _, config = _revalidate_record_plan(root)

    if source_module is None:
        from ..perception import live_sources as source_module
    import numpy as np

    sensor_dir = root / "sensor"
    calibration_dir = root / "calibration"
    started = time.monotonic()
    call_record = {
        "step": "capture",
        "started_at": _utc_now(),
        "allow_live_read": True,
        "calls": [],
        "status": "running",
    }
    try:
        sensor_dir.mkdir()
        calibration_dir.mkdir()
        intrinsics = source_module.load_head_intrinsics(config["intrinsics_path"])
        snapshot = source_module.capture_head(
            socket_path=config["camera_socket"],
            timeout_s=config["timeout_s"],
        )
        call_record["calls"].append({
            "interface": "head_capture_bridge",
            "namespace": "head",
            "source_frame_id": snapshot.frame_id,
            "source_timestamp_s": snapshot.timestamp_s,
            "side_effects": [
                "one synchronous simulator render",
                "camera snapshot cache update",
                "head capture frame_id increment",
            ],
        })
        left = np.asarray(snapshot.left_bgr)
        right = np.asarray(snapshot.right_bgr)
        depth = np.asarray(snapshot.depth_m)
        expected_shape = (intrinsics["height"], intrinsics["width"])
        if left.shape != (*expected_shape, 3):
            raise ValueError(
                f"head left image shape {left.shape} != {(*expected_shape, 3)}"
            )
        if right.shape != left.shape:
            raise ValueError("head stereo image shapes do not match")
        if depth.shape != expected_shape:
            raise ValueError(
                f"head depth shape {depth.shape} != {expected_shape}"
            )
        if left.dtype != np.uint8 or right.dtype != np.uint8:
            raise ValueError("head stereo images must be uint8 BGR arrays")
        if not np.issubdtype(depth.dtype, np.floating):
            raise ValueError("head depth must be a floating-point meter array")
        points = source_module.depth_to_point_cloud(depth, intrinsics)
        if points.ndim != 2 or points.shape[1] != 3 or points.dtype != np.float32:
            raise ValueError("projected point cloud must be float32 Nx3")
        if len(points) == 0:
            raise ValueError("head depth produced an empty point cloud")

        left_path = (sensor_dir / "head_left_bgr.npy").resolve()
        right_path = (sensor_dir / "head_right_bgr.npy").resolve()
        depth_path = (sensor_dir / "head_depth_m.npy").resolve()
        points_path = (sensor_dir / "head_pointcloud_camera.npz").resolve()
        projection_path = (sensor_dir / "projection_manifest.json").resolve()
        binding_path = (sensor_dir / "pointcloud_manifest.json").resolve()
        calibration_path = (calibration_dir / "bundle.json").resolve()
        proprio_path = (root / "proprioception.json").resolve()

        np.save(left_path, left, allow_pickle=False)
        np.save(right_path, right, allow_pickle=False)
        np.save(depth_path, depth.astype(np.float32, copy=False), allow_pickle=False)
        np.savez_compressed(points_path, points=points)

        calibration = {
            "schema": "demo_graph_lab.head_intrinsics.v1",
            "source_path": config["intrinsics_path"],
            "camera": "head",
            "frame": _OPTICAL_FRAME,
            "projection": "opencv_pinhole",
            "axis_convention": {
                "x": "right",
                "y": "down",
                "z": "forward",
            },
            "translation_unit": "meter",
            "intrinsics": intrinsics,
        }
        _write_json(calibration_path, calibration)
        projection = {
            "schema": _PROJECTION_SCHEMA,
            "source_frame_id": snapshot.frame_id,
            "source_timestamp_s": snapshot.timestamp_s,
            "depth_ref": str(depth_path),
            "point_cloud_ref": str(points_path),
            "input_shape": list(depth.shape),
            "valid_point_count": int(len(points)),
            "unit": "meter",
            "frame": _OPTICAL_FRAME,
            "calibration_ref": str(calibration_path),
            "filter": "finite_and_z_gt_zero",
        }
        _write_json(projection_path, projection)
        point_cloud_manifest = {
            "artifact_ref": str(points_path),
            "unit": "meter",
            "frame": _OPTICAL_FRAME,
            "calibration_ref": str(calibration_path),
            "evidence_ref": str(projection_path),
        }
        _write_json(binding_path, point_cloud_manifest)

        proprio_client = source_module.ReadOnlyProprioClient(
            config["pipeline_url"],
            timeout_s=config["timeout_s"],
        )
        proprio = proprio_client.read()
        call_record["calls"].extend(proprio.get("calls", []))
        robot_state = {
            "joint_positions": proprio["joint_positions"],
            "gripper_positions": proprio.get("gripper_positions", []),
            "end_effector_frame": proprio["end_effector_frame"],
            "end_effector_poses": proprio["end_effector_poses"],
            "evidence_ref": str(proprio_path),
        }
        proprio_artifact = {
            "schema": "demo_graph_lab.readonly_proprioception.v1",
            "captured_after_source_frame_id": snapshot.frame_id,
            "values": robot_state,
            "calls": proprio.get("calls", []),
            "synchronization": "sequential_after_head_capture_not_atomic",
            "joint_vector_order": ["arm_0[0:7]", "arm_1[0:7]"],
            "joint_unit": "radian",
            "pose_layout": "x_y_z_qx_qy_qz_qw",
            "position_unit": "meter",
            "quaternion_convention": "xyzw",
        }
        _write_json(proprio_path, proprio_artifact)

        observation_id = f"head-{snapshot.frame_id}-{int(snapshot.timestamp_s * 1e6)}"
        sensor_refs = [
            str(left_path),
            str(right_path),
            str(depth_path),
            str(points_path),
            str(projection_path),
            str(binding_path),
            str(calibration_path),
            str(proprio_path),
        ]
        observation = {
            "observation_id": observation_id,
            "captured_at_s": float(snapshot.timestamp_s),
            "frame": _OPTICAL_FRAME,
            "calibration_ref": str(calibration_path),
            "sensor_refs": sensor_refs,
            "robot_state": robot_state,
            "objects": [],
        }
        observation_from_record(observation)
        _write_json(root / "observation.json", observation)

        call_record["status"] = "ok"
        call_record["duration_s"] = time.monotonic() - started
        _write_json(sensor_dir / "call.json", call_record)
        manifest["status"] = "OBSERVATION_RECORDED"
        manifest["observation_id"] = observation_id
        manifest["updated_at"] = _utc_now()
        manifest["last_error"] = None
        manifest["artifacts"].update({
            "observation": "observation.json",
            "proprioception": "proprioception.json",
            "calibration": "calibration/bundle.json",
            "head_left_bgr": "sensor/head_left_bgr.npy",
            "head_right_bgr": "sensor/head_right_bgr.npy",
            "head_depth_m": "sensor/head_depth_m.npy",
            "point_cloud": "sensor/head_pointcloud_camera.npz",
            "projection_manifest": "sensor/projection_manifest.json",
            "point_cloud_manifest": "sensor/pointcloud_manifest.json",
            "capture_call": "sensor/call.json",
        })
        _write_json(root / "manifest.json", manifest)
        return manifest
    except Exception as error:
        call_record["status"] = "error"
        call_record["duration_s"] = time.monotonic() - started
        call_record["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "http_status": getattr(error, "status_code", None),
        }
        if sensor_dir.is_dir():
            payload = getattr(error, "payload", None)
            if isinstance(payload, Mapping):
                _write_json(sensor_dir / "error_payload.json", payload)
                manifest["artifacts"]["capture_error_payload"] = (
                    "sensor/error_payload.json"
                )
            raw_body = getattr(error, "raw_body", None)
            if isinstance(raw_body, bytes):
                (sensor_dir / "error_raw.bin").write_bytes(raw_body)
                manifest["artifacts"]["capture_error_raw"] = "sensor/error_raw.bin"
            _write_json(sensor_dir / "call.json", call_record)
            manifest["artifacts"]["capture_call"] = "sensor/call.json"
        _record_error(root, manifest, step="capture", error=error)
        raise
