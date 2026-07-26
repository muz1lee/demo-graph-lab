from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import av
import numpy as np
import torch
from PIL import Image

from video_perception_service.contracts import TrackPointsRequest


class CoTrackerBackend:
    def __init__(
        self,
        *,
        repository_path: str | Path,
        checkpoint_path: str | Path,
        device: str = "cuda:0",
        allowed_roots: list[str | Path] | None = None,
    ) -> None:
        self.repository_path = Path(repository_path).resolve()
        self.checkpoint_path = Path(checkpoint_path).resolve()
        self.device = torch.device(device)
        self.allowed_roots = [
            Path(root).resolve() for root in (allowed_roots or ["/mnt/workspace/wht"])
        ]
        self._lock = threading.Lock()

        if not self.repository_path.exists():
            raise FileNotFoundError(self.repository_path)
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(self.checkpoint_path)
        sys.path.insert(0, str(self.repository_path))
        from cotracker.predictor import CoTrackerPredictor

        started = time.perf_counter()
        self.model = CoTrackerPredictor(
            checkpoint=str(self.checkpoint_path),
            offline=True,
            window_len=60,
        ).to(self.device)
        self.model.eval()
        self.load_time_sec = round(time.perf_counter() - started, 4)
        self.checkpoint_sha256 = _sha256(self.checkpoint_path)

    @classmethod
    def from_environment(cls) -> "CoTrackerBackend":
        roots = os.environ.get("VIDEO_TRACKING_ALLOWED_ROOTS", "/mnt/workspace/wht")
        return cls(
            repository_path=os.environ.get(
                "COTRACKER_REPOSITORY",
                "/mnt/workspace/wht/robot-video-perception-service/vendor/co-tracker",
            ),
            checkpoint_path=os.environ.get(
                "COTRACKER_CHECKPOINT",
                "/mnt/workspace/wht/robot-video-perception-service/models/scaled_offline.pth",
            ),
            device=os.environ.get("COTRACKER_DEVICE", "cuda:0"),
            allowed_roots=[item for item in roots.split(":") if item],
        )

    def health(self) -> dict[str, Any]:
        return {
            "ready": True,
            "backend": "cotracker3_offline",
            "device": str(self.device),
            "gpu_name": (
                torch.cuda.get_device_name(self.device) if self.device.type == "cuda" else None
            ),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_sha256": self.checkpoint_sha256,
            "repository_path": str(self.repository_path),
            "load_time_sec": self.load_time_sec,
        }

    def track(self, request: TrackPointsRequest) -> dict[str, Any]:
        video_path = self._validated_video_path(request.video_path)
        decode_started = time.perf_counter()
        decoded = _decode_video(
            video_path,
            target_fps=request.target_fps,
            inference_width=request.inference_width,
            max_frames=request.max_frames,
        )
        decode_time = time.perf_counter() - decode_started

        query_width = request.query_coordinate_width or decoded["source_width"]
        query_height = request.query_coordinate_height or decoded["source_height"]
        query_tensor = []
        query_frames = []
        for query in request.queries:
            frame_index = _nearest_index(decoded["timestamps_sec"], query.timestamp_sec)
            query_frames.append(frame_index)
            query_tensor.append(
                [
                    float(frame_index),
                    float(query.x) * decoded["inference_width"] / query_width,
                    float(query.y) * decoded["inference_height"] / query_height,
                ]
            )

        video_tensor = (
            torch.from_numpy(decoded["frames"])
            .permute(0, 3, 1, 2)
            .unsqueeze(0)
            .float()
            .to(self.device)
        )
        queries = torch.tensor([query_tensor], dtype=torch.float32, device=self.device)

        infer_started = time.perf_counter()
        with self._lock, torch.inference_mode():
            tracks, visibility = self.model(
                video_tensor,
                queries=queries,
                backward_tracking=request.backward_tracking,
            )
            if self.device.type == "cuda":
                torch.cuda.synchronize(self.device)
        inference_time = time.perf_counter() - infer_started

        tracks_np = tracks[0].detach().cpu().numpy()
        visibility_np = visibility[0].detach().cpu().numpy().astype(bool)
        tracks_np[:, :, 0] *= query_width / decoded["inference_width"]
        tracks_np[:, :, 1] *= query_height / decoded["inference_height"]

        response_tracks = []
        for query_index, query in enumerate(request.queries):
            response_tracks.append(
                {
                    "query_id": query.query_id,
                    "object_id": query.object_id,
                    "seed": {
                        "timestamp_sec": query.timestamp_sec,
                        "matched_frame_index": query_frames[query_index],
                        "matched_frame_timestamp_sec": decoded["timestamps_sec"][
                            query_frames[query_index]
                        ],
                        "x": query.x,
                        "y": query.y,
                    },
                    "positions_xy": [
                        [round(float(position[0]), 4), round(float(position[1]), 4)]
                        for position in tracks_np[:, query_index]
                    ],
                    "visible": [
                        bool(value) for value in visibility_np[:, query_index].tolist()
                    ],
                }
            )

        return {
            "schema": "robot_video_perception.cotracker_points.v1",
            "backend": "cotracker3_offline",
            "video": {
                "path": str(video_path),
                "source_width": decoded["source_width"],
                "source_height": decoded["source_height"],
                "inference_width": decoded["inference_width"],
                "inference_height": decoded["inference_height"],
                "target_fps": request.target_fps,
                "frame_count": len(decoded["timestamps_sec"]),
                "timestamps_sec": [
                    round(float(value), 6) for value in decoded["timestamps_sec"]
                ],
                "truncated": decoded["truncated"],
            },
            "coordinate_frame": {
                "name": "video_image_pixels",
                "width": query_width,
                "height": query_height,
            },
            "tracks": response_tracks,
            "timing": {
                "decode_sec": round(decode_time, 4),
                "inference_sec": round(inference_time, 4),
            },
            "model": {
                "checkpoint_sha256": self.checkpoint_sha256,
                "repository_path": str(self.repository_path),
            },
        }

    def _validated_video_path(self, value: str) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError(f"video path is outside allowed roots: {path}")
        return path


def _decode_video(
    path: Path,
    *,
    target_fps: float,
    inference_width: int,
    max_frames: int,
) -> dict[str, Any]:
    frames: list[np.ndarray] = []
    timestamps: list[float] = []
    next_timestamp = 0.0
    source_width = 0
    source_height = 0
    truncated = False

    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            timestamp = float(frame.time) if frame.time is not None else None
            if timestamp is None:
                continue
            if source_width == 0:
                source_width = frame.width
                source_height = frame.height
            if timestamp + 1e-6 < next_timestamp:
                continue
            if len(frames) >= max_frames:
                truncated = True
                break
            inference_height = max(
                2,
                round(source_height * inference_width / source_width),
            )
            image = frame.to_image().convert("RGB").resize(
                (inference_width, inference_height),
                Image.Resampling.BILINEAR,
            )
            frames.append(np.asarray(image))
            timestamps.append(timestamp)
            next_timestamp += 1.0 / target_fps

    if len(frames) < 2:
        raise ValueError(f"video produced fewer than two sampled frames: {path}")
    return {
        "frames": np.stack(frames),
        "timestamps_sec": timestamps,
        "source_width": source_width,
        "source_height": source_height,
        "inference_width": frames[0].shape[1],
        "inference_height": frames[0].shape[0],
        "truncated": truncated,
    }


def _nearest_index(values: list[float], target: float) -> int:
    return min(range(len(values)), key=lambda index: abs(values[index] - target))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
