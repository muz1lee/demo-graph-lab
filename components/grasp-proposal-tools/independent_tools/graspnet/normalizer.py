from __future__ import annotations

from typing import Any

from .contract import GRASPNET_PROPOSALS_SCHEMA, GraspNetConfig


def normalize_grasp_response(
    raw_response: Any,
    *,
    config: GraspNetConfig | dict[str, Any] | None = None,
    input_reference: dict[str, Any] | None = None,
    raw_response_path: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize GraspNet/AnyGrasp-style JSON into grasp proposal records.

    The normalizer keeps original fields and coordinate frame information. It
    does not transform coordinates, infer object identity, or choose a winning
    grasp for a task.
    """

    cfg = config if isinstance(config, GraspNetConfig) else GraspNetConfig.from_dict(config)
    candidates = _extract_candidates(raw_response, field_map=cfg.field_map)
    warnings: list[str] = []

    proposals = [
        _normalize_candidate(
            item,
            index=index,
            cfg=cfg,
            warnings=warnings,
        )
        for index, item in enumerate(candidates)
    ]
    proposals = [item for item in proposals if item is not None]
    if cfg.max_candidates is not None:
        proposals = proposals[: max(0, int(cfg.max_candidates))]
    for rank, item in enumerate(proposals):
        item["rank"] = rank

    return {
        "schema": GRASPNET_PROPOSALS_SCHEMA,
        "source_policy": "external_grasp_proposals_no_route_decision",
        "source": source or {},
        "input_reference": input_reference or {},
        "raw_response_path": raw_response_path,
        "config": cfg.to_dict(),
        "num_raw_candidates": len(candidates),
        "num_proposals": len(proposals),
        "proposals": proposals,
        "warnings": _dedupe(warnings),
    }


def _extract_candidates(raw: Any, *, field_map: dict[str, str]) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [_as_mapping(item) for item in raw]
    if not isinstance(raw, dict):
        return []

    for key in _mapped_keys(field_map, "candidates", ["proposals", "grasps", "grasp_candidates", "grasp_group", "predictions", "results"]):
        value = raw.get(key)
        if isinstance(value, list):
            return [_as_mapping(item) for item in value]

    translations = _first_list(raw, _mapped_keys(field_map, "translation", ["translations", "trans", "centers", "positions"]))
    rotations = _first_list(raw, _mapped_keys(field_map, "rotation_matrix", ["rotation_matrices", "rotations", "rotation"]))
    scores = _first_list(raw, _mapped_keys(field_map, "score", ["scores", "confidence", "confidences"]))
    widths = _first_list(raw, _mapped_keys(field_map, "width", ["widths", "gripper_widths"]))
    depths = _first_list(raw, _mapped_keys(field_map, "depth", ["depths"]))
    heights = _first_list(raw, _mapped_keys(field_map, "height", ["heights"]))
    object_ids = _first_list(raw, _mapped_keys(field_map, "object_id", ["object_ids", "object_names", "labels"]))
    collision_free = _first_list(raw, _mapped_keys(field_map, "collision_free", ["collision_free", "collision_free_mask"]))

    arrays = [item for item in [translations, rotations, scores, widths, depths, heights, object_ids, collision_free] if item is not None]
    if not arrays:
        return [_as_mapping(raw)]
    size = max((len(item) for item in arrays if isinstance(item, list)), default=0)
    candidates: list[dict[str, Any]] = []
    for index in range(size):
        candidate: dict[str, Any] = {}
        _copy_index(candidate, "translation", translations, index)
        _copy_index(candidate, "rotation_matrix", rotations, index)
        _copy_index(candidate, "score", scores, index)
        _copy_index(candidate, "width", widths, index)
        _copy_index(candidate, "depth", depths, index)
        _copy_index(candidate, "height", heights, index)
        _copy_index(candidate, "object_id", object_ids, index)
        _copy_index(candidate, "collision_free", collision_free, index)
        candidate["raw_index"] = index
        candidates.append(candidate)
    return candidates


def _normalize_candidate(
    candidate: dict[str, Any],
    *,
    index: int,
    cfg: GraspNetConfig,
    warnings: list[str],
) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None

    score = _number(_first_value(candidate, cfg.field_map, "score", ["score", "confidence", "grasp_score"]))
    confidence = _number(_first_value(candidate, cfg.field_map, "confidence", ["confidence", "score"]))
    translation = _vector(_first_value(candidate, cfg.field_map, "translation", ["translation", "translation_xyz", "position", "center"]), 3)
    rotation_matrix = _matrix3(_first_value(candidate, cfg.field_map, "rotation_matrix", ["rotation_matrix", "rotation", "rot"]))
    quaternion = _vector(_first_value(candidate, cfg.field_map, "quaternion", ["quaternion", "quat", "orientation"]), 4)
    approach = _vector(_first_value(candidate, cfg.field_map, "approach", ["approach", "approach_vector", "approach_direction"]), 3)

    pose_source_fields = [
        key
        for key in ["translation", "translation_xyz", "position", "center", "rotation_matrix", "rotation", "rot", "quaternion", "quat", "orientation"]
        if key in candidate
    ]
    coordinate_frame = _string_or_none(_first_value(candidate, cfg.field_map, "coordinate_frame", ["coordinate_frame", "frame", "frame_id"])) or cfg.coordinate_frame or "unknown"
    if coordinate_frame == "unknown":
        warnings.append(f"candidate[{index}] coordinate frame is unknown")

    proposal = {
        "proposal_id": f"grasp_{index:03d}",
        "rank": index,
        "raw_index": candidate.get("raw_index", index),
        "score": score,
        "confidence": confidence,
        "object_id": _string_or_none(_first_value(candidate, cfg.field_map, "object_id", ["object_id", "object_name", "label"])),
        "pose": {
            "translation": translation,
            "rotation_matrix": rotation_matrix,
            "quaternion": quaternion,
            "coordinate_frame": coordinate_frame,
            "source_fields": pose_source_fields,
        },
        "approach": approach,
        "width": _number(_first_value(candidate, cfg.field_map, "width", ["width", "gripper_width"])),
        "depth": _number(_first_value(candidate, cfg.field_map, "depth", ["depth"])),
        "height": _number(_first_value(candidate, cfg.field_map, "height", ["height"])),
        "contact_region": _string_or_none(_first_value(candidate, cfg.field_map, "contact_region", ["contact_region", "region"])),
        "collision_free": _bool_or_none(_first_value(candidate, cfg.field_map, "collision_free", ["collision_free", "collision_ok"])),
        "reachable": _bool_or_none(_first_value(candidate, cfg.field_map, "reachable", ["reachable", "ik_ok"])),
        "evidence": {
            "source": "external_grasp_detector_response",
            "raw_index": candidate.get("raw_index", index),
        },
    }
    if cfg.preserve_raw:
        proposal["raw"] = candidate
    if not translation and not rotation_matrix and not quaternion:
        warnings.append(f"candidate[{index}] has no pose fields")
    return proposal


def _mapped_keys(field_map: dict[str, str], canonical: str, defaults: list[str]) -> list[str]:
    mapped = field_map.get(canonical)
    return [mapped, *defaults] if mapped else defaults


def _first_value(candidate: dict[str, Any], field_map: dict[str, str], canonical: str, defaults: list[str]) -> Any:
    for key in _mapped_keys(field_map, canonical, defaults):
        if key in candidate:
            return candidate.get(key)
    return None


def _first_list(raw: dict[str, Any], keys: list[str]) -> list[Any] | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            return value
    return None


def _copy_index(target: dict[str, Any], key: str, values: list[Any] | None, index: int) -> None:
    if isinstance(values, list) and index < len(values):
        target[key] = values[index]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        return {"values": list(value)}
    return {"value": value}


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _vector(value: Any, length: int) -> list[float | int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return None
    return list(value)


def _matrix3(value: Any) -> list[list[float | int]] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    rows = [_vector(row, 3) for row in value]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
