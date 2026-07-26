"""Coarse gripper-object relation extraction at selected demo keyframes.

The extractor consumes one frame at a time. CoTracker points define the
object-relative frame, the object mask checks track provenance, and nearby
dark gripper pixels provide the contact-side observation. It deliberately does
not reconstruct a trajectory or a metric gripper pose.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


class KeyframeRelationError(ValueError):
    pass


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _unit(vector):
    import numpy as np

    norm = float(np.linalg.norm(vector))
    if norm <= 1e-9:
        raise KeyframeRelationError("cannot normalize a zero-length vector")
    return vector / norm


def extract_relation_from_arrays(
    *,
    relation_id: str,
    event: str,
    segment_index: int,
    object_id: str,
    timestamp_sec: float,
    frame_bgr,
    object_points_xy,
    seed_points_xy,
    object_mask,
    total_track_count: int,
    dark_threshold: int = 75,
) -> dict[str, Any]:
    """Extract a non-metric relation from one frame and tracked object points."""

    import cv2
    import numpy as np

    points = np.asarray(object_points_xy, dtype=float)
    seeds = np.asarray(seed_points_xy, dtype=float)
    mask = np.asarray(object_mask)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 4:
        raise KeyframeRelationError("at least four visible object points are required")
    if seeds.ndim != 2 or seeds.shape[1] != 2 or len(seeds) < 4:
        raise KeyframeRelationError("at least four CoTracker seed points are required")
    if total_track_count < len(points):
        raise KeyframeRelationError("total_track_count cannot be smaller than visible points")
    if frame_bgr is None or getattr(frame_bgr, "ndim", 0) != 3:
        raise KeyframeRelationError("keyframe must be a BGR image")
    if mask.ndim == 3:
        mask = mask[..., 0]
    if mask.ndim != 2:
        raise KeyframeRelationError("object mask must be a 2D image")

    center = points.mean(axis=0)
    covariance = np.cov(points - center, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    major_index = int(np.argmax(eigenvalues))
    axis = _unit(eigenvectors[:, major_index])
    projections = (points - center) @ axis
    object_length = float(projections.max() - projections.min())
    if object_length <= 1e-6:
        raise KeyframeRelationError("tracked points do not define an object axis")

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    dark_yx = np.argwhere(gray < int(dark_threshold))
    if not len(dark_yx):
        raise KeyframeRelationError("no gripper-colored pixels found near object")
    dark_xy = dark_yx[:, ::-1].astype(float)
    nearest_sq = ((dark_xy[:, None, :] - points[None, :, :]) ** 2).sum(axis=2).min(axis=1)
    near_radius = max(18.0, min(48.0, object_length * 0.95))
    nearby = dark_xy[nearest_sq <= near_radius**2]
    if len(nearby) < 20:
        raise KeyframeRelationError("insufficient nearby gripper evidence")

    initial_gripper_center = nearby.mean(axis=0)
    if float((initial_gripper_center - center) @ axis) < 0.0:
        axis = -axis
        projections = -projections
    normal = np.array([-axis[1], axis[0]], dtype=float)
    relative = nearby - center
    along = relative @ axis
    across = relative @ normal
    local = nearby[
        (along > -0.75 * object_length)
        & (along < 1.50 * object_length)
        & (np.abs(across) < max(18.0, 0.90 * object_length))
    ]
    if len(local) < 20:
        raise KeyframeRelationError("nearby gripper evidence is geometrically inconsistent")
    gripper_center = local.mean(axis=0)
    if float((gripper_center - center) @ axis) < 0.0:
        axis = -axis
        normal = -normal
        projections = -projections

    approach = _unit(gripper_center - center)
    axial_similarity = abs(float(approach @ axis))
    approach_angle_deg = math.degrees(math.acos(min(1.0, axial_similarity)))
    if approach_angle_deg <= 30.0:
        approach_relation = "axial"
    elif approach_angle_deg <= 60.0:
        approach_relation = "oblique"
    else:
        approach_relation = "lateral"

    object_min = float(projections.min())
    object_max = float(projections.max())
    contact_along = float((gripper_center - center) @ axis)
    contact_fraction = (contact_along - object_min) / (object_max - object_min)
    clipped_fraction = min(1.0, max(0.0, contact_fraction))
    if clipped_fraction < 1.0 / 3.0:
        region_label = "lower_body"
    elif clipped_fraction < 2.0 / 3.0:
        region_label = "middle_body"
    else:
        region_label = "upper_body"

    if normal[0] < 0.0 or (abs(float(normal[0])) < 1e-9 and normal[1] < 0.0):
        normal = -normal
    closing_direction = normal
    local_relative = local - center
    local_along = local_relative @ axis
    local_across = local_relative @ closing_direction
    contact_band = local_along >= object_max - 0.60 * object_length
    positive_support = int(((local_across > 2.0) & contact_band).sum())
    negative_support = int(((local_across < -2.0) & contact_band).sum())
    side_balance = (
        min(positive_support, negative_support)
        / max(positive_support, negative_support)
        if max(positive_support, negative_support)
        else 0.0
    )

    seed_pixels = np.rint(seeds).astype(int)
    inside = (
        (seed_pixels[:, 0] >= 0)
        & (seed_pixels[:, 0] < mask.shape[1])
        & (seed_pixels[:, 1] >= 0)
        & (seed_pixels[:, 1] < mask.shape[0])
    )
    covered = np.zeros(len(seed_pixels), dtype=bool)
    valid = seed_pixels[inside]
    if len(valid):
        covered[inside] = mask[valid[:, 1], valid[:, 0]] > 0
    mask_coverage = float(covered.mean())
    visibility = min(1.0, len(points) / max(1, total_track_count))
    major = float(eigenvalues[major_index])
    minor = float(eigenvalues[1 - major_index])
    anisotropy = 1.0 - min(1.0, max(0.0, minor / max(major, 1e-9)))
    evidence_support = min(1.0, len(local) / max(80.0, object_length * 8.0))
    confidence = (
        0.30 * visibility
        + 0.25 * mask_coverage
        + 0.20 * anisotropy
        + 0.15 * evidence_support
        + 0.10 * side_balance
    )

    return {
        "schema": "demo_graph.keyframe_relation.v1",
        "relation_id": relation_id,
        "event": event,
        "segment_index": int(segment_index),
        "object_id": object_id,
        "keyframe": {"timestamp_sec": round(float(timestamp_sec), 4)},
        "region": {
            "label": region_label,
            "axis_fraction": round(float(clipped_fraction), 4),
        },
        "object_axis": {
            "image_unit": [round(float(value), 6) for value in axis],
        },
        "approach_axis": {
            "image_unit": [round(float(value), 6) for value in approach],
            "object_frame_unit": [
                round(float(approach @ axis), 6),
                round(float(approach @ closing_direction), 6),
            ],
            "relation": approach_relation,
            "angle_to_object_axis_deg": round(float(approach_angle_deg), 3),
        },
        "closing_direction": {
            "image_unit": [
                round(float(value), 6) for value in closing_direction
            ],
            "object_frame_unit": [0.0, 1.0],
            "relation": "cross_axis",
            "angle_to_object_axis_deg": 90.0,
        },
        "confidence": round(float(min(1.0, max(0.0, confidence))), 4),
        "evidence_quality": {
            "visible_track_fraction": round(visibility, 4),
            "seed_mask_coverage": round(mask_coverage, 4),
            "object_axis_anisotropy": round(anisotropy, 4),
            "two_sided_gripper_support": round(side_balance, 4),
        },
        "provenance": {
            "source": "demo_video",
            "reference": f"keyframe:{relation_id}:cotracker+object_mask",
        },
    }


def _load_track_payload(path: str | Path) -> Mapping[str, Any]:
    track_path = Path(path)
    if track_path.suffix == ".gz":
        with gzip.open(track_path, "rt", encoding="utf-8") as handle:
            raw = json.load(handle)
    else:
        with track_path.open("rt", encoding="utf-8") as handle:
            raw = json.load(handle)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("tracks"), list):
        raise KeyframeRelationError("CoTracker artifact has an unsupported schema")
    return raw


def extract_relation_from_artifacts(
    *,
    case: Mapping[str, Any],
    video_path: str | Path,
    tracks_path: str | Path,
) -> dict[str, Any]:
    import cv2
    import numpy as np

    raw = _load_track_payload(tracks_path)
    timestamps = tuple(float(value) for value in raw.get("video", {}).get("timestamps_sec", ()))
    if not timestamps:
        raise KeyframeRelationError("CoTracker artifact has no timestamps")
    requested_timestamp = float(case["timestamp_sec"])
    frame_index = min(
        range(len(timestamps)),
        key=lambda index: abs(timestamps[index] - requested_timestamp),
    )
    object_id = str(case["object_id"])
    tracks = [
        item
        for item in raw["tracks"]
        if isinstance(item, Mapping) and str(item.get("object_id")) == object_id
    ]
    if not tracks:
        raise KeyframeRelationError(f"object {object_id!r} has no CoTracker points")
    visible_tracks = [
        item
        for item in tracks
        if bool(item.get("visible", ())[frame_index])
    ]
    object_points = [
        item["positions_xy"][frame_index] for item in visible_tracks
    ]
    seed_points = [
        [item["seed"]["x"], item["seed"]["y"]]
        for item in tracks
    ]

    capture = cv2.VideoCapture(str(video_path))
    capture.set(cv2.CAP_PROP_POS_MSEC, timestamps[frame_index] * 1000.0)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise KeyframeRelationError("failed to decode selected keyframe")
    mask_path = Path(str(case["object_mask"]))
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise KeyframeRelationError(f"failed to read object mask: {mask_path}")
    if mask.shape != frame.shape[:2]:
        mask = cv2.resize(
            mask,
            (frame.shape[1], frame.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )

    relation = extract_relation_from_arrays(
        relation_id=str(case["relation_id"]),
        event=str(case.get("event") or "grasp"),
        segment_index=int(case["segment_index"]),
        object_id=object_id,
        timestamp_sec=timestamps[frame_index],
        frame_bgr=frame,
        object_points_xy=np.asarray(object_points, dtype=float),
        seed_points_xy=np.asarray(seed_points, dtype=float),
        object_mask=mask,
        total_track_count=len(tracks),
        dark_threshold=int(case.get("dark_threshold", 75)),
    )
    relation["keyframe"]["requested_timestamp_sec"] = round(requested_timestamp, 4)
    relation["evidence"] = {
        "video_digest": _sha256(video_path),
        "tracks_digest": _sha256(tracks_path),
        "mask_digest": _sha256(mask_path),
        "visible_track_count": len(visible_tracks),
        "total_track_count": len(tracks),
    }
    return relation


def compare_annotations(
    relations: Sequence[Mapping[str, Any]],
    annotations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected = {str(item["relation_id"]): item for item in annotations}
    checks: list[dict[str, Any]] = []
    for relation in relations:
        relation_id = str(relation["relation_id"])
        annotation = expected.get(relation_id)
        if annotation is None:
            checks.append(
                {
                    "relation_id": relation_id,
                    "passed": False,
                    "reason": "missing human annotation",
                }
            )
            continue
        actual = {
            "region_label": (relation.get("region") or {}).get("label"),
            "approach_relation": (
                relation.get("approach_axis") or {}
            ).get("relation"),
            "closing_relation": (
                relation.get("closing_direction") or {}
            ).get("relation"),
        }
        wanted = {
            "region_label": annotation.get("region_label"),
            "approach_relation": annotation.get("approach_relation"),
            "closing_relation": annotation.get("closing_relation"),
        }
        checks.append(
            {
                "relation_id": relation_id,
                "passed": actual == wanted,
                "actual": actual,
                "expected": wanted,
            }
        )
    passed = sum(bool(item["passed"]) for item in checks)
    return {
        "checked": len(checks),
        "passed": passed,
        "all_match": bool(checks) and passed == len(checks),
        "checks": checks,
    }


def _resolve_cases(
    path: str | Path,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    spec_path = Path(path)
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise TypeError("relation case file must contain an object")
    cases = raw.get("cases") or ()
    annotations = raw.get("annotations") or ()
    if not isinstance(cases, list) or not all(isinstance(item, Mapping) for item in cases):
        raise TypeError("cases must be a list of objects")
    if not isinstance(annotations, list) or not all(
        isinstance(item, Mapping) for item in annotations
    ):
        raise TypeError("annotations must be a list of objects")
    resolved: list[Mapping[str, Any]] = []
    for item in cases:
        case = dict(item)
        mask = Path(str(case["object_mask"]))
        if not mask.is_absolute():
            mask = spec_path.parent / mask
        case["object_mask"] = str(mask)
        resolved.append(case)
    return tuple(resolved), tuple(dict(item) for item in annotations)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract coarse gripper-object relations at selected keyframes."
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    cases, annotations = _resolve_cases(args.cases)
    relations = [
        extract_relation_from_artifacts(
            case=case,
            video_path=args.video,
            tracks_path=args.tracks,
        )
        for case in cases
    ]
    payload: dict[str, Any] = {
        "schema": "demo_graph.keyframe_relations.v1",
        "relations": relations,
    }
    if annotations:
        payload["human_annotation_evaluation"] = compare_annotations(
            relations,
            annotations,
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
