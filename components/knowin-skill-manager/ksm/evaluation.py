from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .feedback_attribution import current_or_rebuilt_attribution


def analyze_episode_failure(
    *,
    failure_signature: str,
    policy_ok: bool,
    execute: bool,
    run_result: Any | None,
    run_error: str | None,
) -> dict[str, Any]:
    status = run_result.final_status if run_result is not None else {}
    status_text = json.dumps(status, ensure_ascii=False)
    failed_actions = _failed_actions(status)
    primary_signal = _primary_signal(
        failure_signature=failure_signature,
        status_text=status_text,
        run_error=run_error,
    )
    return {
        "schema": "ksm.aspire_kw.failure_analysis.v1",
        "category": _category(failure_signature),
        "stage": _stage(
            failure_signature=failure_signature,
            policy_ok=policy_ok,
            execute=execute,
            run_result=run_result,
            run_error=run_error,
        ),
        "primary_signal": primary_signal,
        "failed_actions": failed_actions,
        "service_related": _service_related(failure_signature, status_text, run_error),
        "action_related": bool(failed_actions),
        "recommended_focus": _recommended_focus(failure_signature, failed_actions),
    }


def summarize_episode_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    categories = Counter()
    stages = Counter()
    failed_actions = Counter()
    focus = Counter()
    trace_failures = Counter()
    subgoal_failures = Counter()
    evidence_gaps = Counter()
    summaries = Counter()
    for report in reports:
        analysis = report.get("failure_analysis") if isinstance(report.get("failure_analysis"), dict) else {}
        categories[str(analysis.get("category") or "unknown")] += 1
        stages[str(analysis.get("stage") or "unknown")] += 1
        focus[str(analysis.get("recommended_focus") or "unknown")] += 1
        for action in analysis.get("failed_actions") or []:
            failed_actions[str(action)] += 1
        attribution = current_or_rebuilt_attribution(report)
        trace_failures.update(attribution.get("trace_failure_breakdown") or {})
        subgoal_failures.update(attribution.get("subgoal_failure_breakdown") or {})
        evidence_gaps.update({str(item): 1 for item in attribution.get("evidence_gaps") or []})
        summary = str(attribution.get("recurring_summary") or "")
        if summary:
            summaries.update([summary])
    return {
        "schema": "ksm.aspire_kw.evaluation_summary.v1",
        "num_reports": len(reports),
        "category_breakdown": dict(sorted(categories.items())),
        "stage_breakdown": dict(sorted(stages.items())),
        "recommended_focus_breakdown": dict(sorted(focus.items())),
        "trace_failure_breakdown": dict(sorted(trace_failures.items())),
        "subgoal_failure_breakdown": dict(sorted(subgoal_failures.items())),
        "evidence_gap_breakdown": dict(sorted(evidence_gaps.items())),
        "recurring_summaries": dict(summaries.most_common(8)),
        "top_failed_actions": [
            {"action": action, "count": count}
            for action, count in failed_actions.most_common(8)
        ],
    }


def _category(failure_signature: str) -> str:
    if failure_signature in ("success",):
        return "success"
    if failure_signature in ("effect_feedback_missing",):
        return "feedback"
    if failure_signature in ("verifier_failed", "verifier_inconclusive"):
        return "verifier"
    if failure_signature in ("predicate_failed",):
        return "predicate"
    if failure_signature in ("dry_run",):
        return "not_executed"
    if failure_signature in ("static_policy_violation", "runtime_output_binding_failed"):
        return "yaml_contract"
    if failure_signature in ("vision_grounding_failed", "frame_capture_failed"):
        return "perception"
    if failure_signature in ("reasoning_unauthorized",):
        return "service_auth"
    if failure_signature in ("motion_planning_failed",):
        return "motion"
    if failure_signature in ("execution_exception", "missing_run_result"):
        return "runtime"
    return "skill_execution"


def _stage(
    *,
    failure_signature: str,
    policy_ok: bool,
    execute: bool,
    run_result: Any | None,
    run_error: str | None,
) -> str:
    if not policy_ok:
        return "static_policy"
    if not execute:
        return "dry_run"
    if run_error:
        return "pipeline_submission"
    if run_result is None:
        return "pipeline_result"
    if failure_signature == "predicate_failed":
        return "verify_predicate"
    if failure_signature in ("verifier_failed", "verifier_inconclusive"):
        return "verify_skill_effect"
    if failure_signature == "effect_feedback_missing":
        return "effect_feedback"
    if failure_signature == "success":
        return "completed"
    if failure_signature in ("vision_grounding_failed", "frame_capture_failed"):
        return "perception"
    if failure_signature == "motion_planning_failed":
        return "motion"
    return "skill_runtime"


def _primary_signal(*, failure_signature: str, status_text: str, run_error: str | None) -> str:
    if run_error:
        return run_error[:500]
    lowered = status_text.lower()
    markers = [
        "failed to fetch current frame",
        "cannot find the object",
        "grounding",
        "not defined",
        "nameerror",
        "motion planning failed",
        "motion_planning failed",
        "401",
        "unauthorized",
    ]
    for marker in markers:
        if marker in lowered:
            return marker
    return failure_signature


def _service_related(failure_signature: str, status_text: str, run_error: str | None) -> bool:
    text = f"{failure_signature} {status_text} {run_error or ''}".lower()
    return any(marker in text for marker in ("401", "unauthorized", "timeout", "service", "failed to fetch current frame"))


def _recommended_focus(failure_signature: str, failed_actions: list[str]) -> str:
    if failure_signature == "dry_run":
        return "execute_or_admit"
    if failure_signature == "static_policy_violation":
        return "yaml_policy"
    if failure_signature == "runtime_output_binding_failed":
        return "output_binding"
    if failure_signature in ("vision_grounding_failed", "frame_capture_failed"):
        return "perception_dependency"
    if failure_signature == "reasoning_unauthorized":
        return "service_configuration"
    if failure_signature == "motion_planning_failed":
        return "motion_strategy"
    if failure_signature == "predicate_failed":
        return "observable_condition_mapping"
    if failure_signature in ("verifier_failed", "verifier_inconclusive"):
        return "kw_verifier_result"
    if failure_signature == "effect_feedback_missing":
        return "add_kw_predicate_or_verifier_contract"
    if failed_actions:
        return "failed_action_mechanism"
    return "runtime_evidence"


def _failed_actions(status: dict[str, Any]) -> list[str]:
    actions: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            step = value.get("step")
            if str(value.get("status") or "").lower() == "failed" and isinstance(step, dict):
                action = step.get("action")
                if isinstance(action, str):
                    actions.append(action)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(status)
    return list(dict.fromkeys(actions))
