from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any


ATTRIBUTION_SCHEMA = "ksm.aspire_kw.feedback_attribution.v1"
ATTRIBUTION_VERSION = 2
MEMORY_SCHEMA = "ksm.aspire_kw.feedback_memory.v1"
TASK_ANALYSIS_SCHEMA = "ksm.aspire_kw.task_analysis_state.v1"
AGENT_FEEDBACK_SCHEMA = "ksm.aspire_kw.agent_feedback.v1"

EVALUATOR_ONLY_KEYS = {
    "confirmed_success",
    "debug_feedback",
    "effect_success",
    "effect_success_rate",
    "evaluator_feedback",
    "failure_analysis",
    "predicate_report",
    "predicate_report_inline",
    "predicate_report_status",
    "predicate_success",
    "predicate_success_rate",
    "predicates",
    "success",
    "success_evidence_level",
    "success_rate",
    "task_completed",
    "task_success",
    "task_success_rate",
    "verification",
    "verifier_success",
    "visual_task_success",
}
EVALUATOR_ONLY_TEXT_TOKENS = (
    "ground_truth",
    "ground-truth",
    "groundtruth",
    "predicate",
    "predicate_success",
    "verifier_success",
)
EVALUATOR_ONLY_MODE_TOKENS = (
    "category:predicate",
    "category:verifier",
    "failure_signature:effect_feedback_missing",
    "failure_signature:predicate_failed",
    "failure_signature:verifier_failed",
    "failure_signature:verifier_inconclusive",
    "kw_verifier_failed",
    "missing_effect_verification",
    "stage:verify_predicate",
    "stage:verify_skill_effect",
)
AGENT_STATIC_TAXONOMY_KEYS = {
    "eliminated_directions",
    "failed_strategy_families",
    "failure_breakdown",
    "failure_key",
    "inferred_failure_modes",
    "observable_failure_signature",
    "repair_focus",
    "strategy_features",
    "subgoal_failure_breakdown",
    "trace_failure_breakdown",
}


def confirmed_success_evidence(report: dict[str, Any]) -> bool:
    """Return true only when success is backed by task/effect evidence."""

    return any(
        report.get(key) is True
        for key in (
            "task_success",
            "effect_success",
            "predicate_success",
            "verifier_success",
        )
    )


def success_evidence_level(report: dict[str, Any]) -> str:
    for key in ("task_success", "effect_success", "predicate_success", "verifier_success"):
        if report.get(key) is True:
            return key
    if report.get("success") is True:
        return "reported_success_without_effect_evidence"
    if report.get("pipeline_success") is True or report.get("skill_success") is True:
        return "pipeline_only"
    return "none"


def untrusted_reported_success(report: dict[str, Any]) -> bool:
    return bool(report.get("success") is True and not confirmed_success_evidence(report))


