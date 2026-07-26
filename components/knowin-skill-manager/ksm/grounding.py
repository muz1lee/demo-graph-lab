from __future__ import annotations

import ast
from typing import Any

from .config import ManagerConfig
from .runner import PipelineDirectClient


def preflight_runtime_skill_args(
    *,
    config: ManagerConfig,
    task_metadata: dict[str, Any],
    runtime_args: dict[str, Any],
    enabled: bool,
    client: PipelineDirectClient | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "enabled": bool(enabled),
        "status": "skipped" if not enabled else "ok",
        "original_skill_args": dict(runtime_args),
        "skill_args": dict(runtime_args),
        "checks": {},
        "overrides": {},
    }
    if not enabled:
        return report
    robodojo = _robodojo_metadata(task_metadata)
    if not robodojo:
        report["status"] = "skipped"
        report["reason"] = "missing_robodojo_metadata"
        return report
    if config.pipeline.mode != "direct":
        report["status"] = "skipped"
        report["reason"] = f"unsupported_pipeline_mode={config.pipeline.mode}"
        return report

    binding = _dict(robodojo.get("binding"))
    runtime_client = client or PipelineDirectClient(config.pipeline.base_url, timeout_s=12.0)

    if apply_stateful_plan_preflight(report=report, robodojo=robodojo, skill_args=runtime_args, client=runtime_client):
        _finalize_preflight_status(report, override_status="overrode_unverified_runtime_args")
        return report

    pick_labels = _dedupe(
        [runtime_args.get("pick_label"), binding.get("primary_pick_label")]
        + list(binding.get("candidate_pick_labels") or [])
        + list(_dict(binding.get("asset_visual_hints")).get("preferred_labels") or [])
    )
    pick_position = _position(_dict(robodojo.get("target_import")))
    if pick_position is not None and pick_labels:
        pick_report = validate_label_arg(
            client=runtime_client,
            current_label=runtime_args.get("pick_label"),
            target_position=pick_position,
            candidate_labels=pick_labels,
            target_ref=str(robodojo.get("target_object") or binding.get("target_ref") or ""),
        )
        report["checks"]["pick_label"] = pick_report
        _apply_selected_label(report, "pick_label", pick_report)
    else:
        report["checks"]["pick_label"] = {"status": "skipped", "reason": "missing_pick_position_or_labels"}

    place_labels = _dedupe(
        [runtime_args.get("place_label"), binding.get("primary_place_label")]
        + list(binding.get("candidate_place_labels") or [])
    )
    place_position = _position(_dict(robodojo.get("place_import")))
    if place_position is not None and place_labels:
        place_report = validate_label_arg(
            client=runtime_client,
            current_label=runtime_args.get("place_label"),
            target_position=place_position,
            candidate_labels=place_labels,
            target_ref=str(binding.get("place_ref") or binding.get("support_ref") or _dict(robodojo.get("place_asset")).get("id") or ""),
        )
        report["checks"]["place_label"] = place_report
        _apply_selected_label(report, "place_label", place_report)
    else:
        report["checks"]["place_label"] = {"status": "skipped", "reason": "missing_place_position_or_labels"}

    _finalize_preflight_status(report, override_status="overrode_unverified_runtime_args")
    return report


