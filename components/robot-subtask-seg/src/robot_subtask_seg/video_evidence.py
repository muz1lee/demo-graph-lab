from __future__ import annotations

import base64
import io
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests
from PIL import Image

from robot_subtask_seg.schema import Trace
from robot_subtask_seg.video import get_video_duration, sample_frames_at_interval


VIDEO_EVIDENCE_SCHEMA = "robot_subtask_seg.video_evidence.v1"


class VideoEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class GroundingDetection:
    bbox_xyxy: tuple[float, float, float, float]
    score: float | None = None
    mask_base64: str | None = None
    source_details: dict[str, Any] | None = None


class TextGroundingPort(Protocol):
    name: str

    def segment(self, image: Image.Image, *, text_prompt: str) -> list[GroundingDetection]:
        ...


class Sam3Client:
    name = "sam3_text_grounding"

    def __init__(
        self,
        service_url: str,
        *,
        timeout_sec: float = 60.0,
        max_retries: int = 3,
        backoff_sec: float = 1.0,
        session: requests.Session | None = None,
    ) -> None:
        self.service_url = service_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.max_retries = max(0, max_retries)
        self.backoff_sec = max(0.0, backoff_sec)
        self.session = session or requests.Session()

    def segment(self, image: Image.Image, *, text_prompt: str) -> list[GroundingDetection]:
        prompt = text_prompt.strip()
        if not prompt:
            raise ValueError("text_prompt must not be empty")

        payload = {"image": _encode_jpeg_base64(image), "text_prompt": prompt}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.service_url}/segment",
                    json=payload,
                    timeout=self.timeout_sec,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise VideoEvidenceError(
                        f"SAM3 transient HTTP {response.status_code}: {response.text[:300]}"
                    )
                response.raise_for_status()
                body = response.json()
                if not body.get("success", False):
                    raise VideoEvidenceError(
                        f"SAM3 rejected request: {body.get('error') or body.get('message')}"
                    )
                return [_parse_detection(item) for item in body.get("detections", [])]
            except (requests.RequestException, ValueError, VideoEvidenceError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_sec * (2**attempt))
        raise VideoEvidenceError(f"SAM3 request failed after retries: {last_error}") from last_error


def extract_video_evidence(
    video_path: str | Path,
    *,
    output_dir: str | Path,
    prompts: list[str],
    segmenter: TextGroundingPort,
    task_id: str | None = None,
    instruction: str = "",
    trace: Trace | None = None,
    trace_path: str | Path | None = None,
    sample_sec: float = 1.0,
    frame_width: int = 640,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    max_frames: int = 120,
    keep_going: bool = True,
) -> dict[str, Any]:
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(video)
    normalized_prompts = _normalize_prompts(prompts)
    if not normalized_prompts:
        raise ValueError("at least one non-empty prompt is required")
    if max_frames <= 0:
        raise ValueError("max_frames must be > 0")

    destination = Path(output_dir)
    frames_dir = destination / "artifacts" / "frames"
    masks_dir = destination / "artifacts" / "masks"
    frames_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)

    duration_sec = get_video_duration(video)
    bounded_end = end_sec
    if bounded_end is None and duration_sec is not None:
        bounded_end = duration_sec

    observations: list[dict[str, Any]] = []
    service_errors: list[dict[str, Any]] = []
    sampled_frames: list[dict[str, Any]] = []
    iterator = sample_frames_at_interval(
        video,
        sample_sec=sample_sec,
        frame_width=frame_width,
        start_sec=start_sec,
        end_sec=bounded_end,
    )
    for frame_index, (timestamp_sec, image) in enumerate(iterator):
        if frame_index >= max_frames:
            break
        frame_rel = Path("artifacts") / "frames" / f"frame_{frame_index:04d}.jpg"
        image.save(destination / frame_rel, format="JPEG", quality=95)
        sampled_frames.append(
            {
                "frame_index": frame_index,
                "timestamp_sec": timestamp_sec,
                "image_path": str(frame_rel),
                "width": image.width,
                "height": image.height,
            }
        )
        for prompt in normalized_prompts:
            try:
                detections = segmenter.segment(image, text_prompt=prompt)
            except Exception as exc:
                service_errors.append(
                    {
                        "frame_index": frame_index,
                        "timestamp_sec": timestamp_sec,
                        "prompt": prompt,
                        "error_type": type(exc).__name__,
                        "reason": str(exc),
                    }
                )
                if not keep_going:
                    raise
                continue
            for detection_index, detection in enumerate(detections):
                mask_path = _write_mask(
                    detection.mask_base64,
                    masks_dir=masks_dir,
                    prompt=prompt,
                    frame_index=frame_index,
                    detection_index=detection_index,
                )
                observations.append(
                    {
                        "observation_id": (
                            f"obs_{frame_index:04d}_{_safe_name(prompt)}_{detection_index:03d}"
                        ),
                        "frame_index": frame_index,
                        "timestamp_sec": timestamp_sec,
                        "prompt": prompt,
                        "bbox_xyxy": [round(value, 4) for value in detection.bbox_xyxy],
                        "center_xy": [
                            round((detection.bbox_xyxy[0] + detection.bbox_xyxy[2]) / 2.0, 4),
                            round((detection.bbox_xyxy[1] + detection.bbox_xyxy[3]) / 2.0, 4),
                        ],
                        "score": detection.score,
                        "mask_path": mask_path,
                        "frame_path": str(frame_rel),
                        "segment_indices": _segments_at_timestamp(trace, timestamp_sec),
                        "coordinate_frame": "image_pixels",
                        "evidence_source": segmenter.name,
                        "source_details": detection.source_details or {},
                    }
                )

    tracks = link_detection_tracklets(
        observations,
        frame_width=frame_width,
        sample_sec=sample_sec,
    )
    bundle = {
        "schema": VIDEO_EVIDENCE_SCHEMA,
        "task_id": task_id,
        "instruction": instruction,
        "source": {
            "video_path": str(video),
            "trace_path": str(trace_path) if trace_path is not None else None,
            "input_mode": "monocular_rgb_video",
            "duration_sec": duration_sec,
        },
        "sampling": {
            "sample_sec": sample_sec,
            "frame_width": frame_width,
            "start_sec": start_sec,
            "end_sec": bounded_end,
            "sampled_frame_count": len(sampled_frames),
            "truncated_by_max_frames": len(sampled_frames) >= max_frames,
        },
        "grounding": {
            "provider": segmenter.name,
            "prompts": normalized_prompts,
            "method": "independent_keyframe_text_grounding",
        },
        "tracking": {
            "method": "sampled_detection_linking",
            "coordinate_frame": "image_pixels",
            "track_count": len(tracks),
            "tracks": tracks,
        },
        "frames": sampled_frames,
        "observations": observations,
        "service_errors": service_errors,
        "evidence_gaps": _default_evidence_gaps(service_errors),
        "provenance": {
            "generated_by": "robot-subtask-seg.extract-video-evidence",
            "claims_metric_3d": False,
            "claims_6d_pose": False,
            "claims_dense_tracking": False,
        },
    }
    (destination / "video_evidence.json").write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return bundle