def analyze_episode_report(report: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw episode report into ASPIRE-style repair evidence.

    This layer intentionally keeps both taxonomy-style labels and open-ended
    summaries. If a failure is not covered by known buckets, the raw action
    sequence and recurring summary still remain available to the next prompt.
    """

    metadata = _dict(report.get("metadata"))
    run_result = _dict(metadata.get("run_result"))
    final_status = _dict(run_result.get("final_status"))
    action_timeline = extract_action_timeline(final_status)
    failed_actions = [
        item["action"]
        for item in action_timeline
        if item.get("status") == "failed" and item.get("action")
    ]
    first_failed_action = failed_actions[0] if failed_actions else None
    visual_evidence = _dict(metadata.get("visual_evidence"))
    trace_analysis = _dict(metadata.get("trace_analysis"))
    runtime_delta = runtime_arg_delta(
        task_args=_dict(metadata.get("task_args")),
        candidate_args=_dict(metadata.get("candidate_skill_args")),
        runtime_args=_dict(metadata.get("runtime_args")),
    )
    modes = infer_failure_modes(
        report=report,
        action_timeline=action_timeline,
        visual_evidence=visual_evidence,
        trace_analysis=trace_analysis,
    )
    subgoals = infer_subgoal_failures(report=report, modes=modes)
    evidence_gaps = infer_evidence_gaps(
        report=report,
        action_timeline=action_timeline,
        visual_evidence=visual_evidence,
        trace_analysis=trace_analysis,
    )
    summary = recurring_summary(
        report=report,
        first_failed_action=first_failed_action,
        modes=modes,
        visual_evidence=visual_evidence,
        evidence_gaps=evidence_gaps,
    )
    candidate_manifest = _dict(metadata.get("candidate_manifest"))
    negative = negative_evidence_entry(
        report=report,
        modes=modes,
        failed_actions=failed_actions,
        runtime_delta=runtime_delta,
        candidate_manifest=candidate_manifest,
        summary=summary,
    )
    confirmed_success = confirmed_success_evidence(report)
    untrusted_success = untrusted_reported_success(report)
    agent_feedback = build_agent_feedback(
        report=report,
        action_timeline=action_timeline,
        failed_actions=failed_actions,
        first_failed_action=first_failed_action,
        visual_evidence=visual_evidence,
        trace_analysis=trace_analysis,
        runtime_delta=runtime_delta,
        modes=modes,
        subgoals=subgoals,
        evidence_gaps=evidence_gaps,
    )
    return {
        "schema": ATTRIBUTION_SCHEMA,
        "version": ATTRIBUTION_VERSION,
        "candidate_id": report.get("candidate_id"),
        "task_id": report.get("task_id"),
        "success": confirmed_success,
        "reported_success": bool(report.get("success")),
        "confirmed_success": confirmed_success,
        "success_evidence_level": success_evidence_level(report),
        "untrusted_success_evidence": untrusted_success,
        "task_success": report.get("task_success") is True,
        "execution_success": bool(report.get("execution_success", report.get("pipeline_success"))),
        "effect_success": report.get("effect_success"),
        "pipeline_success": bool(report.get("pipeline_success")),
        "skill_success": bool(report.get("skill_success")),
        "predicate_success": report.get("predicate_success"),
        "verifier_success": report.get("verifier_success"),
        "verification": report.get("verification"),
        "failure_signature": report.get("failure_signature"),
        "failure_analysis": report.get("failure_analysis"),
        "action_timeline": action_timeline,
        "failed_actions": list(dict.fromkeys(failed_actions)),
        "first_failed_action": first_failed_action,
        "first_failed_trace_context": first_failed_trace_context(action_timeline),
        "runtime_arg_delta": runtime_delta,
        "visual_feedback": visual_feedback_summary(visual_evidence),
        "trace_analysis": trace_analysis,
        "trace_failure_breakdown": _count_modes(modes),
        "subgoal_failure_breakdown": _count_modes(subgoals),
        "rich_trace_feature_counts": trace_analysis.get("rich_trace_feature_counts") or {},
        "visual_effect_probes": trace_analysis.get("visual_effect_probes") or [],
        "inferred_failure_modes": modes,
        "recurring_summary": summary,
        "evidence_gaps": evidence_gaps,
        "negative_evidence": negative,
        "repair_focus": repair_focus(
            modes=modes,
            failed_actions=failed_actions,
            evidence_gaps=evidence_gaps,
            report=report,
        ),
        "agent_feedback": agent_feedback,
    }


def build_feedback_memory(reports: list[dict[str, Any]]) -> dict[str, Any]:
    attributions = [
        _current_or_rebuilt_attribution(report)
        for report in reports
        if isinstance(report, dict)
    ]
    if len(attributions) != len(reports):
        attributions = [
            _current_or_rebuilt_attribution(report)
            for report in reports
        ]

    trace_counts: Counter[str] = Counter()
    subgoal_counts: Counter[str] = Counter()
    gap_counts: Counter[str] = Counter()
    summary_counts: Counter[str] = Counter()
    candidate_states: list[dict[str, Any]] = []
    for attr in attributions:
        agent = _dict(attr.get("agent_feedback"))
        trace_counts.update(agent.get("trace_failure_breakdown") or attr.get("trace_failure_breakdown") or {})
        subgoal_counts.update(agent.get("subgoal_failure_breakdown") or attr.get("subgoal_failure_breakdown") or {})
        gap_counts.update({str(item): 1 for item in agent.get("evidence_gaps") or []})
        summary = str(agent.get("recurring_summary") or attr.get("recurring_summary") or "")
        if summary:
            summary_counts.update([summary])
        candidate_states.append(candidate_state_from_attribution(attr))

    return {
        "schema": MEMORY_SCHEMA,
        "num_reports": len(reports),
        "candidate_states": candidate_states,
        "trace_failure_breakdown": dict(sorted(trace_counts.items())),
        "subgoal_failure_breakdown": dict(sorted(subgoal_counts.items())),
        "evidence_gap_breakdown": dict(sorted(gap_counts.items())),
        "recurring_summaries": dict(summary_counts.most_common(8)),
        "open_questions": open_questions(candidate_states),
        "evidence_gaps": evidence_gaps_from_states(candidate_states),
    }


def current_or_rebuilt_attribution(report: dict[str, Any]) -> dict[str, Any]:
    existing = report.get("feedback_attribution")
    if isinstance(existing, dict) and existing.get("version") == ATTRIBUTION_VERSION:
        return existing
    if isinstance(existing, dict) and not _has_rebuildable_raw_evidence(report):
        return existing
    return analyze_episode_report(report)


def _current_or_rebuilt_attribution(report: dict[str, Any]) -> dict[str, Any]:
    return current_or_rebuilt_attribution(report)


def _has_rebuildable_raw_evidence(report: dict[str, Any]) -> bool:
    metadata = _dict(report.get("metadata"))
    return any(
        key in metadata
        for key in (
            "run_result",
            "visual_evidence",
            "trace_analysis",
            "task_args",
            "candidate_skill_args",
            "runtime_args",
        )
    )


def merge_feedback_memories(items: list[dict[str, Any]]) -> dict[str, Any]:
    trace_counts: Counter[str] = Counter()
    subgoal_counts: Counter[str] = Counter()
    gap_counts: Counter[str] = Counter()
    summary_counts: Counter[str] = Counter()
    states: list[dict[str, Any]] = []
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        total += int(item.get("num_reports") or 0)
        trace_counts.update(item.get("trace_failure_breakdown") or {})
        subgoal_counts.update(item.get("subgoal_failure_breakdown") or {})
        gap_counts.update(item.get("evidence_gap_breakdown") or {})
        summary_counts.update(item.get("recurring_summaries") or {})
        states.extend(item.get("candidate_states") or [])
    return {
        "schema": MEMORY_SCHEMA,
        "num_reports": total,
        "candidate_states": states,
        "trace_failure_breakdown": dict(sorted(trace_counts.items())),
        "subgoal_failure_breakdown": dict(sorted(subgoal_counts.items())),
        "evidence_gap_breakdown": dict(sorted(gap_counts.items())),
        "recurring_summaries": dict(summary_counts.most_common(8)),
        "open_questions": open_questions(states),
        "evidence_gaps": evidence_gaps_from_states(states),
    }


def build_task_analysis_state(
    *,
    suite_id: str,
    task_ids: list[str] | None = None,
    stage: str = "suite_run",
    manifest_path: str | None = None,
    run_dir: str | None = None,
    success_threshold: float = 1.0,
    reports: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    feedback_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ASPIRE-style task analysis state from KSM episode evidence."""

    report_items = reports or []
    memory = feedback_memory if isinstance(feedback_memory, dict) else build_feedback_memory(report_items)
    states = task_candidate_states(
        candidates=candidates or [],
        reports=report_items,
        feedback_memory=memory,
        stage=stage,
        success_threshold=float(success_threshold),
    )
    best = states[0] if states else None
    ids = [str(item) for item in (task_ids or []) if str(item)]
    return {
        "schema": TASK_ANALYSIS_SCHEMA,
        "version": 1,
        "updated_at": _now(),
        "suite_id": suite_id,
        "task_id": ids[0] if len(ids) == 1 else suite_id,
        "task_ids": ids,
        "stage": stage,
        "manifest_path": manifest_path,
        "run_dir": run_dir,
        "debug_seeds": [],
        "heldout_seeds": [],
        "success_threshold": float(success_threshold),
        "best_candidate": best,
        "candidate_states": states,
        "eliminated_directions": eliminated_directions(states),
        "visual_geometry_conclusions": [],
        "visual_effect_conclusions": [],
        "auto_effect_probes": [],
        "blocked_untested_directions": [],
        "open_questions": memory.get("open_questions") or open_questions(states),
        "evidence_gaps": memory.get("evidence_gaps") or evidence_gaps_from_states(states),
        "retrieved_negative_evidence": negative_evidence_from_states(states),
        "trace_failure_breakdown": memory.get("trace_failure_breakdown") or {},
        "subgoal_failure_breakdown": memory.get("subgoal_failure_breakdown") or {},
        "evidence_gap_breakdown": memory.get("evidence_gap_breakdown") or {},
        "recurring_summaries": memory.get("recurring_summaries") or {},
    }


def task_candidate_states(
    *,
    candidates: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    feedback_memory: dict[str, Any],
    stage: str,
    success_threshold: float,
) -> list[dict[str, Any]]:
    reports_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        candidate_id = str(report.get("candidate_id") or "unknown")
        reports_by_candidate.setdefault(candidate_id, []).append(report)

    memory_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for state in feedback_memory.get("candidate_states") or []:
        if isinstance(state, dict):
            candidate_id = str(state.get("candidate_id") or "unknown")
            memory_by_candidate.setdefault(candidate_id, []).append(state)

    states: list[dict[str, Any]] = []
    if candidates:
        for rank, candidate in enumerate(candidates, start=1):
            candidate_id = str(candidate.get("candidate_id") or "unknown")
            states.append(
                task_candidate_state(
                    rank=rank,
                    candidate=candidate,
                    reports=reports_by_candidate.get(candidate_id) or [],
                    memory_states=memory_by_candidate.get(candidate_id) or [],
                    stage=stage,
                    success_threshold=success_threshold,
                )
            )
    else:
        for rank, memory_state in enumerate(feedback_memory.get("candidate_states") or [], start=1):
            if isinstance(memory_state, dict):
                states.append(
                    task_candidate_state(
                        rank=rank,
                        candidate={},
                        reports=reports_by_candidate.get(str(memory_state.get("candidate_id") or "unknown")) or [],
                        memory_states=[memory_state],
                        stage=stage,
                        success_threshold=success_threshold,
                    )
                )
    return states


def task_candidate_state(
    *,
    rank: int,
    candidate: dict[str, Any],
    reports: list[dict[str, Any]],
    memory_states: list[dict[str, Any]],
    stage: str,
    success_threshold: float,
) -> dict[str, Any]:
    sample_report = reports[0] if reports else {}
    manifest = _dict(_dict(sample_report.get("metadata")).get("candidate_manifest"))
    candidate_id = str(candidate.get("candidate_id") or manifest.get("candidate_id") or _first_value(memory_states, "candidate_id") or "unknown")
    trials = int(candidate.get("num_trials") or len(reports) or len(memory_states))
    attribution_items = [
        current_or_rebuilt_attribution(report)
        for report in reports
    ]
    has_direct_evidence = bool(attribution_items or memory_states)
    completed = int(candidate.get("task_completed") or sum(1 for state in memory_states if state.get("success")))
    if attribution_items:
        completed = sum(1 for item in attribution_items if item.get("success"))
    elif memory_states:
        completed = sum(1 for state in memory_states if state.get("success"))
    untrusted_success = any(item.get("untrusted_success_evidence") for item in attribution_items) or any(
        str(state.get("status") or "") == "untrusted_success_evidence" for state in memory_states
    )
    if has_direct_evidence:
        success_rate = float(completed) / trials if trials else 0.0
    else:
        success_rate = (
            float(candidate.get("success_rate"))
            if candidate.get("success_rate") is not None
            else (float(completed) / trials if trials else 0.0)
        )
    success = bool(trials > 0 and success_rate >= success_threshold)
    trace_summary = _most_common_text(candidate.get("recurring_summaries"))
    if not trace_summary:
        trace_summary = str(_first_value(memory_states, "recurring_summary") or "")
    negative_items = [
        item.get("negative_evidence")
        for item in attribution_items
        if isinstance(item.get("negative_evidence"), dict) and item.get("negative_evidence")
    ]
    if not negative_items:
        negative_items = [
            state.get("negative_evidence")
            for state in memory_states
            if isinstance(state.get("negative_evidence"), dict) and state.get("negative_evidence")
        ]
    return {
        "rank": rank,
        "candidate_id": candidate_id,
        "parent_id": manifest.get("parent_id") or _first_value(memory_states, "parent_id"),
        "origin": str(manifest.get("origin") or manifest.get("generator_mode") or "ksm/aspire"),
        "status": candidate_status(
            trials=trials,
            success_rate=success_rate,
            success_threshold=success_threshold,
            stage=stage,
            untrusted_success=untrusted_success,
        ),
        "success": success,
        "untrusted_success_evidence": untrusted_success,
        "success_rate": success_rate,
        "average_reward": float(candidate.get("average_reward") or 0.0),
        "trials": f"{completed}/{trials}",
        "failure_breakdown": _as_count_dict(candidate.get("failure_breakdown") or _merge_counts(memory_states, "failure_breakdown")),
        "trace_failure_breakdown": _as_count_dict(candidate.get("trace_failure_breakdown") or _merge_counts(memory_states, "trace_failure_breakdown")),
        "subgoal_failure_breakdown": _as_count_dict(candidate.get("subgoal_failure_breakdown") or _merge_counts(memory_states, "subgoal_failure_breakdown")),
        "rich_trace_feature_counts": _as_count_dict(
            candidate.get("rich_trace_feature_counts") or _merge_counts(memory_states, "rich_trace_feature_counts")
        ),
        "visual_displacement_evidence": {},
        "visual_effect_probes": [
            probe
            for state in memory_states
            for probe in (state.get("visual_effect_probes") or [])
            if isinstance(probe, dict)
        ][:12],
        "trace_summary": trace_summary,
        "recurring_summaries": _as_count_dict(candidate.get("recurring_summaries")),
        "evidence_gaps": _gap_list(candidate.get("evidence_gaps")) or sorted(
            dict.fromkeys(str(gap) for state in memory_states for gap in (state.get("evidence_gaps") or []))
        ),
        "failed_actions": sorted(
            dict.fromkeys(str(action) for state in memory_states for action in (state.get("failed_actions") or []))
        ),
        "runtime_arg_delta": _first_mapping(memory_states, "runtime_arg_delta"),
        "hypothesis": manifest.get("hypothesis") or _first_value(memory_states, "hypothesis"),
        "change_summary": manifest.get("change_summary") or _first_value(memory_states, "change_summary"),
        "expected_failure_modes": manifest.get("expected_failure_modes") or [],
        "negative_evidence": negative_items,
    }


def candidate_status(
    *,
    trials: int,
    success_rate: float,
    success_threshold: float,
    stage: str,
    untrusted_success: bool = False,
) -> str:
    label = safe_stage_label(stage)
    if trials <= 0:
        return "not_evaluated"
    if untrusted_success and success_rate < success_threshold:
        return "untrusted_success_evidence"
    if success_rate >= success_threshold:
        return f"validated_on_{label}"
    return f"failed_on_{label}"


def eliminated_directions(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eliminated = []
    for state in states:
        status = str(state.get("status") or "")
        if not (status.startswith("failed_on_") or status == "excluded_negative_evidence"):
            continue
        reason_parts = []
        for source in ("subgoal_failure_breakdown", "trace_failure_breakdown", "failure_breakdown"):
            counts = state.get(source) if isinstance(state.get(source), dict) else {}
            if counts:
                reason_parts.append(source + "=" + ",".join(f"{key}:{value}" for key, value in counts.items()))
        eliminated.append(
            {
                "candidate_id": state.get("candidate_id"),
                "status": status,
                "hypothesis": state.get("hypothesis"),
                "reason": "; ".join(reason_parts) or state.get("trace_summary") or "no evidence",
            }
        )
    return eliminated


def negative_evidence_from_states(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for state in states:
        for item in state.get("negative_evidence") or []:
            if not isinstance(item, dict) or not item:
                continue
            key = (
                str(item.get("candidate_id") or state.get("candidate_id") or ""),
                str(item.get("reason") or item.get("failed_actions") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(item))
    return out[:12]


def extract_action_timeline(final_status: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            step = value.get("step")
            if isinstance(step, dict):
                action = step.get("action")
                status = value.get("status")
                if isinstance(action, str) or isinstance(status, str):
                    item = {
                        "action": action,
                        "status": status,
                        "description": step.get("description") or value.get("description"),
                        "action_type": value.get("action_type"),
                        "system_time": value.get("system_time"),
                    }
                    for key in (
                        "error",
                        "output",
                        "level",
                        "step_idx",
                        "step_path",
                        "skill_file",
                        "resolved_skill_file",
                        "call_stack",
                        "details",
                        "duration_s",
                    ):
                        if value.get(key) is not None:
                            item[key] = value.get(key)
                    if step.get("assert") is not None:
                        item["assert"] = step.get("assert")
                    out.append(item)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(final_status)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for item in out:
        key = (item.get("system_time"), item.get("action"), item.get("status"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def first_failed_trace_context(action_timeline: list[dict[str, Any]]) -> dict[str, Any]:
    for item in action_timeline:
        if str(item.get("status") or "").lower() != "failed":
            continue
        return {
            "action": item.get("action"),
            "action_type": item.get("action_type"),
            "skill_file": item.get("skill_file"),
            "resolved_skill_file": item.get("resolved_skill_file"),
            "step_path": item.get("step_path"),
            "call_stack": item.get("call_stack") if isinstance(item.get("call_stack"), list) else [],
            "error": item.get("error"),
            "details": item.get("details") if isinstance(item.get("details"), dict) else {},
        }
    return {}


def infer_failure_modes(
    *,
    report: dict[str, Any],
    action_timeline: list[dict[str, Any]],
    visual_evidence: dict[str, Any],
    trace_analysis: dict[str, Any] | None = None,
) -> list[str]:
    if confirmed_success_evidence(report):
        return ["success"]
    if untrusted_reported_success(report):
        return ["untrusted_reported_success"]
    modes: list[str] = []
    signature = str(report.get("failure_signature") or "unknown_failure")
    modes.append(f"failure_signature:{signature}")
    analysis = _dict(report.get("failure_analysis"))
    category = str(analysis.get("category") or "")
    stage = str(analysis.get("stage") or "")
    if category:
        modes.append(f"category:{category}")
    if stage:
        modes.append(f"stage:{stage}")
    if signature == "effect_feedback_missing":
        modes.append("missing_effect_verification")
    if signature == "verifier_failed":
        modes.append("kw_verifier_failed")

    failed = [item for item in action_timeline if str(item.get("status") or "").lower() == "failed"]
    if not failed and signature not in {"success", "dry_run"}:
        modes.append("no_failed_action_attribution")
    for index, item in enumerate(failed):
        action = str(item.get("action") or "unknown_action")
        modes.append(f"action_failed:{action}")
        namespace = action.split("/", 1)[0] if "/" in action else action
        if namespace:
            modes.append(f"namespace_failed:{namespace}")
        if index == 0:
            modes.append(f"first_failed_action:{action}")
        lowered = action.lower()
        if "motion_planning" in lowered:
            modes.append("motion_planning_action_failed")
        if "semantic_pick" in lowered:
            modes.append("semantic_pick_action_failed")
        if "pick_verifier" in lowered:
            modes.append("pick_verifier_failed")

    trace = _dict(trace_analysis)
    for mode in trace.get("inferred_failure_modes") or []:
        text = str(mode)
        if text and text != "no_trace_failure_inferred":
            modes.append(text)
    for key, count in (trace.get("trace_failure_breakdown") or {}).items():
        if int(count or 0) > 0 and str(key) != "no_trace_failure_inferred":
            modes.append(str(key))

    visual = visual_feedback_summary(visual_evidence)
    if visual.get("no_visible_motion") is True:
        modes.append("no_observable_visual_state_change")
    if visual.get("target_object_displaced") is False:
        modes.append("target_object_not_displaced")
    if visual.get("grasp_attempt_visible") is False:
        modes.append("grasp_not_visibly_attempted")
    for probe in trace.get("visual_effect_probes") or []:
        if not isinstance(probe, dict):
            continue
        if probe.get("near_zero_after_actions") is True:
            modes.append("no_observable_visual_state_change")
        label = str(probe.get("label") or "")
        if label:
            modes.append(f"visual_effect:{label}")
    return sorted(dict.fromkeys(modes))


def infer_subgoal_failures(*, report: dict[str, Any], modes: list[str]) -> list[str]:
    subgoals: list[str] = []
    task_id = str(report.get("task_id") or "")
    for mode in modes:
        if mode.startswith("subgoal_failed:"):
            subgoals.append(mode.split(":", 1)[1])
    if any("motion_planning" in mode for mode in modes):
        subgoals.append("pregrasp_or_approach")
    if "pick_bottle" in task_id and not subgoals and not bool(report.get("success")):
        subgoals.append("pick_bottle")
    elif any("semantic_pick_action_failed" == mode for mode in modes):
        subgoals.append("object_acquisition")
    return sorted(dict.fromkeys(subgoals))


def infer_evidence_gaps(
    *,
    report: dict[str, Any],
    action_timeline: list[dict[str, Any]],
    visual_evidence: dict[str, Any],
    trace_analysis: dict[str, Any] | None = None,
) -> list[str]:
    gaps: list[str] = []
    if not action_timeline and report.get("failure_signature") not in {"success", "dry_run"}:
        gaps.append("missing_action_timeline")
    visual = visual_feedback_summary(visual_evidence)
    if visual.get("status") in {"", "human_review_required", "missing"} and not visual.get("analysis_available"):
        gaps.append("missing_structured_visual_feedback")
    if report.get("predicate_success") is None:
        gaps.append("missing_predicate_feedback")
    if report.get("effect_success") is None:
        gaps.append("missing_effect_feedback")
    if not any(item.get("status") == "failed" for item in action_timeline) and not bool(report.get("success")):
        gaps.append("missing_failed_step_attribution")
    for gap in (_dict(trace_analysis).get("evidence_gaps") or []):
        gaps.append(str(gap))
    return sorted(dict.fromkeys(gaps))


def build_agent_feedback(
    *,
    report: dict[str, Any],
    action_timeline: list[dict[str, Any]],
    failed_actions: list[str],
    first_failed_action: str | None,
    visual_evidence: dict[str, Any],
    trace_analysis: dict[str, Any] | None,
    runtime_delta: dict[str, Any],
    modes: list[str],
    subgoals: list[str],
    evidence_gaps: list[str],
) -> dict[str, Any]:
    agent_modes = agent_visible_modes(modes)
    agent_gaps = agent_visible_evidence_gaps(evidence_gaps)
    if not agent_modes:
        agent_modes = ["agent_visible_failure_unspecified"]
    summary = agent_recurring_summary(
        report=report,
        first_failed_action=first_failed_action,
        modes=agent_modes,
        visual_evidence=visual_evidence,
        evidence_gaps=agent_gaps,
    )
    return {
        "schema": AGENT_FEEDBACK_SCHEMA,
        "source_policy": "agent_observable_only",
        "excluded_sources": [
            "privileged_task_scoring",
            "debug_only_raw_reports",
        ],
        "candidate_id": report.get("candidate_id"),
        "task_id": report.get("task_id"),
        "observable_status": agent_observable_status(report=report, visual_evidence=visual_evidence),
        "execution_success": bool(report.get("execution_success", report.get("pipeline_success"))),
        "pipeline_success": bool(report.get("pipeline_success")),
        "skill_success": bool(report.get("skill_success")),
        "reported_success": bool(report.get("success")),
        "reported_success_without_effect_evidence": untrusted_reported_success(report),
        "observable_failure_signature": agent_visible_failure_signature(
            report,
            first_failed_action=first_failed_action,
        ),
        "action_timeline": action_timeline,
        "failed_actions": list(dict.fromkeys(failed_actions)),
        "first_failed_action": first_failed_action,
        "first_failed_trace_context": first_failed_trace_context(action_timeline),
        "runtime_arg_delta": runtime_delta,
        "visual_feedback": visual_feedback_summary(visual_evidence),
        "trace_summary": _dict(trace_analysis).get("recurring_summary"),
        "visual_effect_probes": _dict(trace_analysis).get("visual_effect_probes") or [],
        "rich_trace_feature_counts": _dict(trace_analysis).get("rich_trace_feature_counts") or {},
        "trace_failure_breakdown": _count_modes(agent_modes),
        "subgoal_failure_breakdown": _count_modes(agent_visible_modes(subgoals)),
        "inferred_failure_modes": agent_modes,
        "recurring_summary": summary,
        "evidence_gaps": agent_gaps,
        "repair_focus": repair_focus(
            modes=agent_modes,
            failed_actions=failed_actions,
            evidence_gaps=agent_gaps,
            report=report,
        ),
    }


def agent_visible_evidence_gaps(evidence_gaps: list[str]) -> list[str]:
    return sorted(
        dict.fromkeys(
            str(item)
            for item in evidence_gaps
            if not _contains_evaluator_only_text(str(item))
            and str(item) not in {"missing_effect_feedback"}
        )
    )


def agent_visible_modes(modes: list[str]) -> list[str]:
    return sorted(
        dict.fromkeys(
            str(item)
            for item in modes
            if str(item)
            and not any(token in str(item).lower() for token in EVALUATOR_ONLY_MODE_TOKENS)
            and not _contains_evaluator_only_text(str(item))
        )
    )


def agent_visible_failure_signature(report: dict[str, Any], *, first_failed_action: str | None = None) -> str:
    signature = str(report.get("failure_signature") or "unknown_failure")
    if first_failed_action and not _contains_evaluator_only_text(signature):
        return f"action_failed:{first_failed_action}"
    if signature == "effect_feedback_missing" or _contains_evaluator_only_text(signature):
        if bool(report.get("pipeline_success")):
            return "pipeline_completed_effect_unconfirmed_by_agent_feedback"
        return "agent_visible_failure_unspecified"
    return signature


def agent_observable_status(*, report: dict[str, Any], visual_evidence: dict[str, Any]) -> str:
    if not bool(report.get("pipeline_success")):
        return "pipeline_failed"
    visual = visual_feedback_summary(visual_evidence)
    if visual.get("no_visible_motion") is True:
        return "pipeline_completed_no_visible_motion"
    if any(
        visual.get(key) is True
        for key in (
            "robot_motion_visible",
            "grasp_attempt_visible",
            "target_contact_visible",
            "target_object_displaced",
            "target_object_lifted",
        )
    ):
        return "pipeline_completed_with_visual_progress"
    return "pipeline_completed_effect_unconfirmed_by_agent_feedback"


def agent_recurring_summary(
    *,
    report: dict[str, Any],
    first_failed_action: str | None,
    modes: list[str],
    visual_evidence: dict[str, Any],
    evidence_gaps: list[str],
) -> str:
    parts: list[str] = []
    if first_failed_action:
        parts.append(f"First failed action: {first_failed_action}.")
    elif not bool(report.get("pipeline_success")):
        signature = agent_visible_failure_signature(report)
        parts.append(f"Agent-visible failure signature: {signature}.")
    else:
        parts.append("Pipeline completed; task effect is not confirmed by agent-observable feedback.")
    if "motion_planning_action_failed" in modes:
        parts.append("The candidate failed in the approach/pregrasp motion-planning path.")
    elif "semantic_pick_action_failed" in modes:
        parts.append("The candidate failed inside the existing semantic pick path.")
    visual = visual_feedback_summary(visual_evidence)
    if visual.get("no_visible_motion") is True:
        parts.append("Visual evidence reports no visible robot/object motion.")
    elif visual.get("analysis_available") is False:
        parts.append("No structured visual analysis is available.")
    if evidence_gaps:
        parts.append("Agent-observable evidence gaps: " + ", ".join(evidence_gaps) + ".")
    return " ".join(parts)


def runtime_arg_delta(
    *,
    task_args: dict[str, Any],
    candidate_args: dict[str, Any],
    runtime_args: dict[str, Any],
) -> dict[str, Any]:
    candidate_overrides: dict[str, dict[str, Any]] = {}
    for key, value in candidate_args.items():
        if task_args.get(key) != value:
            candidate_overrides[key] = {"task": task_args.get(key), "candidate": value}
    runtime_mismatches: dict[str, dict[str, Any]] = {}
    for key, value in candidate_args.items():
        if runtime_args.get(key) != value:
            runtime_mismatches[key] = {"candidate": value, "runtime": runtime_args.get(key)}
    return {
        "candidate_overrides": candidate_overrides,
        "runtime_mismatches": runtime_mismatches,
        "runtime_matches_candidate": not bool(runtime_mismatches),
    }


def visual_feedback_summary(visual_evidence: dict[str, Any]) -> dict[str, Any]:
    if not visual_evidence:
        return {"status": "missing", "analysis_available": False}
    analysis = _dict(visual_evidence.get("analysis"))
    summary = {
        "status": str(visual_evidence.get("status") or ""),
        "analysis_available": bool(analysis),
        "artifacts": visual_evidence.get("artifacts") or {},
        "note": visual_evidence.get("note"),
    }
    for key in (
        "temporal_observations",
        "robot_object_interactions",
        "visible_state_changes",
        "uncertain_points",
        "evidence_summary",
        "suggested_next_probe",
        "no_visible_motion",
        "robot_motion_visible",
        "grasp_attempt_visible",
        "target_contact_visible",
        "target_object_displaced",
        "target_object_lifted",
        "object_released_over_container",
        "placed_inside_container",
        "task_success",
        "failure_stage",
        "evidence",
        "confidence",
    ):
        if key in analysis:
            summary[key] = analysis[key]
    if "evidence" not in summary and analysis.get("evidence_summary"):
        summary["evidence"] = analysis.get("evidence_summary")
    return summary


def recurring_summary(
    *,
    report: dict[str, Any],
    first_failed_action: str | None,
    modes: list[str],
    visual_evidence: dict[str, Any],
    evidence_gaps: list[str],
) -> str:
    if confirmed_success_evidence(report):
        return "Task completed successfully."
    if untrusted_reported_success(report):
        return "Reported success lacks task/effect evidence; exclude it from success memory."
    parts: list[str] = []
    if first_failed_action:
        parts.append(f"First failed action: {first_failed_action}.")
    else:
        parts.append(f"Failure signature: {report.get('failure_signature') or 'unknown_failure'}.")
    if "motion_planning_action_failed" in modes:
        parts.append("The candidate failed in the approach/pregrasp motion-planning path.")
    elif "semantic_pick_action_failed" in modes:
        parts.append("The candidate failed inside the existing semantic pick path.")
    visual = visual_feedback_summary(visual_evidence)
    if visual.get("no_visible_motion") is True:
        parts.append("Visual evidence reports no visible robot/object motion.")
    if evidence_gaps:
        parts.append("Evidence gaps: " + ", ".join(evidence_gaps) + ".")
    return " ".join(parts)


def negative_evidence_entry(
    *,
    report: dict[str, Any],
    modes: list[str],
    failed_actions: list[str],
    runtime_delta: dict[str, Any],
    candidate_manifest: dict[str, Any],
    summary: str,
) -> dict[str, Any] | None:
    if confirmed_success_evidence(report) or untrusted_reported_success(report):
        return None
    return {
        "candidate_id": report.get("candidate_id"),
        "failed_actions": list(dict.fromkeys(failed_actions)),
        "hypothesis": candidate_manifest.get("hypothesis"),
        "change_summary": candidate_manifest.get("change_summary"),
        "reason": summary,
    }


def repair_focus(
    *,
    modes: list[str],
    failed_actions: list[str],
    evidence_gaps: list[str],
    report: dict[str, Any],
) -> dict[str, Any]:
    avoid = []
    if failed_actions:
        avoid.append("Do not repeat the same failed action path without a new mechanism-level reason.")
    if "missing_structured_visual_feedback" in evidence_gaps:
        avoid.append("Do not infer physical progress from skill status alone; visual feedback is missing.")
    return {
        "avoid_repeating": avoid,
        "next_prompt_requirement": (
            "The next candidate must explicitly state which failed mechanism it changes and "
            "which trace/visual signal should improve."
            if not confirmed_success_evidence(report)
            else ""
        ),
    }


def candidate_state_from_attribution(attr: dict[str, Any]) -> dict[str, Any]:
    negative = attr.get("negative_evidence") if isinstance(attr.get("negative_evidence"), dict) else {}
    agent = _dict(attr.get("agent_feedback"))
    untrusted_success = bool(attr.get("untrusted_success_evidence"))
    success = bool(attr.get("success"))
    return {
        "candidate_id": attr.get("candidate_id"),
        "status": (
            "validated"
            if success
            else "untrusted_success_evidence"
            if untrusted_success
            else "failed"
        ),
        "success": success,
        "reported_success": bool(attr.get("reported_success")),
        "confirmed_success": success,
        "success_evidence_level": attr.get("success_evidence_level"),
        "untrusted_success_evidence": untrusted_success,
        "agent_feedback": agent,
        "failure_breakdown": _count_modes([str(agent.get("observable_failure_signature") or "unknown")]),
        "trace_failure_breakdown": agent.get("trace_failure_breakdown") or {},
        "subgoal_failure_breakdown": agent.get("subgoal_failure_breakdown") or {},
        "rich_trace_feature_counts": agent.get("rich_trace_feature_counts") or attr.get("rich_trace_feature_counts") or {},
        "visual_effect_probes": agent.get("visual_effect_probes") or attr.get("visual_effect_probes") or [],
        "recurring_summary": agent.get("recurring_summary"),
        "evidence_gaps": agent.get("evidence_gaps") or [],
        "failed_actions": agent.get("failed_actions") or [],
        "runtime_arg_delta": agent.get("runtime_arg_delta") or {},
        "hypothesis": negative.get("hypothesis"),
        "change_summary": negative.get("change_summary"),
        "negative_evidence": negative,
    }


def open_questions(states: list[dict[str, Any]]) -> list[str]:
    questions: list[str] = []
    trace_modes: Counter[str] = Counter()
    subgoals: Counter[str] = Counter()
    gaps: Counter[str] = Counter()
    for state in states:
        trace_modes.update(state.get("trace_failure_breakdown") or {})
        subgoals.update(state.get("subgoal_failure_breakdown") or {})
        gaps.update({str(item): 1 for item in state.get("evidence_gaps") or []})
    if trace_modes.get("no_failed_action_attribution"):
        questions.append("Some failed candidates lack failed-action attribution; inspect pipeline logs or trace collection.")
    if gaps.get("missing_structured_visual_feedback"):
        questions.append("Failed candidates lack structured visual feedback; next runs should attach WebUI frame/video analysis.")
    if subgoals:
        name, count = subgoals.most_common(1)[0]
        questions.append(f"Recurring blocked subgoal is `{name}` ({count} reports); next candidates should target this prerequisite effect.")
    return questions


def evidence_gaps_from_states(states: list[dict[str, Any]]) -> list[str]:
    gaps: Counter[str] = Counter()
    for state in states:
        gaps.update({str(item): 1 for item in state.get("evidence_gaps") or []})
    return [f"{name}:{count}" for name, count in gaps.most_common()]


def agent_safe_payload(value: Any) -> Any:
    """Strip evaluator/ground-truth feedback before payloads enter LLM prompts."""

    if isinstance(value, dict):
        if value.get("schema") == ATTRIBUTION_SCHEMA:
            return {
                "schema": "ksm.aspire_kw.agent_safe_feedback_attribution.v1",
                "agent_feedback": agent_safe_payload(agent_feedback_from_attribution(value)),
            }
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if is_evaluator_only_key(key) or key in AGENT_STATIC_TAXONOMY_KEYS:
                continue
            if key == "feedback_attribution" and isinstance(item, dict):
                out["agent_feedback"] = agent_safe_payload(agent_feedback_from_attribution(item))
                continue
            safe_item = agent_safe_payload(item)
            if safe_item is not None:
                out[key] = safe_item
        return out
    if isinstance(value, list):
        return [item for item in (agent_safe_payload(item) for item in value) if item is not None]
    if isinstance(value, tuple):
        return [item for item in (agent_safe_payload(item) for item in value) if item is not None]
    if isinstance(value, str):
        return "evaluator_feedback_omitted" if _contains_evaluator_only_text(value) else value
    return value


def agent_feedback_from_attribution(attr: dict[str, Any]) -> dict[str, Any]:
    existing = attr.get("agent_feedback")
    if isinstance(existing, dict):
        return existing
    modes = agent_visible_modes(list(attr.get("inferred_failure_modes") or []))
    gaps = agent_visible_evidence_gaps(list(attr.get("evidence_gaps") or []))
    return {
        "schema": AGENT_FEEDBACK_SCHEMA,
        "source_policy": "agent_observable_only",
        "candidate_id": attr.get("candidate_id"),
        "task_id": attr.get("task_id"),
        "observable_status": "legacy_agent_feedback_missing",
        "execution_success": attr.get("execution_success"),
        "pipeline_success": attr.get("pipeline_success"),
        "skill_success": attr.get("skill_success"),
        "reported_success": attr.get("reported_success"),
        "reported_success_without_effect_evidence": attr.get("untrusted_success_evidence"),
        "observable_failure_signature": (
            str(attr.get("failure_signature") or "unknown_failure")
            if not _contains_evaluator_only_text(str(attr.get("failure_signature") or ""))
            else "agent_visible_failure_unspecified"
        ),
        "action_timeline": attr.get("action_timeline") or [],
        "failed_actions": attr.get("failed_actions") or [],
        "first_failed_action": attr.get("first_failed_action"),
        "runtime_arg_delta": attr.get("runtime_arg_delta") or {},
        "visual_feedback": attr.get("visual_feedback") or {},
        "trace_failure_breakdown": _count_modes(modes),
        "subgoal_failure_breakdown": {},
        "inferred_failure_modes": modes,
        "recurring_summary": (
            str(attr.get("recurring_summary") or "")
            if not _contains_evaluator_only_text(str(attr.get("recurring_summary") or ""))
            else "Legacy evaluator feedback omitted; inspect agent-observable traces."
        ),
        "evidence_gaps": gaps,
        "repair_focus": agent_safe_payload(attr.get("repair_focus") or {}),
    }


def is_evaluator_only_key(key: str) -> bool:
    lowered = str(key).lower()
    return lowered in EVALUATOR_ONLY_KEYS or any(token in lowered for token in ("predicate", "ground_truth", "groundtruth"))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_stage_label(stage: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(stage or "stage")).strip("._") or "stage"


def _first_value(states: list[dict[str, Any]], key: str) -> Any:
    for state in states:
        value = state.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _first_mapping(states: list[dict[str, Any]], key: str) -> dict[str, Any]:
    value = _first_value(states, key)
    return dict(value) if isinstance(value, dict) else {}


def _merge_counts(states: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for state in states:
        counts.update(_as_count_dict(state.get(key)))
    return dict(sorted(counts.items()))


def _as_count_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, count in value.items():
        try:
            out[str(key)] = int(count)
        except Exception:
            continue
    return dict(sorted(out.items()))


def _gap_list(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [f"{name}:{count}" for name, count in sorted(value.items())]


def _most_common_text(value: Any) -> str:
    counts = _as_count_dict(value)
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _count_modes(modes: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(modes).items()))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _contains_evaluator_only_text(value: str) -> bool:
    lowered = str(value).lower()
    return any(token in lowered for token in EVALUATOR_ONLY_TEXT_TOKENS)