def apply_stateful_plan_preflight(
    *,
    report: dict[str, Any],
    robodojo: dict[str, Any],
    skill_args: dict[str, Any],
    client: PipelineDirectClient,
) -> bool:
    plan = stateful_plan_from_robodojo(robodojo)
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps:
        return False
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        bindings = _dict(step.get("arg_bindings"))
        pick_arg = str(bindings.get("pick_label") or f"pick_label_{index}").strip()
        place_arg = str(bindings.get("place_label") or f"place_label_{index}").strip()
        pick_labels = _dedupe(
            [skill_args.get(pick_arg), step.get("primary_pick_label")]
            + list(step.get("candidate_pick_labels") or [])
        )
        pick_position = _position(_dict(step.get("source_import")))
        if pick_arg and pick_position is not None and pick_labels:
            pick_report = validate_label_arg(
                client=client,
                current_label=skill_args.get(pick_arg),
                target_position=pick_position,
                candidate_labels=pick_labels,
                target_ref=str(step.get("source_object") or ""),
            )
            report["checks"][pick_arg] = pick_report
            _apply_selected_label(report, pick_arg, pick_report)
        elif pick_arg:
            report["checks"][pick_arg] = {"status": "skipped", "reason": "missing_stateful_pick_position_or_labels"}

        place_labels = _dedupe(
            [skill_args.get(place_arg), step.get("primary_place_label")]
            + list(step.get("candidate_place_labels") or [])
        )
        place_position = _position(_dict(step.get("support_import")))
        if place_arg and place_position is not None and place_labels:
            place_report = validate_label_arg(
                client=client,
                current_label=skill_args.get(place_arg),
                target_position=place_position,
                candidate_labels=place_labels,
                target_ref=str(step.get("support_object") or ""),
            )
            report["checks"][place_arg] = place_report
            _apply_selected_label(report, place_arg, place_report)
        elif place_arg:
            report["checks"][place_arg] = {"status": "skipped", "reason": "missing_stateful_place_position_or_labels"}
    report["stateful_plan"] = {
        "schema": plan.get("schema"),
        "plan_type": plan.get("plan_type"),
        "relation": plan.get("relation"),
        "step_count": len(steps),
        "object_order": plan.get("object_order"),
    }
    return True


def stateful_plan_from_robodojo(robodojo: dict[str, Any]) -> dict[str, Any]:
    subtask = _dict(robodojo.get("subtask"))
    plan = _dict(subtask.get("stateful_plan"))
    if plan:
        return plan
    binding = _dict(robodojo.get("binding"))
    return _dict(binding.get("stateful_plan"))


def validate_label_arg(
    *,
    client: PipelineDirectClient,
    current_label: Any,
    target_position: tuple[float, float, float],
    candidate_labels: list[str],
    target_ref: str,
) -> dict[str, Any]:
    current = str(current_label or "").strip()
    labels = _dedupe(([current] if current else []) + candidate_labels)
    attempts = [grounding_attempt(client=client, label=label, target_position=target_position) for label in labels[:10]]
    current_attempt = next((attempt for attempt in attempts if attempt.get("label") == current), None)
    selected_attempt = choose_grounded_label(attempts)
    current_ok = grounding_attempt_matches_target(current_attempt)
    selected_ok = grounding_attempt_matches_target(selected_attempt)
    selected_label = current if current_ok else (selected_attempt.get("label") if selected_ok and selected_attempt else current)
    if current_ok:
        status = "ok"
        reason = "runtime_arg_grounded_near_target"
    elif selected_ok and selected_attempt:
        status = "overrode"
        reason = "runtime_arg_not_grounded_near_target"
    else:
        status = "failed"
        reason = "no_label_grounded_near_target"
    return {
        "status": status,
        "target_ref": target_ref,
        "target_position": list(target_position),
        "current_label": current,
        "selected_label": selected_label,
        "reason": reason,
        "attempts": attempts,
    }


def grounding_attempt(*, client: PipelineDirectClient, label: str, target_position: tuple[float, float, float]) -> dict[str, Any]:
    kwargs = {"text": [label], "offsets": [[0, 0, 0.07]]}
    attempt: dict[str, Any] = {"label": label, "success": False, "kwargs": kwargs}
    try:
        response = client.run_reasoning("qwen_xquat", kwargs)
    except Exception as exc:
        attempt["error"] = repr(exc)
        return attempt
    parsed = parse_reasoning_result(response)
    attempt["response_ok"] = bool(response.get("ok", True)) if isinstance(response, dict) else None
    attempt["status"] = parsed.get("status")
    attempt["xyz"] = parsed.get("xyz")
    attempt["success"] = bool(parsed.get("success"))
    xyz = parsed.get("xyz")
    if isinstance(xyz, list) and len(xyz) >= 2:
        attempt["xy_distance_m"] = ((float(xyz[0]) - target_position[0]) ** 2 + (float(xyz[1]) - target_position[1]) ** 2) ** 0.5
    return attempt