def link_detection_tracklets(
    observations: list[dict[str, Any]],
    *,
    frame_width: float,
    sample_sec: float,
    min_iou: float = 0.05,
    max_center_distance_ratio: float = 0.18,
    max_gap_steps: int = 2,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for observation in observations:
        grouped.setdefault(str(observation["prompt"]), {}).setdefault(
            int(observation["frame_index"]), []
        ).append(observation)

    completed: list[dict[str, Any]] = []
    for prompt, by_frame in sorted(grouped.items()):
        active: list[dict[str, Any]] = []
        track_counter = 0
        for frame_index in sorted(by_frame):
            detections = list(by_frame[frame_index])
            candidate_pairs: list[tuple[float, int, int]] = []
            for track_index, track in enumerate(active):
                gap_steps = frame_index - int(track["last_frame_index"])
                if gap_steps > max_gap_steps:
                    continue
                previous = track["observations"][-1]
                for detection_index, detection in enumerate(detections):
                    score = _association_score(
                        previous["bbox_xyxy"],
                        detection["bbox_xyxy"],
                        frame_width=frame_width,
                        min_iou=min_iou,
                        max_center_distance_ratio=max_center_distance_ratio,
                    )
                    if score is not None:
                        candidate_pairs.append((score, track_index, detection_index))

            matched_tracks: set[int] = set()
            matched_detections: set[int] = set()
            for _, track_index, detection_index in sorted(candidate_pairs, reverse=True):
                if track_index in matched_tracks or detection_index in matched_detections:
                    continue
                track = active[track_index]
                track["observations"].append(detections[detection_index])
                track["last_frame_index"] = frame_index
                matched_tracks.add(track_index)
                matched_detections.add(detection_index)

            still_active: list[dict[str, Any]] = []
            for track in active:
                if frame_index - int(track["last_frame_index"]) <= max_gap_steps:
                    still_active.append(track)
                else:
                    completed.append(track)
            active = still_active

            for detection_index, detection in enumerate(detections):
                if detection_index in matched_detections:
                    continue
                active.append(
                    {
                        "track_id": f"{_safe_name(prompt)}_{track_counter:03d}",
                        "prompt": prompt,
                        "last_frame_index": frame_index,
                        "observations": [detection],
                    }
                )
                track_counter += 1
        completed.extend(active)

    return [_summarize_track(track, sample_sec=sample_sec) for track in completed]


def _parse_detection(item: Any) -> GroundingDetection:
    if not isinstance(item, dict):
        raise VideoEvidenceError(f"invalid SAM3 detection type: {type(item).__name__}")
    bbox = item.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise VideoEvidenceError(f"invalid SAM3 bbox: {bbox!r}")
    return GroundingDetection(
        bbox_xyxy=tuple(float(value) for value in bbox),
        score=float(item["score"]) if item.get("score") is not None else None,
        mask_base64=str(item["mask"]) if item.get("mask") else None,
        source_details={
            key: value for key, value in item.items() if key not in {"bbox", "score", "mask"}
        },
    )


def _encode_jpeg_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _write_mask(
    encoded: str | None,
    *,
    masks_dir: Path,
    prompt: str,
    frame_index: int,
    detection_index: int,
) -> str | None:
    if not encoded:
        return None
    try:
        payload = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise VideoEvidenceError("SAM3 returned an invalid base64 mask") from exc
    filename = f"frame_{frame_index:04d}_{_safe_name(prompt)}_{detection_index:03d}.png"
    path = masks_dir / filename
    path.write_bytes(payload)
    return str(Path("artifacts") / "masks" / filename)


def _normalize_prompts(prompts: list[str]) -> list[str]:
    return list(dict.fromkeys(prompt.strip() for prompt in prompts if prompt.strip()))


def _segments_at_timestamp(trace: Trace | None, timestamp_sec: float) -> list[int]:
    if trace is None:
        return []
    return [
        segment.index
        for segment in trace.segments
        if segment.start_sec - 1e-6 <= timestamp_sec <= segment.end_sec + 1e-6
    ]


def _association_score(
    previous_bbox: list[float],
    current_bbox: list[float],
    *,
    frame_width: float,
    min_iou: float,
    max_center_distance_ratio: float,
) -> float | None:
    iou = _bbox_iou(previous_bbox, current_bbox)
    center_distance = math.dist(_bbox_center(previous_bbox), _bbox_center(current_bbox))
    max_distance = max(1.0, frame_width * max_center_distance_ratio)
    if iou < min_iou and center_distance > max_distance:
        return None
    proximity = max(0.0, 1.0 - center_distance / max_distance)
    return iou + 0.25 * proximity


def _bbox_iou(first: list[float], second: list[float]) -> float:
    x1 = max(float(first[0]), float(second[0]))
    y1 = max(float(first[1]), float(second[1]))
    x2 = min(float(first[2]), float(second[2]))
    y2 = min(float(first[3]), float(second[3]))
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    first_area = max(0.0, float(first[2]) - float(first[0])) * max(
        0.0, float(first[3]) - float(first[1])
    )
    second_area = max(0.0, float(second[2]) - float(second[0])) * max(
        0.0, float(second[3]) - float(second[1])
    )
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def _bbox_center(bbox: list[float]) -> tuple[float, float]:
    return (
        (float(bbox[0]) + float(bbox[2])) / 2.0,
        (float(bbox[1]) + float(bbox[3])) / 2.0,
    )


def _summarize_track(track: dict[str, Any], *, sample_sec: float) -> dict[str, Any]:
    observations = sorted(track["observations"], key=lambda item: item["timestamp_sec"])
    centers = [tuple(float(value) for value in item["center_xy"]) for item in observations]
    scores = [float(item["score"]) for item in observations if item.get("score") is not None]
    displacement = (
        [round(centers[-1][0] - centers[0][0], 4), round(centers[-1][1] - centers[0][1], 4)]
        if len(centers) >= 2
        else [0.0, 0.0]
    )
    path_length = sum(math.dist(first, second) for first, second in zip(centers, centers[1:]))
    return {
        "track_id": track["track_id"],
        "prompt": track["prompt"],
        "observation_count": len(observations),
        "start_sec": observations[0]["timestamp_sec"],
        "end_sec": observations[-1]["timestamp_sec"],
        "mean_score": round(sum(scores) / len(scores), 6) if scores else None,
        "displacement_xy_px": displacement,
        "path_length_px": round(path_length, 4),
        "nominal_sample_sec": sample_sec,
        "observations": observations,
    }


def _default_evidence_gaps(service_errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps = [
        {
            "capability": "metric_depth",
            "reason": "source contains monocular RGB frames only",
            "required_evidence": ["aligned depth or metric multi-view reconstruction"],
        },
        {
            "capability": "camera_calibration",
            "reason": "camera intrinsics and extrinsics are absent from the video manifest",
            "required_evidence": ["camera intrinsics", "camera-to-robot transform"],
        },
        {
            "capability": "robot_state",
            "reason": "EEF and joint state are not synchronized with the MP4",
            "required_evidence": ["timestamped EEF pose or qpos"],
        },
        {
            "capability": "object_6d_pose",
            "reason": "metric depth, calibration, and object pose evidence are unavailable",
            "required_evidence": ["RGB-D", "camera calibration", "object model or pose observations"],
        },
        {
            "capability": "dense_temporal_tracking",
            "reason": "current output links independently grounded sampled frames",
            "required_evidence": ["video point tracker or mask propagation model"],
        },
    ]
    if service_errors:
        gaps.append(
            {
                "capability": "complete_grounding_observations",
                "reason": f"{len(service_errors)} grounding requests failed",
                "required_evidence": ["successful retry or alternate grounding service"],
            }
        )
    return gaps


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_").lower()
    return normalized or "object"
