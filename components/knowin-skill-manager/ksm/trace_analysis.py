from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


TRACE_ANALYSIS_SCHEMA = "ksm.aspire_kw.trace_analysis.v1"


def analyze_trace(trace: dict[str, Any] | str | Path) -> dict[str, Any]:
    data = _load_trace(trace)
    events = data.get("events") if isinstance(data.get("events"), list) else []
    function_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    action_events: list[dict[str, Any]] = []
    failed_events: list[dict[str, Any]] = []
    subgoals: list[dict[str, Any]] = []
    visual_events: list[dict[str, Any]] = []
    visual_effect_probes: list[dict[str, Any]] = []
    rich_features: Counter[str] = Counter()

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        function = str(event.get("function") or event.get("event_type") or "unknown")
        status = str(event.get("status") or "unknown")
        label = str(event.get("label") or "")
        function_counts[function] += 1
        status_counts[status] += 1
        if event.get("call_stack"):
            rich_features["call_stack"] += 1
        if event.get("step_path"):
            rich_features["step_path"] += 1
        if event.get("skill_file"):
            rich_features["skill_file"] += 1
        if function not in {"log", "aspire_log_event"}:
            action_events.append(_compact_event(event, index=index))
        if status.lower() in {"failed", "error", "failure"} and not _is_outer_skill_container(event):
            failed_events.append(_compact_event(event, index=index))
        if function == "aspire_log_event" and label == "subgoal_assertion":
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            subgoals.append(
                {
                    "step": event.get("step", index),
                    "name": result.get("name") or "unknown_subgoal",
                    "passed": result.get("passed"),
                    "metrics": result.get("metrics") if isinstance(result.get("metrics"), dict) else {},
                    "failure_reason": result.get("failure_reason"),
                    "source": "aspire_trace.json",
                }
            )
        if function == "aspire_log_event" and label == "visual_feedback":
            visual = _visual_event_payload(event)
            visual_events.append(visual)
            visual_effect_probes.extend(_visual_effect_probes(visual))
            if visual.get("analysis_available"):
                rich_features["visual_feedback"] += 1

    modes = infer_trace_failure_modes(
        failed_events=failed_events,
        subgoals=subgoals,
        visual_effect_probes=visual_effect_probes,
    )
    gaps = infer_evidence_gaps(
        events=events,
        failed_events=failed_events,
        visual_events=visual_events,
        modes=modes,
    )
    return {
        "schema": TRACE_ANALYSIS_SCHEMA,
        "trace_schema": data.get("schema"),
        "num_events": int(data.get("num_events") or len(events)),
        "function_counts": dict(sorted(function_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "action_events": action_events,
        "failed_events": failed_events,
        "subgoals": subgoals,
        "visual_events": visual_events,
        "visual_effect_probes": visual_effect_probes,
        "rich_trace_feature_counts": dict(sorted(rich_features.items())),
        "trace_failure_breakdown": _count_modes(modes),
        "subgoal_failure_breakdown": _count_modes(
            [str(item.get("name") or "unknown_subgoal") for item in subgoals if item.get("passed") is False]
        ),
        "inferred_failure_modes": modes,
        "evidence_gaps": gaps,
        "recurring_summary": trace_summary_sentence(
            modes=modes,
            failed_events=failed_events,
            subgoals=subgoals,
            visual_effect_probes=visual_effect_probes,
            gaps=gaps,
        ),
    }


def write_trace_analysis(trace: dict[str, Any] | str | Path, output_path: str | Path) -> dict[str, Any]:
    analysis = analyze_trace(trace)
    Path(output_path).write_text(json.dumps(analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return analysis


def infer_trace_failure_modes(
    *,
    failed_events: list[dict[str, Any]],
    subgoals: list[dict[str, Any]],
    visual_effect_probes: list[dict[str, Any]],
) -> list[str]:
    modes: list[str] = []
    for event in failed_events:
        function = str(event.get("function") or "unknown_action")
        modes.append(f"action_failed:{function}")
        namespace = function.split("/", 1)[0] if "/" in function else ""
        if namespace:
            modes.append(f"namespace_failed:{namespace}")
        lowered = function.lower()
        if "motion_planning" in lowered:
            modes.append("motion_planning_action_failed")
        if "semantic_pick" in lowered:
            modes.append("semantic_pick_action_failed")
        if "pick_verifier" in lowered:
            modes.append("pick_verifier_failed")
    for subgoal in subgoals:
        if subgoal.get("passed") is False:
            modes.append(f"subgoal_failed:{subgoal.get('name') or 'unknown_subgoal'}")
    for probe in visual_effect_probes:
        label = str(probe.get("label") or "")
        if probe.get("near_zero_after_actions") is True:
            modes.append("no_observable_visual_state_change")
        if label:
            modes.append(f"visual_effect:{label}")
    return sorted(dict.fromkeys(modes)) or ["no_trace_failure_inferred"]


def infer_evidence_gaps(
    *,
    events: list[Any],
    failed_events: list[dict[str, Any]],
    visual_events: list[dict[str, Any]],
    modes: list[str],
) -> list[str]:
    gaps: list[str] = []
    if not events:
        gaps.append("missing_pipeline_trace")
    if not failed_events and any(mode not in {"no_trace_failure_inferred", "success"} for mode in modes):
        gaps.append("missing_failed_step_attribution")
    if not visual_events:
        gaps.append("missing_structured_visual_feedback")
    elif not any(item.get("analysis_available") for item in visual_events):
        gaps.append("missing_structured_visual_feedback")
    if "no_trace_failure_inferred" in modes and not gaps:
        gaps.append("trace_has_no_actionable_failure_attribution")
    return sorted(dict.fromkeys(gaps))


def trace_summary_sentence(
    *,
    modes: list[str],
    failed_events: list[dict[str, Any]],
    subgoals: list[dict[str, Any]],
    visual_effect_probes: list[dict[str, Any]],
    gaps: list[str],
) -> str:
    failed_subgoals = [item for item in subgoals if item.get("passed") is False]
    if failed_subgoals:
        item = failed_subgoals[-1]
        return f"Subgoal failed: {item.get('name')}. Metrics: {item.get('metrics') or {}}."
    if failed_events:
        first = failed_events[0]
        error = str(first.get("error") or "").strip()
        error_text = f" Error: {error[:240]}." if error else ""
        stack = first.get("call_stack") if isinstance(first.get("call_stack"), list) else []
        stack_text = f" Call stack: {' -> '.join(str(item) for item in stack)}." if stack else ""
        step_path = str(first.get("step_path") or "").strip()
        step_text = f" Step path: {step_path}." if step_path else ""
        return f"First failed action: {first.get('function')}. Status: {first.get('status')}.{stack_text}{step_text}{error_text}"
    near_zero = [item for item in visual_effect_probes if item.get("near_zero_after_actions") is True]
    if near_zero:
        labels = ", ".join(str(item.get("label") or item.get("object_key") or "visible_object") for item in near_zero[:3])
        return f"Visual feedback reports no observable state change for: {labels}."
    if gaps:
        return "Trace evidence gaps: " + ", ".join(gaps) + "."
    return ", ".join(modes)


def _visual_effect_probes(visual: dict[str, Any]) -> list[dict[str, Any]]:
    analysis = visual.get("analysis") if isinstance(visual.get("analysis"), dict) else {}
    probes: list[dict[str, Any]] = []

    for item in _list_of_dicts(analysis.get("visible_state_changes")):
        label = str(item.get("label") or item.get("object") or item.get("description") or "visible_state_change")
        changed = _bool_or_none(item.get("changed"))
        probes.append(
            {
                "label": label,
                "object_key": item.get("object") or item.get("target") or label,
                "summary": item.get("summary") or item.get("description") or "",
                "near_zero_after_actions": changed is False,
                "confidence": item.get("confidence"),
                "source": "visual_feedback",
            }
        )

    legacy = analysis
    if legacy.get("target_object_displaced") is False:
        probes.append(
            {
                "label": "target_object_not_displaced",
                "object_key": "target_object",
                "summary": "Legacy visual feedback reported target_object_displaced=false.",
                "near_zero_after_actions": True,
                "confidence": legacy.get("confidence"),
                "source": "visual_feedback_legacy",
            }
        )
    if legacy.get("no_visible_motion") is True:
        probes.append(
            {
                "label": "no_visible_motion",
                "object_key": "scene",
                "summary": "Legacy visual feedback reported no visible robot/object motion.",
                "near_zero_after_actions": True,
                "confidence": legacy.get("confidence"),
                "source": "visual_feedback_legacy",
            }
        )
    return probes


def _visual_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    return {
        "step": event.get("step"),
        "status": event.get("status"),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "analysis_available": bool(result.get("analysis_available")),
        "analysis": result.get("analysis") if isinstance(result.get("analysis"), dict) else {},
        "artifacts": result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {},
        "note": result.get("note"),
        "source_policy": result.get("source_policy"),
    }


def _compact_event(event: dict[str, Any], *, index: int) -> dict[str, Any]:
    compact = {
        "step": event.get("step", index),
        "function": event.get("function") or event.get("event_type") or "unknown",
        "label": event.get("label"),
        "status": event.get("status"),
        "args": event.get("args") if isinstance(event.get("args"), dict) else {},
        "error": event.get("error") or event.get("message"),
    }
    for key in (
        "output",
        "level",
        "step_idx",
        "step_path",
        "skill_file",
        "resolved_skill_file",
        "call_stack",
        "details",
        "duration_s",
        "assert",
    ):
        if event.get(key) is not None:
            compact[key] = event.get(key)
    return compact


def _is_outer_skill_container(event: dict[str, Any]) -> bool:
    function = str(event.get("function") or "")
    skill_file = str(event.get("skill_file") or "")
    if not skill_file or function != skill_file:
        return False
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    return not str(event.get("label") or "") and result.get("action_type") in {None, ""}


def _load_trace(trace: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(trace, dict):
        return trace
    path = Path(trace)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _count_modes(modes: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(str(item) for item in modes if str(item)).items()))


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
