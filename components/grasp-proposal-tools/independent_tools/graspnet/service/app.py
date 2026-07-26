from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import sys
import traceback

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    image_path: str | None = None
    depth_path: str | None = None
    mask_path: str | None = None
    point_cloud_path: str | None = None
    object_hint: str | None = None
    frame_id: str | None = None
    coordinate_frame: str | None = None
    camera_intrinsics: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def _load_config() -> dict[str, Any]:
    path = os.environ.get("GRASPNET_SERVICE_CONFIG")
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {"config_error": f"missing config: {path}"}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"config_error": repr(exc)}
    return data if isinstance(data, dict) else {"config_error": "config root must be object"}


CONFIG = _load_config()
BACKEND = None
BACKEND_ERROR = None


class FixtureBackend:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.proposals = config.get("fixture_proposals") if isinstance(config.get("fixture_proposals"), list) else []

    def predict(self, request: PredictRequest) -> dict[str, Any]:
        coordinate_frame = request.coordinate_frame or self.config.get("coordinate_frame") or "unknown"
        proposals = []
        for index, item in enumerate(self.proposals):
            if isinstance(item, dict):
                copied = dict(item)
            else:
                copied = {}
            copied.setdefault("raw_index", index)
            copied.setdefault("coordinate_frame", coordinate_frame)
            proposals.append(copied)
        return {
            "schema": "kw_independent.graspnet.raw_response.v1",
            "backend": "fixture",
            "source_policy": "fixture_transport_smoke_not_model_evidence",
            "coordinate_frame": coordinate_frame,
            "grasps": proposals,
        }


class GraspNetBaselineBackend:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.repo_dir = Path(str(config["repo_dir"]))
        self.checkpoint_path = Path(str(config["checkpoint_path"]))
        if not self.repo_dir.exists():
            raise FileNotFoundError(f"repo_dir not found: {self.repo_dir}")
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"checkpoint_path not found: {self.checkpoint_path}")

        sys.path.insert(0, str(self.repo_dir))
        sys.path.insert(0, str(self.repo_dir / "models"))
        sys.path.insert(0, str(self.repo_dir / "dataset"))
        sys.path.insert(0, str(self.repo_dir / "utils"))

        import torch
        from graspnet import GraspNet, pred_decode

        self.torch = torch
        self.pred_decode = pred_decode
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.net = GraspNet(
            input_feature_dim=0,
            num_view=int(config.get("num_view", 300)),
            num_angle=int(config.get("num_angle", 12)),
            num_depth=int(config.get("num_depth", 4)),
            cylinder_radius=float(config.get("cylinder_radius", 0.05)),
            hmin=float(config.get("hmin", -0.02)),
            hmax_list=list(config.get("hmax_list", [0.01, 0.02, 0.03, 0.04])),
            is_training=False,
        )
        self.net.to(self.device)
        checkpoint = torch.load(str(self.checkpoint_path), map_location=self.device)
        self.net.load_state_dict(checkpoint["model_state_dict"])
        self.epoch = checkpoint.get("epoch")
        self.net.eval()

    def predict(self, request: PredictRequest) -> dict[str, Any]:
        end_points = self._request_to_end_points(request)
        with self.torch.no_grad():
            end_points = self.net(end_points)
            grasp_preds = self.pred_decode(end_points)
        grasp_array = grasp_preds[0].detach().cpu().numpy()
        if grasp_array.size:
            grasp_array = grasp_array[np.argsort(-grasp_array[:, 0])]
        max_grasps = int(request.extra.get("max_grasps") or self.config.get("max_grasps", 50))
        array = grasp_array[:max_grasps]
        return {
            "schema": "kw_independent.graspnet.raw_response.v1",
            "backend": "graspnet_baseline",
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_epoch": self.epoch,
            "coordinate_frame": request.coordinate_frame or self.config.get("coordinate_frame") or "unknown",
            "grasps": [_grasp_array_to_dict(row, i, request.coordinate_frame or self.config.get("coordinate_frame")) for i, row in enumerate(array)],
        }

    def _request_to_end_points(self, request: PredictRequest) -> dict[str, Any]:
        if request.point_cloud_path:
            cloud = np.load(request.point_cloud_path)
            if isinstance(cloud, np.lib.npyio.NpzFile):
                cloud = cloud["points"] if "points" in cloud else cloud[cloud.files[0]]
            sampled = _sample_points(np.asarray(cloud, dtype=np.float32), int(self.config.get("num_point", 20000)))
            tensor = self.torch.from_numpy(sampled[np.newaxis].astype(np.float32)).to(self.device)
            return {"point_clouds": tensor}
        raise ValueError("graspnet_baseline backend currently requires point_cloud_path (.npy/.npz)")


def _sample_points(points: np.ndarray, num_point: int) -> np.ndarray:
    if points.ndim != 2 or points.shape[1] < 3:
        raise ValueError("point_cloud_path must contain an Nx3 or NxC array")
    points = points[:, :3]
    valid = np.isfinite(points).all(axis=1)
    points = points[valid]
    if len(points) == 0:
        raise ValueError("point cloud has no valid points")
    if len(points) >= num_point:
        idxs = np.random.choice(len(points), num_point, replace=False)
    else:
        idxs = np.concatenate([np.arange(len(points)), np.random.choice(len(points), num_point - len(points), replace=True)])
    return points[idxs]


def _grasp_array_to_dict(row: np.ndarray, index: int, coordinate_frame: str | None) -> dict[str, Any]:
    values = row.astype(float).tolist()
    return {
        "raw_index": index,
        "score": values[0],
        "width": values[1],
        "height": values[2],
        "depth": values[3],
        "rotation_matrix": [values[4:7], values[7:10], values[10:13]],
        "translation": values[13:16],
        "object_id": int(values[16]),
        "coordinate_frame": coordinate_frame or "unknown",
        "raw_grasp_array": values,
    }


def _init_backend() -> tuple[Any, str | None]:
    if CONFIG.get("config_error"):
        return None, str(CONFIG["config_error"])
    backend = str(CONFIG.get("backend") or "fixture")
    try:
        if backend == "fixture":
            return FixtureBackend(CONFIG), None
        if backend == "graspnet_baseline":
            return GraspNetBaselineBackend(CONFIG), None
        return None, f"unsupported backend: {backend}"
    except Exception:
        return None, traceback.format_exc()


BACKEND, BACKEND_ERROR = _init_backend()
app = FastAPI(title="Standalone Grasp Proposal Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": BACKEND is not None,
        "schema": "kw_independent.graspnet.health.v1",
        "backend": CONFIG.get("backend") or "fixture",
        "backend_ready": BACKEND is not None,
        "backend_error": BACKEND_ERROR,
        "source_policy": "service_health_not_task_evidence",
    }


@app.post("/predict")
def predict(payload: PredictRequest) -> dict[str, Any]:
    if BACKEND is None:
        return {
            "ok": False,
            "schema": "kw_independent.graspnet.raw_response.v1",
            "error": BACKEND_ERROR or "backend unavailable",
            "source_policy": "backend_unavailable_not_grasp_quality_evidence",
        }
    try:
        result = BACKEND.predict(payload)
        result["ok"] = True
        result["input_reference"] = _payload_dict(payload)
        return result
    except Exception:
        return {
            "ok": False,
            "schema": "kw_independent.graspnet.raw_response.v1",
            "error": traceback.format_exc(),
            "input_reference": _payload_dict(payload),
            "source_policy": "prediction_error_not_route_decision",
        }


def _payload_dict(payload: PredictRequest) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()
