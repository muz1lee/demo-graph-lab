from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from robot_subtask_seg.schema import Trace


DEMONSTRATION_BUNDLE_SCHEMA = "robot_subtask_seg.demonstration_bundle.v1"
DENSE_EVIDENCE_SCHEMA = "robot_subtask_seg.video_evidence.v1"


def build_demonstration_bundle(
    *,
    trace_path: str | Path,
    dense_evidence_path: str | Path,
) -> dict[str, Any]:
    trace_file = Path(trace_path).expanduser().resolve()
    evidence_file = Path(dense_evidence_path).expanduser().resolve()
    trace = Trace.model_validate_json(trace_file.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    if evidence.get("schema") != DENSE_EVIDENCE_SCHEMA:
        raise ValueError(f"unsupported video evidence schema: {evidence.get('schema')}")
    dense = evidence.get("dense_tracking")
    if not isinstance(dense, dict):
        raise ValueError("dense video evidence is missing dense_tracking")

    dense_frames = _load_dense_frames(evidence, evidence_file=evidence_file)
    objects = [_compact_object(item) for item in dense.get("objects", []) if isinstance(item, dict)]
    segment_evidence = [
        _segment_evidence(
            segment=segment.model_dump(),
            dense_frames=dense_frames,
            object_summaries=objects,
        )
        for segment in trace.segments
    ]
    artifact_refs = _artifact_refs(
        trace_file=trace_file,
        evidence_file=evidence_file,
        evidence=evidence,
    )
    return {
        "schema": DEMONSTRATION_BUNDLE_SCHEMA,
        "task_id": trace.task_id,
        "task_class": trace.task_class,
        "instruction": trace.instruction,
        "trace": {
            "schema_version": trace.schema_version,
            "trace_id": trace.trace_id,
            "task_id": trace.task_id,
            "task_class": trace.task_class,
            "instruction": trace.instruction,
            "video": trace.video.model_dump(),
            "segments": [segment.model_dump() for segment in trace.segments],
        },
        "objects": objects,
        "segment_evidence": segment_evidence,
        "evidence_gaps": list(evidence.get("evidence_gaps") or []),
        "artifact_refs": artifact_refs,
        "summary": {
            "segment_count": len(trace.segments),
            "object_count": len(objects),
            "dense_frame_count": int(dense.get("frame_count") or 0),
            "linked_observation_count": int(
                (dense.get("association") or {}).get("linked_observation_count") or 0
            ),
            "coordinate_frame": dense.get("coordinate_frame"),
            "tracking_provider": dense.get("provider"),
        },
        "provenance": {
            "generated_by": "robot-subtask-seg.export-demonstration-bundle",
            "trace_sha256": _sha256(trace_file),
            "dense_evidence_sha256": _sha256(evidence_file),
            "claims_metric_3d": bool(
                (evidence.get("provenance") or {}).get("claims_metric_3d", False)
            ),
            "claims_6d_pose": bool(
                (evidence.get("provenance") or {}).get("claims_6d_pose", False)
            ),
            "claims_dense_tracking": bool(
                (evidence.get("provenance") or {}).get("claims_dense_tracking", False)
            ),
        },
    }


def write_demonstration_bundle(bundle: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output


def _load_dense_frames(
    evidence: dict[str, Any],
    *,
    evidence_file: Path,
) -> dict[str, Any] | None:
    dense = evidence.get("dense_tracking") or {}
    artifact = (dense.get("artifacts") or {}).get("dense_frames")
    if not artifact:
        return None
    path = Path(artifact)
    if not path.is_absolute():
        path = evidence_file.parent / path
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else None


def _compact_object(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "object_id",
        "prompt",
        "anchor_observation_id",
        "anchor_timestamp_sec",
        "anchor_bbox_xyxy",
        "query_point_count",
        "reliable_frame_count",
        "reliable_frame_fraction",
        "displacement_xy_px",
        "path_length_px",
        "occlusion_intervals",
        "linked_observation_ids",
    )
    return {field: item.get(field) for field in fields if field in item}


def _segment_evidence(
    *,
    segment: dict[str, Any],
    dense_frames: dict[str, Any] | None,
    object_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    index = int(segment["index"])
    result = {
        "segment_index": index,
        "start_sec": float(segment["start_sec"]),
        "end_sec": float(segment["end_sec"]),
        "label": segment["label"],
        "object_observations": [],
        "evidence_source": "dense_video_evidence",
    }
    if not dense_frames:
        result["evidence_gap"] = "dense frame artifact is unavailable"
        return result

    object_frames = dense_frames.get("objects") or {}
    for summary in object_summaries:
        object_id = str(summary.get("object_id") or "")
        frames = object_frames.get(object_id)
        if not object_id or not isinstance(frames, list):
            continue
        selected = [
            frame
            for frame in frames
            if isinstance(frame, dict)
            and float(segment["start_sec"]) - 1e-6
            <= float(frame.get("timestamp_sec") or 0.0)
            <= float(segment["end_sec"]) + 1e-6
        ]
        if not selected:
            continue
        visible = [frame for frame in selected if frame.get("center_xy") is not None]
        centers = [
            [float(value) for value in frame["center_xy"]]
            for frame in visible
        ]
        net_displacement = 0.0
        path_length = 0.0
        if len(centers) >= 2:
            net_displacement = math.dist(centers[0], centers[-1])
            path_length = sum(
                math.dist(first, second)
                for first, second in zip(centers, centers[1:])
            )
        result["object_observations"].append(
            {
                "object_id": object_id,
                "prompt": summary.get("prompt"),
                "sample_count": len(selected),
                "visible_sample_count": len(visible),
                "visible_fraction": round(len(visible) / len(selected), 4),
                "first_visible_center_xy": _rounded_point(centers[0]) if centers else None,
                "last_visible_center_xy": _rounded_point(centers[-1]) if centers else None,
                "net_displacement_px": round(net_displacement, 4),
                "path_length_px": round(path_length, 4),
                "evidence_ref": f"dense_track:{object_id}:segment:{index}",
            }
        )
    return result


def _artifact_refs(
    *,
    trace_file: Path,
    evidence_file: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    dense_artifacts = ((evidence.get("dense_tracking") or {}).get("artifacts") or {})
    refs: dict[str, Any] = {
        "trace": {"path": str(trace_file), "kind": "refined_trace"},
        "dense_evidence": {"path": str(evidence_file), "kind": "dense_video_evidence"},
    }
    for name in ("dense_frames", "raw_tracks", "preview"):
        value = dense_artifacts.get(name)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = evidence_file.parent / path
        refs[name] = {"path": str(path.resolve()), "kind": name}
    return refs


def _rounded_point(point: list[float]) -> list[float]:
    return [round(float(value), 4) for value in point]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
