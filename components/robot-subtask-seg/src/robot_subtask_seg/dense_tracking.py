from __future__ import annotations

import gzip
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

from robot_subtask_seg.schema import Trace
from robot_subtask_seg.video import sample_frames_uniform


DENSE_TRACKING_SCHEMA = "robot_subtask_seg.dense_tracking.v1"


class DenseTrackingError(RuntimeError):
    pass


class PointTrackingPort(Protocol):
    name: str

    def track_points(
        self,
        *,
        video_path: str,
        queries: list[dict[str, Any]],
        coordinate_width: int,
        coordinate_height: int,
        target_fps: float,
        inference_width: int,
        max_frames: int,
    ) -> dict[str, Any]:
        ...


class CoTrackerClient:
    name = "cotracker3_offline"

    def __init__(
        self,
        service_url: str,
        *,
        timeout_sec: float = 180.0,
        max_retries: int = 2,
        backoff_sec: float = 1.0,
        session: requests.Session | None = None,
    ) -> None:
        self.service_url = service_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.max_retries = max(0, max_retries)
        self.backoff_sec = max(0.0, backoff_sec)
        self.session = session or requests.Session()

    def track_points(
        self,
        *,
        video_path: str,
        queries: list[dict[str, Any]],
        coordinate_width: int,
        coordinate_height: int,
        target_fps: float,
        inference_width: int,
        max_frames: int,
    ) -> dict[str, Any]:
        payload = {
            "video_path": video_path,
            "queries": queries,
            "query_coordinate_width": coordinate_width,
            "query_coordinate_height": coordinate_height,
            "target_fps": target_fps,
            "inference_width": inference_width,
            "max_frames": max_frames,
            "backward_tracking": True,
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.service_url}/track_points",
                    json=payload,
                    timeout=self.timeout_sec,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_sec * (2**attempt))
                continue
            except requests.RequestException as exc:
                raise DenseTrackingError(f"CoTracker request failed: {exc}") from exc

            if response.status_code == 429 or response.status_code >= 500:
                last_error = DenseTrackingError(
                    f"CoTracker transient HTTP {response.status_code}: {response.text[:500]}"
                )
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_sec * (2**attempt))
                continue
            if response.status_code >= 400:
                raise DenseTrackingError(
                    f"CoTracker HTTP {response.status_code}: {response.text[:500]}"
                )
            try:
                result = response.json()
            except ValueError as exc:
                raise DenseTrackingError("CoTracker returned invalid JSON") from exc
            if result.get("schema") != "robot_video_perception.cotracker_points.v1":
                raise DenseTrackingError(
                    f"unexpected CoTracker response schema: {result.get('schema')}"
                )
            return result
        raise DenseTrackingError(
            f"CoTracker request failed after retries: {last_error}"
        ) from last_error


