"""Validate graph structure, closed vocabularies, and metric literals.

约束 args 中不得出现数值；度量量必须通过 typed hole 解析。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..common import artifacts
from . import vocab

_UNIT_RE = re.compile(r"\d+\.?\d*\s*(mm|cm|m\b|deg|°|rad)", re.I)
_HOLE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_OBJECT_ARGS = {
    "axis_parallel": {"axis_a", "axis_b"},
    "axis_vertical": {"axis"},
    "center_align": {"obj_a", "obj_b"},
    "region_grasp": {"obj"},
    "approach_direction": {"target"},
    "above": {"obj_a", "obj_b"},
    "inside": {"obj_a", "obj_b"},
    "clearance": {"obj_a", "obj_b"},
}


def _is_metric_literal(v) -> bool:
    """数值型 arg 即违规;含单位的数字串违规;'tube0' 这类带数字标识符不违规。"""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        s = v.strip()
        try:
            float(s)
            return True
        except ValueError:
            pass
        return bool(_UNIT_RE.search(s))
    if isinstance(v, (list, tuple)):
        return any(_is_metric_literal(x) for x in v)
    if isinstance(v, dict):
        return any(_is_metric_literal(x) for x in v.values())
    return False


def _is_registry_ref(value, registry_ids: set[str]) -> bool:
    return (isinstance(value, str)
            and value.split(".", 1)[0] in registry_ids)


def _contains_registry_ref(value, registry_ids: set[str]) -> bool:
    if not isinstance(value, str):
        return False
    return any(re.search(
        rf"(?<![A-Za-z0-9]){re.escape(object_id)}(?![A-Za-z0-9])", value)
        for object_id in registry_ids
    )


def check_item(item: dict, stage_idx: int, field: str,
               registry_ids: set[str] | None = None) -> list[str]:
    if not isinstance(item, dict):
        return [f"s{stage_idx}.{field}: item 必须是对象"]
    errs = []
    name = item.get("name")
    if name not in vocab.CONSTRAINT_VOCAB:
        errs.append(f"s{stage_idx}.{field}: 未知约束名 {name!r}")
    args = item.get("args", {}) or {}
    if not isinstance(args, dict):
        errs.append(f"s{stage_idx}.{field}: args 必须是对象")
        args = {}
    spec = vocab.CONSTRAINT_VOCAB.get(name)
    if spec:
        required = set(spec["args"])
        allowed = required | set(spec.get("optional", []))
        missing = sorted(required - set(args))
        extra = sorted(set(args) - allowed)
        if missing:
            errs.append(f"s{stage_idx}.{field}: {name} 缺少参数 {missing}")
        if extra:
            errs.append(f"s{stage_idx}.{field}: {name} 含未知参数 {extra}")
    if name == "region_grasp" and args.get("region") not in vocab.GRASP_REGIONS:
        errs.append(f"s{stage_idx}.{field}: 非法 region {args.get('region')!r}")
    if name == "approach_direction" and args.get("cone") not in vocab.APPROACH_CONES:
        errs.append(f"s{stage_idx}.{field}: 非法 cone {args.get('cone')!r}")
    if item.get("provenance") not in vocab.PROVENANCE_ALLOWED:
        errs.append(f"s{stage_idx}.{field}: 非法 provenance {item.get('provenance')!r}")
    if registry_ids is not None:
        for key in _OBJECT_ARGS.get(name, set()):
            if key in args and not _is_registry_ref(args[key], registry_ids):
                errs.append(
                    f"s{stage_idx}.{field}: {name}.{key} 未引用 object registry: "
                    f"{args[key]!r}")
        if (name == "carry" and "relation" in args
                and not _contains_registry_ref(args["relation"], registry_ids)):
            errs.append(
                f"s{stage_idx}.{field}: carry.relation 未引用 object registry: "
                f"{args['relation']!r}")
    pairs = args.items() if isinstance(args, dict) else enumerate(args)
    for key, v in pairs:
        if _is_metric_literal(v):
            errs.append(f"s{stage_idx}.{field}: 度量字面量 {name}.{key}={v!r}(必须留洞)")
    return errs


def _confidence_errors(item: dict, prefix: str) -> list[str]:
    confidence = item.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1):
        return [f"{prefix}.confidence must be in [0, 1]"]
    return []


def _evidence_errors(item: dict, prefix: str, required: bool = True,
                     total_frames: int | None = None,
                     allowed_evidence_frames: set[int] | None = None) -> list[str]:
    evidence = item.get("evidence_frames")
    if (not isinstance(evidence, list)
            or any(not isinstance(frame, int) or isinstance(frame, bool) or frame < 0
                   for frame in evidence)):
        return [f"{prefix}.evidence_frames must be a non-negative integer list"]
    errors = []
    if required and not evidence:
        errors.append(f"{prefix}.evidence_frames must not be empty")
    if (total_frames is not None
            and any(frame >= total_frames for frame in evidence)):
        errors.append(
            f"{prefix}.evidence_frames must be smaller than total_frames")
    if (allowed_evidence_frames is not None
            and any(frame not in allowed_evidence_frames for frame in evidence)):
        errors.append(
            f"{prefix}.evidence_frames must reference only displayed keyframes")
    return errors


def _hole_errors(hole, prefix: str, *, allow_votes: bool = True) -> list[str]:
    if not isinstance(hole, dict):
        return [f"{prefix} must be an object"]
    required = {"name", "type", "solver_hint", "frame"}
    allowed = required | {"purpose"} | ({"votes"} if allow_votes else set())
    errors = []
    missing = sorted(required - set(hole))
    extra = sorted(set(hole) - allowed)
    if missing:
        errors.append(f"{prefix} missing fields {missing}")
    if extra:
        errors.append(f"{prefix} has unknown fields {extra}")
    if not isinstance(hole.get("name"), str) or not _HOLE_NAME_RE.fullmatch(hole.get("name", "")):
        errors.append(f"{prefix}.name must be snake_case")
    if hole.get("type") not in vocab.HOLE_TYPES:
        errors.append(f"{prefix}.type is outside the closed vocabulary")
    for field in ("solver_hint", "frame"):
        if not isinstance(hole.get(field), str) or not hole.get(field):
            errors.append(f"{prefix}.{field} must be a non-empty string")
    if _is_metric_literal(hole.get("solver_hint")):
        errors.append(f"{prefix}.solver_hint contains a metric literal")
    purpose = hole.get("purpose")
    if purpose is not None:
        if hole.get("type") != "runtime_condition":
            errors.append(f"{prefix}.purpose is only valid for runtime_condition")
        elif purpose != "lower_stop":
            errors.append(f"{prefix}.purpose is outside the closed vocabulary")
    return errors


def validate_stage_sample(sample, stage: dict, registry_ids: set[str],
                          total_frames: int | None = None,
                          allowed_evidence_frames: set[int] | None = None) -> list[str]:
    """Validate one raw constraint-extraction sample before it can vote."""
    if not isinstance(sample, dict):
        return ["sample must be an object"]
    errors = []
    required = {"stage", "stage_objects", "constraints", "acceptance", "holes"}
    allowed = required | {"notes"}
    missing = sorted(required - set(sample))
    extra = sorted(set(sample) - allowed)
    if missing:
        errors.append(f"sample missing fields {missing}")
    if extra:
        errors.append(f"sample has unknown fields {extra}")
    if sample.get("stage") != stage.get("name"):
        errors.append(
            f"sample.stage {sample.get('stage')!r} does not match {stage.get('name')!r}")

    stage_objects = sample.get("stage_objects")
    if not isinstance(stage_objects, dict):
        errors.append("sample.stage_objects must be an object")
    else:
        expected_keys = {"manipulated", "target"}
        if set(stage_objects) != expected_keys:
            errors.append("sample.stage_objects must contain exactly manipulated and target")
        for key in expected_keys:
            value = stage_objects.get(key)
            if value is not None and value not in registry_ids:
                errors.append(f"sample.stage_objects.{key} is not a registry id: {value!r}")

    item_allowed = {
        "name", "args", "holds", "confidence", "evidence_frames", "provenance", "notes",
    }
    for field in ("constraints", "acceptance"):
        items = sample.get(field)
        if not isinstance(items, list):
            errors.append(f"sample.{field} must be a list")
            continue
        for index, item in enumerate(items):
            prefix = f"sample.{field}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be an object")
                continue
            missing_item = sorted(
                {"name", "args", "holds", "confidence", "evidence_frames"} - set(item))
            extra_item = sorted(set(item) - item_allowed)
            if missing_item:
                errors.append(f"{prefix} missing fields {missing_item}")
            if extra_item:
                errors.append(f"{prefix} has unknown fields {extra_item}")
            if not isinstance(item.get("args"), dict):
                errors.append(f"{prefix}.args must be an object")
            if item.get("holds") not in vocab.HOLDS_ALLOWED:
                errors.append(f"{prefix}.holds is outside the closed vocabulary")
            errors.extend(_confidence_errors(item, prefix))
            errors.extend(_evidence_errors(
                item, prefix, total_frames=total_frames,
                allowed_evidence_frames=allowed_evidence_frames))
            normalized = dict(item)
            normalized.setdefault("provenance", "demo_video")
            errors.extend(check_item(
                normalized, stage.get("index", -1), field, registry_ids=registry_ids))

    holes = sample.get("holes")
    if not isinstance(holes, list):
        errors.append("sample.holes must be a list")
    else:
        names = []
        for index, hole in enumerate(holes):
            errors.extend(_hole_errors(
                hole, f"sample.holes[{index}]", allow_votes=False))
            if isinstance(hole, dict) and isinstance(hole.get("name"), str):
                names.append(hole["name"])
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            errors.append(f"sample.holes has duplicate names {duplicates}")
    if "notes" in sample and not isinstance(sample["notes"], str):
        errors.append("sample.notes must be a string")
    return errors


def _is_finite_number(value) -> bool:
    return (not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value))


def validate_stage_manifest(stages, *, fps: float | None,
                            total_frames: int | None) -> list[str]:
    """Validate stage identity and temporal ordering before frame sampling."""
    if not isinstance(stages, list) or not stages:
        return ["stages.json must be a non-empty list"]
    errors: list[str] = []
    seen_indices: set[int] = set()
    previous_end: float | None = None
    duration = (total_frames / fps
                if (_is_finite_number(fps) and fps > 0
                    and isinstance(total_frames, int)
                    and not isinstance(total_frames, bool) and total_frames > 0)
                else None)
    for position, stage in enumerate(stages):
        prefix = f"stages.json[{position}]"
        if not isinstance(stage, dict):
            errors.append(f"{prefix} must be an object")
            continue
        index = stage.get("index")
        if (not isinstance(index, int) or isinstance(index, bool) or index < 0):
            errors.append(f"{prefix}.index must be a non-negative integer")
        elif index in seen_indices:
            errors.append(f"{prefix}.index is duplicated: {index}")
        else:
            seen_indices.add(index)
        if not isinstance(stage.get("name"), str) or not stage.get("name"):
            errors.append(f"{prefix}.name must be a non-empty string")
        if not isinstance(stage.get("label"), str):
            errors.append(f"{prefix}.label must be a string")
        start_sec, end_sec = stage.get("start_sec"), stage.get("end_sec")
        valid_window = (_is_finite_number(start_sec) and _is_finite_number(end_sec)
                        and 0 <= start_sec <= end_sec)
        if not valid_window:
            errors.append(f"{prefix}.start_sec/end_sec must be a valid time window")
            continue
        if previous_end is not None and start_sec < previous_end:
            errors.append(f"{prefix} overlaps or precedes the previous stage")
        if duration is not None and end_sec > duration + 1e-9:
            errors.append(f"{prefix}.end_sec exceeds video duration")
        previous_end = end_sec
    return errors


def validate_final_graph(graph, stages, registry_ids: set[str], *,
                         fps: float | None, total_frames: int | None
                         ) -> tuple[list[str], list[str], int]:
    """Validate final graph semantics against the complete stage manifest."""
    errors: list[str] = []
    warnings: list[str] = []
    n_items = 0

    if not isinstance(graph, dict):
        errors.append("graph.json must be an object")
        return errors, warnings, n_items
    graph_stages = graph.get("stages")
    if not isinstance(graph_stages, list):
        errors.append("graph.stages must be a list")
        return errors, warnings, n_items
    if not graph_stages:
        errors.append("graph.stages must not be empty")

    errors.extend(validate_stage_manifest(stages, fps=fps, total_frames=total_frames))
    reference_stages = stages if isinstance(stages, list) else []

    if len(graph_stages) != len(reference_stages):
        errors.append(
            "graph.stages must contain every stages.json entry "
            f"({len(graph_stages)} != {len(reference_stages)})")
    for position, (actual, expected) in enumerate(zip(graph_stages, reference_stages)):
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            continue
        actual_key = (actual.get("index"), actual.get("name"))
        expected_key = (expected.get("index"), expected.get("name"))
        if actual_key != expected_key:
            errors.append(
                f"graph.stages[{position}] index/name {actual_key!r} does not align "
                f"with stages.json {expected_key!r}")
        if actual.get("label") != expected.get("label"):
            errors.append(
                f"graph.stages[{position}].label does not align with stages.json")
        for field in ("start_sec", "end_sec"):
            actual_value, expected_value = actual.get(field), expected.get(field)
            if (not _is_finite_number(actual_value)
                    or not _is_finite_number(expected_value)
                    or not math.isclose(actual_value, expected_value,
                                        rel_tol=0.0, abs_tol=1e-9)):
                errors.append(
                    f"graph.stages[{position}].{field} does not align with stages.json")

    requested_k = graph.get("k")
    if (not isinstance(requested_k, int) or isinstance(requested_k, bool)
            or requested_k <= 0):
        errors.append("graph.k must be a positive integer")
        requested_k = None

    valid_fps = _is_finite_number(fps) and fps > 0
    for position, stage in enumerate(graph_stages):
        if not isinstance(stage, dict):
            errors.append(f"graph.stages[{position}] must be an object")
            continue
        idx = stage.get("index")
        prefix = f"s{idx}" if isinstance(idx, int) and not isinstance(idx, bool) \
            else f"graph.stages[{position}]"
        if not isinstance(idx, int) or isinstance(idx, bool):
            errors.append(f"{prefix}.index must be an integer")
        if not isinstance(stage.get("name"), str) or not stage.get("name"):
            errors.append(f"{prefix}.name must be a non-empty string")
        if not isinstance(stage.get("label"), str):
            errors.append(f"{prefix}.label must be a string")

        start_sec, end_sec = stage.get("start_sec"), stage.get("end_sec")
        valid_window = (_is_finite_number(start_sec) and _is_finite_number(end_sec)
                        and 0 <= start_sec <= end_sec)
        if not valid_window:
            errors.append(f"{prefix}.start_sec/end_sec must be a valid time window")
        lo = start_sec - 1.0 if valid_window else None
        hi = end_sec + 1.0 if valid_window else None

        k_valid = stage.get("k_valid")
        if (not isinstance(k_valid, int) or isinstance(k_valid, bool)
                or k_valid < 0):
            errors.append(f"{prefix}.k_valid must be a non-negative integer")
        elif requested_k is not None:
            if k_valid > requested_k:
                errors.append(f"{prefix}.k_valid must be in [0, {requested_k}]")
            elif k_valid < requested_k // 2 + 1:
                errors.append(
                    f"{prefix}: 有效 backend samples {k_valid}/{requested_k}，不足多数票")

        stage_objects = stage.get("stage_objects")
        if not isinstance(stage_objects, dict):
            errors.append(f"{prefix}: stage_objects 必须是对象")
            stage_objects = {}
        else:
            if set(stage_objects) != {"manipulated", "target"}:
                errors.append(
                    f"{prefix}: stage_objects 必须且只能包含 manipulated 和 target")
            for key in ("manipulated", "target"):
                value = stage_objects.get(key)
                if value is not None and value not in registry_ids:
                    errors.append(
                        f"{prefix}: stage_objects.{key} 未引用 object registry: {value!r}")

        semantic_lists: dict[str, list] = {}
        for field in ("constraints", "acceptance"):
            items = stage.get(field)
            if not isinstance(items, list):
                errors.append(f"{prefix}.{field} must be a list")
                continue
            semantic_lists[field] = items
            if not items:
                errors.append(f"{prefix}.{field} must not be empty")
            for item_index, item in enumerate(items):
                n_items += 1
                item_prefix = f"{prefix}.{field}[{item_index}]"
                if not isinstance(item, dict):
                    errors.append(f"{item_prefix} must be an object")
                    continue
                errors.extend(check_item(
                    item, idx if isinstance(idx, int) else position, field,
                    registry_ids=registry_ids))
                errors.extend(_confidence_errors(item, item_prefix))
                errors.extend(_evidence_errors(
                    item, item_prefix,
                    required=item.get("provenance") != "derived",
                    total_frames=total_frames))
                if item.get("holds") not in vocab.HOLDS_ALLOWED:
                    errors.append(
                        f"{item_prefix}.holds is outside the closed vocabulary")
                evidence = item.get("evidence_frames")
                if (item.get("provenance") != "derived"
                        and isinstance(evidence, list) and evidence
                        and all(isinstance(frame, int) and not isinstance(frame, bool)
                                for frame in evidence)
                        and valid_fps and valid_window
                        and all(not (lo <= frame / fps <= hi) for frame in evidence)):
                    warnings.append(
                        f"{prefix}.{field}: 时序错位? {item.get('name')} "
                        f"证据帧 {evidence} 全在阶段窗口外")

        holes = stage.get("holes")
        if not isinstance(holes, list):
            errors.append(f"{prefix}.holes must be a list")
            holes = []
        hole_names = []
        for hole_index, hole in enumerate(holes):
            errors.extend(_hole_errors(hole, f"{prefix}.holes[{hole_index}]"))
            if isinstance(hole, dict) and isinstance(hole.get("name"), str):
                hole_names.append(hole["name"])
        duplicate_holes = sorted(
            {name for name in hole_names if hole_names.count(name) > 1})
        if duplicate_holes:
            errors.append(f"{prefix}.holes: 重复 hole 名 {duplicate_holes}")

        constraints = semantic_lists.get("constraints", [])
        if (stage_objects.get("target") is not None
                and sum(1 for hole in holes
                        if isinstance(hole, dict) and hole.get("type") == "axis_3d") >= 2
                and not any(isinstance(item, dict)
                            and item.get("name") == "axis_parallel"
                            for item in constraints)):
            warnings.append(f"{prefix}: 装配缺口——axis_3d 洞≥2 但无 axis_parallel 约束")
    return errors, warnings, n_items


def validate_run_dir(run_dir: Path, task: str) -> dict:
    """Revalidate one explicit run directory and refresh ``validation.json``."""
    from ..demo.registry import validate_registry

    run_dir = Path(run_dir)
    artifacts.invalidate_outputs(run_dir, (
        "validation.json", "report.html", "stage_program.json", "policy.py",
        "compile_report.json", "compiled_graph.json", "compiled_objects.json",
    ))
    preflight_errors: list[str] = []

    def load(name: str):
        path = run_dir / name
        if not path.exists():
            preflight_errors.append(f"{name} 缺失")
            return None
        try:
            return artifacts.read_json(path)
        except (OSError, ValueError) as error:
            preflight_errors.append(f"{name} 无法读取: {error}")
            return None

    graph = load("graph.json")
    stages = load("stages.json")
    meta = load("meta.json")
    objects = load("objects.json")

    video = meta.get("video") if isinstance(meta, dict) else None
    if not isinstance(video, dict):
        preflight_errors.append("meta.json.video must be an object")
        video = {}
    fps = video.get("fps")
    total_frames = video.get("total_frames")
    if not _is_finite_number(fps) or fps <= 0:
        preflight_errors.append("meta.json.video.fps must be positive")
        fps = None
    if (not isinstance(total_frames, int) or isinstance(total_frames, bool)
            or total_frames <= 0):
        preflight_errors.append("meta.json.video.total_frames must be a positive integer")
        total_frames = None

    registry_ids: set[str] = set()
    if objects is not None and total_frames is not None:
        preflight_errors.extend(
            f"objects.json: {error}"
            for error in validate_registry(objects, total_frames)
        )
        if isinstance(objects, list):
            registry_ids = {
                obj.get("id") for obj in objects
                if isinstance(obj, dict) and isinstance(obj.get("id"), str)
            }

    errors, warnings, n_items = validate_final_graph(
        graph, stages, registry_ids, fps=fps, total_frames=total_frames)
    errors = [*preflight_errors, *errors]
    result = {"task": task, "items_checked": n_items,
              "violations": errors, "warnings": warnings, "passed": not errors}
    artifacts.write_json(run_dir / "validation.json", result)
    print(f"[validate] {task}: {n_items} items, {len(errors)} violations, "
          f"{len(warnings)} warnings -> {'PASS' if result['passed'] else 'FAIL'}")
    return result


def run(task: str) -> dict:
    return validate_run_dir(artifacts.latest_run_dir(task), task)