def parse_reasoning_result(response: dict[str, Any]) -> dict[str, Any]:
    payload: Any = response
    if isinstance(response.get("response"), dict):
        payload = response["response"]
    raw_result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(raw_result, str):
        try:
            raw_result = ast.literal_eval(raw_result)
        except Exception:
            return {"success": False, "status": "parse_failed"}
    if not isinstance(raw_result, dict):
        return {"success": False, "status": "missing_result"}
    statuses = raw_result.get("status")
    xquats = raw_result.get("xquats")
    status = statuses[0] if isinstance(statuses, list) and statuses else None
    xyz = None
    if isinstance(xquats, list) and xquats and isinstance(xquats[0], list) and xquats[0]:
        first = xquats[0][0]
        if isinstance(first, list) and len(first) >= 3:
            xyz = [float(first[0]), float(first[1]), float(first[2])]
    return {"success": status == "Success" and xyz is not None, "status": status, "xyz": xyz}


def choose_grounded_label(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    successes = [attempt for attempt in attempts if attempt.get("success")]
    if not successes:
        return None
    with_distance = [attempt for attempt in successes if isinstance(attempt.get("xy_distance_m"), (int, float))]
    if with_distance:
        best_distance = min(float(attempt["xy_distance_m"]) for attempt in with_distance)
        near_best = [
            attempt
            for attempt in with_distance
            if float(attempt["xy_distance_m"]) <= best_distance + 0.02
        ]
        return max(
            near_best,
            key=lambda attempt: (_label_specificity_score(str(attempt.get("label") or "")), -float(attempt["xy_distance_m"])),
        )
    return max(successes, key=lambda attempt: _label_specificity_score(str(attempt.get("label") or "")))


def _label_specificity_score(label: str) -> int:
    text = label.strip().lower()
    normalized = text.replace(":dof", "").replace("_", " ").strip()
    if not normalized:
        return -10
    generic = {
        "object",
        "target object",
        "block",
        "target block",
        "bottle",
        "target bottle",
        "can",
        "target can",
        "cup",
        "target cup",
        "box",
        "target box",
        "bowl",
        "target bowl",
        "积木",
        "目标积木",
        "瓶子",
        "目标瓶子",
        "易拉罐",
        "杯子",
        "盒子",
        "碗",
    }
    if normalized in generic:
        return 0
    score = 1
    color_terms = (
        "red", "orange", "yellow", "green", "blue", "purple", "pink", "white", "black", "gray", "grey", "brown",
        "红", "橙", "黄", "绿", "蓝", "紫", "粉", "白", "黑", "灰", "棕",
    )
    if any(term in text for term in color_terms):
        score += 5
    if ":dof" in text:
        score += 1
    if "_prop" in text or normalized.startswith("block ") and normalized.split()[-1].isdigit():
        score -= 1
    return score


def grounding_attempt_matches_target(attempt: dict[str, Any] | None, *, max_xy_distance_m: float = 0.12) -> bool:
    if not attempt or not attempt.get("success"):
        return False
    distance = attempt.get("xy_distance_m")
    if isinstance(distance, (int, float)):
        return float(distance) <= max_xy_distance_m
    return True


def _apply_selected_label(report: dict[str, Any], key: str, check: dict[str, Any]) -> None:
    selected = check.get("selected_label")
    original = report["original_skill_args"].get(key)
    if selected and selected != original:
        report["skill_args"][key] = selected
        report["overrides"][key] = {"from": original, "to": selected, "reason": check.get("reason")}


def _robodojo_metadata(task_metadata: dict[str, Any]) -> dict[str, Any]:
    raw = _dict(task_metadata.get("raw"))
    return _dict(raw.get("robodojo"))


def _position(import_item: dict[str, Any]) -> tuple[float, float, float] | None:
    pose = _dict(import_item.get("pose"))
    pos = pose.get("position")
    if isinstance(pos, list) and len(pos) >= 3:
        try:
            return float(pos[0]), float(pos[1]), float(pos[2])
        except (TypeError, ValueError):
            return None
    return None


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dedupe(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _finalize_preflight_status(report: dict[str, Any], *, override_status: str) -> None:
    failed = [key for key, check in dict(report.get("checks") or {}).items() if isinstance(check, dict) and check.get("status") == "failed"]
    if failed:
        report["failed_checks"] = failed
        report["status"] = "failed_with_overrides" if report.get("overrides") else "failed"
    elif report.get("overrides"):
        report["status"] = override_status