def enrich_dense_tracking(
    evidence_dir: str | Path,
    *,
    tracker: PointTrackingPort,
    output_dir: str | Path | None = None,
    points_per_object: int = 16,
    target_fps: float = 10.0,
    inference_width: int = 512,
    max_frames: int = 300,
    min_visible_ratio: float = 0.2,
) -> dict[str, Any]:
    source_root = Path(evidence_dir)
    destination = Path(output_dir) if output_dir is not None else source_root
    source_path = source_root / "video_evidence.json"
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    bundle = json.loads(source_path.read_text(encoding="utf-8"))
    if bundle.get("schema") != "robot_subtask_seg.video_evidence.v1":
        raise ValueError(f"unsupported evidence schema: {bundle.get('schema')}")
    if points_per_object < 4:
        raise ValueError("points_per_object must be >= 4")

    frame_meta = bundle.get("frames") or []
    if not frame_meta:
        raise ValueError("video evidence has no sampled frames")
    trace = _load_source_trace(bundle, evidence_root=source_root)
    video_path = _resolve_source_video_path(
        bundle,
        evidence_root=source_root,
        trace=trace,
    )
    coordinate_width = int(frame_meta[0]["width"])
    coordinate_height = int(frame_meta[0]["height"])
    anchors = select_anchor_objects(bundle["observations"])
    if not anchors:
        raise ValueError("video evidence has no grounding observations to track")

    queries: list[dict[str, Any]] = []
    object_meta: dict[str, dict[str, Any]] = {}
    for anchor in anchors:
        points = sample_anchor_points(
            source_root,
            anchor,
            point_count=points_per_object,
            width=coordinate_width,
            height=coordinate_height,
        )
        if len(points) < 4:
            continue
        object_id = anchor["dense_object_id"]
        object_meta[object_id] = {
            "object_id": object_id,
            "prompt": anchor["prompt"],
            "anchor_observation_id": anchor["observation_id"],
            "anchor_timestamp_sec": anchor["timestamp_sec"],
            "anchor_bbox_xyxy": anchor["bbox_xyxy"],
            "query_point_count": len(points),
        }
        for point_index, (x, y) in enumerate(points):
            queries.append(
                {
                    "query_id": f"{object_id}_point_{point_index:03d}",
                    "object_id": object_id,
                    "timestamp_sec": anchor["timestamp_sec"],
                    "x": round(float(x), 4),
                    "y": round(float(y), 4),
                }
            )
    if not queries:
        raise ValueError("grounding observations did not yield enough query points")

    raw = tracker.track_points(
        video_path=str(video_path),
        queries=queries,
        coordinate_width=coordinate_width,
        coordinate_height=coordinate_height,
        target_fps=target_fps,
        inference_width=inference_width,
        max_frames=max_frames,
    )
    dense_frames, object_summaries = summarize_dense_tracks(
        raw,
        object_meta=object_meta,
        trace=trace,
        min_visible_ratio=min_visible_ratio,
    )
    association = associate_sparse_observations(
        bundle["observations"],
        dense_frames=dense_frames,
        evidence_root=source_root,
        coordinate_width=coordinate_width,
        coordinate_height=coordinate_height,
    )
    for summary in object_summaries:
        summary["linked_observation_ids"] = association["by_object"].get(
            summary["object_id"], []
        )
        summary["segment_indices"] = sorted(
            {
                segment_index
                for observation in dense_frames["objects"][summary["object_id"]]
                for segment_index in observation["segment_indices"]
            }
        )

    destination.mkdir(parents=True, exist_ok=True)
    artifact_dir = destination / "artifacts" / "dense_tracking"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / "cotracker_raw.json.gz"
    frames_path = artifact_dir / "dense_track_frames.json.gz"
    _write_json_gzip(raw_path, raw)
    _write_json_gzip(frames_path, dense_frames)

    enriched = json.loads(json.dumps(bundle))
    for observation in enriched["observations"]:
        observation["dense_object_id"] = association["by_observation"].get(
            observation["observation_id"]
        )
    enriched["dense_tracking"] = {
        "schema": DENSE_TRACKING_SCHEMA,
        "provider": tracker.name,
        "method": "sam3_anchor_masks_plus_joint_point_tracking",
        "coordinate_frame": raw["coordinate_frame"],
        "frame_count": raw["video"]["frame_count"],
        "target_fps": raw["video"]["target_fps"],
        "object_count": len(object_summaries),
        "objects": object_summaries,
        "association": {
            "linked_observation_count": len(association["by_observation"]),
            "unlinked_observation_ids": association["unlinked_observation_ids"],
        },
        "artifacts": {
            "raw_tracks": str(raw_path.relative_to(destination)),
            "dense_frames": str(frames_path.relative_to(destination)),
        },
        "timing": raw.get("timing", {}),
        "model": raw.get("model", {}),
    }
    enriched["evidence_gaps"] = _updated_evidence_gaps(enriched.get("evidence_gaps", []))
    enriched.setdefault("provenance", {})["claims_dense_tracking"] = True
    enriched.setdefault("provenance", {})["claims_dense_point_tracking"] = True
    enriched["provenance"]["claims_dense_mask_tracking"] = False
    output_path = destination / "dense_video_evidence.json"
    output_path.write_text(
        json.dumps(enriched, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    preview_path = destination / "dense_tracking_preview.jpg"
    write_dense_tracking_preview(
        enriched,
        dense_frames=dense_frames,
        output_path=preview_path,
        video_path=video_path,
    )
    enriched["dense_tracking"]["artifacts"]["preview"] = str(
        preview_path.relative_to(destination)
    )
    output_path.write_text(
        json.dumps(enriched, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return enriched


def select_anchor_objects(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_prompt: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        by_prompt.setdefault(str(observation["prompt"]), []).append(observation)

    anchors: list[dict[str, Any]] = []
    for prompt, candidates in sorted(by_prompt.items()):
        first_frame = min(int(item["frame_index"]) for item in candidates)
        first = sorted(
            (item for item in candidates if int(item["frame_index"]) == first_frame),
            key=lambda item: (float(item["center_xy"][0]), float(item["center_xy"][1])),
        )
        for object_index, observation in enumerate(first):
            anchor = dict(observation)
            anchor["dense_object_id"] = f"{_safe_name(prompt)}_{object_index:03d}"
            anchors.append(anchor)
    return anchors


def sample_anchor_points(
    evidence_root: Path,
    anchor: dict[str, Any],
    *,
    point_count: int,
    width: int,
    height: int,
) -> list[tuple[float, float]]:
    mask_path = anchor.get("mask_path")
    if mask_path and (evidence_root / mask_path).exists():
        mask = np.asarray(Image.open(evidence_root / mask_path).convert("L")) > 127
        return _sample_binary_mask(mask, point_count=point_count)
    return _sample_bbox(
        anchor["bbox_xyxy"],
        point_count=point_count,
        width=width,
        height=height,
    )


def summarize_dense_tracks(
    raw: dict[str, Any],
    *,
    object_meta: dict[str, dict[str, Any]],
    trace: Trace | None,
    min_visible_ratio: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    timestamps = [float(value) for value in raw["video"]["timestamps_sec"]]
    by_object: dict[str, list[dict[str, Any]]] = {}
    for track in raw["tracks"]:
        by_object.setdefault(track["object_id"], []).append(track)

    dense_objects: dict[str, list[dict[str, Any]]] = {}
    summaries: list[dict[str, Any]] = []
    for object_id, tracks in sorted(by_object.items()):
        frame_observations: list[dict[str, Any]] = []
        centers: list[tuple[float, float] | None] = []
        for frame_index, timestamp in enumerate(timestamps):
            visible_positions = [
                track["positions_xy"][frame_index]
                for track in tracks
                if track["visible"][frame_index]
            ]
            visible_ratio = len(visible_positions) / len(tracks)
            reliable = (
                len(visible_positions) >= 2 and visible_ratio >= min_visible_ratio
            )
            center = None
            extent = None
            if reliable:
                points = np.asarray(visible_positions, dtype=float)
                center = [
                    round(float(np.median(points[:, 0])), 4),
                    round(float(np.median(points[:, 1])), 4),
                ]
                extent = [
                    round(float(points[:, 0].min()), 4),
                    round(float(points[:, 1].min()), 4),
                    round(float(points[:, 0].max()), 4),
                    round(float(points[:, 1].max()), 4),
                ]
                centers.append((center[0], center[1]))
            else:
                centers.append(None)
            frame_observations.append(
                {
                    "frame_index": frame_index,
                    "timestamp_sec": round(timestamp, 6),
                    "visible_point_count": len(visible_positions),
                    "visible_ratio": round(visible_ratio, 4),
                    "center_xy": center,
                    "tracked_point_extent_xyxy": extent,
                    "segment_indices": _segments_at_timestamp(trace, timestamp),
                }
            )

        reliable_centers = [center for center in centers if center is not None]
        displacement = [0.0, 0.0]
        if len(reliable_centers) >= 2:
            displacement = [
                round(reliable_centers[-1][0] - reliable_centers[0][0], 4),
                round(reliable_centers[-1][1] - reliable_centers[0][1], 4),
            ]
        path_length = sum(
            math.dist(first, second)
            for first, second in zip(reliable_centers, reliable_centers[1:])
        )
        meta = object_meta[object_id]
        summary = {
            **meta,
            "reliable_frame_count": len(reliable_centers),
            "reliable_frame_fraction": round(len(reliable_centers) / len(timestamps), 4),
            "displacement_xy_px": displacement,
            "path_length_px": round(path_length, 4),
            "occlusion_intervals": _occlusion_intervals(frame_observations),
        }
        dense_objects[object_id] = frame_observations
        summaries.append(summary)
    return (
        {
            "schema": "robot_subtask_seg.dense_track_frames.v1",
            "timestamps_sec": timestamps,
            "objects": dense_objects,
        },
        summaries,
    )


def associate_sparse_observations(
    observations: list[dict[str, Any]],
    *,
    dense_frames: dict[str, Any],
    evidence_root: Path,
    coordinate_width: int,
    coordinate_height: int,
) -> dict[str, Any]:
    object_prompts = {
        object_id: _prompt_from_object_id(object_id, observations)
        for object_id in dense_frames["objects"]
    }
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for observation in observations:
        grouped.setdefault(
            (int(observation["frame_index"]), str(observation["prompt"])), []
        ).append(observation)

    by_observation: dict[str, str] = {}
    by_object: dict[str, list[str]] = {}
    timestamps = dense_frames["timestamps_sec"]
    for (_, prompt), sparse_group in sorted(grouped.items()):
        pairs: list[tuple[float, str, str]] = []
        for observation in sparse_group:
            dense_index = _nearest_index(timestamps, float(observation["timestamp_sec"]))
            mask = _load_observation_mask(evidence_root, observation)
            for object_id, frames in dense_frames["objects"].items():
                if object_prompts.get(object_id) != prompt:
                    continue
                dense = frames[dense_index]
                center = dense.get("center_xy")
                if center is None:
                    continue
                score = _observation_match_score(
                    center,
                    observation,
                    mask=mask,
                    width=coordinate_width,
                    height=coordinate_height,
                )
                if score is not None:
                    pairs.append((score, observation["observation_id"], object_id))
        used_observations: set[str] = set()
        used_objects: set[str] = set()
        for _, observation_id, object_id in sorted(pairs, reverse=True):
            if observation_id in used_observations or object_id in used_objects:
                continue
            by_observation[observation_id] = object_id
            by_object.setdefault(object_id, []).append(observation_id)
            used_observations.add(observation_id)
            used_objects.add(object_id)

    all_ids = {observation["observation_id"] for observation in observations}
    return {
        "by_observation": by_observation,
        "by_object": by_object,
        "unlinked_observation_ids": sorted(all_ids - set(by_observation)),
    }


def write_dense_tracking_preview(
    bundle: dict[str, Any],
    *,
    dense_frames: dict[str, Any],
    output_path: str | Path,
    video_path: str | Path | None = None,
    panel_count: int = 4,
) -> None:
    resolved_video_path = video_path or bundle["source"]["video_path"]
    timestamps = dense_frames["timestamps_sec"]
    if not timestamps:
        return
    selected_indices = np.linspace(0, len(timestamps) - 1, panel_count).round().astype(int)
    selected_times = [timestamps[index] for index in selected_indices]
    sampled = sample_frames_uniform(
        resolved_video_path,
        start_sec=max(0.0, selected_times[0]),
        end_sec=max(selected_times[-1], selected_times[0] + 1e-3),
        max_frames=panel_count,
        frame_width=int(bundle["frames"][0]["width"]),
    )
    colors = [
        (0, 220, 120),
        (255, 80, 70),
        (0, 180, 255),
        (255, 210, 0),
        (220, 90, 255),
        (255, 140, 40),
    ]
    font = ImageFont.load_default()
    panels: list[Image.Image] = []
    for panel_index, (_, image) in enumerate(sampled):
        dense_index = int(selected_indices[min(panel_index, len(selected_indices) - 1)])
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 170, 24), fill=(0, 0, 0))
        draw.text(
            (7, 6),
            f"t={timestamps[dense_index]:.2f}s",
            font=font,
            fill=(255, 255, 255),
        )
        for object_index, (object_id, frames) in enumerate(
            sorted(dense_frames["objects"].items())
        ):
            color = colors[object_index % len(colors)]
            history = [
                frame["center_xy"]
                for frame in frames[: dense_index + 1]
                if frame["center_xy"] is not None
            ]
            if len(history) >= 2:
                draw.line([tuple(point) for point in history], fill=color, width=3)
            current = frames[dense_index]
            if current["center_xy"] is None:
                continue
            x, y = current["center_xy"]
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color, outline=(255, 255, 255))
            draw.text((x + 8, y - 8), object_id, font=font, fill=color)
        panels.append(image)

    if not panels:
        return
    columns = 2
    rows = math.ceil(len(panels) / columns)
    width = max(panel.width for panel in panels)
    height = max(panel.height for panel in panels)
    canvas = Image.new("RGB", (columns * width, rows * height), (25, 25, 25))
    for index, panel in enumerate(panels):
        canvas.paste(panel, ((index % columns) * width, (index // columns) * height))
    canvas.save(output_path, format="JPEG", quality=92)


def _sample_binary_mask(mask: np.ndarray, *, point_count: int) -> list[tuple[float, float]]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return []
    order = np.argsort(xs + ys * mask.shape[1])
    xs = xs[order]
    ys = ys[order]
    indices = np.linspace(0, len(xs) - 1, min(point_count, len(xs))).round().astype(int)
    return [(float(xs[index]), float(ys[index])) for index in np.unique(indices)]


def _sample_bbox(
    bbox: list[float],
    *,
    point_count: int,
    width: int,
    height: int,
) -> list[tuple[float, float]]:
    x1, y1, x2, y2 = [float(value) for value in bbox]
    side = math.ceil(math.sqrt(point_count))
    xs = np.linspace(max(0.0, x1 + 1), min(width - 1.0, x2 - 1), side)
    ys = np.linspace(max(0.0, y1 + 1), min(height - 1.0, y2 - 1), side)
    return [(float(x), float(y)) for y in ys for x in xs][:point_count]


def _observation_match_score(
    center: list[float],
    observation: dict[str, Any],
    *,
    mask: np.ndarray | None,
    width: int,
    height: int,
) -> float | None:
    x, y = center
    inside = False
    if mask is not None:
        px = min(max(int(round(x)), 0), mask.shape[1] - 1)
        py = min(max(int(round(y)), 0), mask.shape[0] - 1)
        inside = bool(mask[py, px])
    x1, y1, x2, y2 = [float(value) for value in observation["bbox_xyxy"]]
    inside_bbox = x1 <= x <= x2 and y1 <= y <= y2
    distance = math.dist((x, y), tuple(float(value) for value in observation["center_xy"]))
    max_distance = 0.12 * math.hypot(width, height)
    if not inside and not inside_bbox and distance > max_distance:
        return None
    return (1.0 if inside else 0.5 if inside_bbox else 0.0) + max(
        0.0, 1.0 - distance / max_distance
    ) * 0.25


def _load_observation_mask(
    evidence_root: Path, observation: dict[str, Any]
) -> np.ndarray | None:
    mask_path = observation.get("mask_path")
    if not mask_path:
        return None
    path = evidence_root / mask_path
    if not path.exists():
        return None
    return np.asarray(Image.open(path).convert("L")) > 127


def _prompt_from_object_id(
    object_id: str, observations: list[dict[str, Any]]
) -> str | None:
    anchors = select_anchor_objects(observations)
    for anchor in anchors:
        if anchor["dense_object_id"] == object_id:
            return str(anchor["prompt"])
    return None


def _occlusion_intervals(frames: list[dict[str, Any]]) -> list[dict[str, float]]:
    intervals: list[dict[str, float]] = []
    start: float | None = None
    previous: float | None = None
    for frame in frames:
        timestamp = float(frame["timestamp_sec"])
        if frame["center_xy"] is None:
            if start is None:
                start = timestamp
            previous = timestamp
        elif start is not None:
            intervals.append({"start_sec": start, "end_sec": previous or start})
            start = None
            previous = None
    if start is not None:
        intervals.append({"start_sec": start, "end_sec": previous or start})
    return intervals


def _load_source_trace(
    bundle: dict[str, Any],
    *,
    evidence_root: Path,
) -> Trace | None:
    trace_path = bundle.get("source", {}).get("trace_path")
    if not trace_path:
        return None
    path = _resolve_existing_path(trace_path, evidence_root=evidence_root)
    if path is None:
        return None
    return Trace.model_validate_json(path.read_text(encoding="utf-8"))


def _resolve_source_video_path(
    bundle: dict[str, Any],
    *,
    evidence_root: Path,
    trace: Trace | None,
) -> Path:
    candidates = []
    if trace is not None and trace.video.path:
        candidates.append(trace.video.path)
    source_path = bundle.get("source", {}).get("video_path")
    if source_path:
        candidates.append(source_path)
    for candidate in candidates:
        resolved = _resolve_existing_path(candidate, evidence_root=evidence_root)
        if resolved is not None:
            return resolved
    raise DenseTrackingError(
        "source video not found; checked trace.video.path and evidence source.video_path"
    )


def _resolve_existing_path(
    value: str | Path,
    *,
    evidence_root: Path,
) -> Path | None:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve() if path.exists() else None

    candidates = [Path.cwd() / path]
    candidates.extend(parent / path for parent in (evidence_root, *evidence_root.parents))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _segments_at_timestamp(trace: Trace | None, timestamp_sec: float) -> list[int]:
    if trace is None:
        return []
    return [
        segment.index
        for segment in trace.segments
        if segment.start_sec - 1e-6 <= timestamp_sec <= segment.end_sec + 1e-6
    ]


def _updated_evidence_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = [
        gap for gap in gaps if gap.get("capability") != "dense_temporal_tracking"
    ]
    updated.append(
        {
            "capability": "dense_mask_tracking",
            "reason": "continuous point tracks are available, but masks are grounded only on sampled frames",
            "required_evidence": ["video mask propagation backend"],
        }
    )
    return updated


def _write_json_gzip(path: Path, payload: dict[str, Any]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def _nearest_index(values: list[float], target: float) -> int:
    return min(range(len(values)), key=lambda index: abs(values[index] - target))


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_").lower()
    return normalized or "object"
