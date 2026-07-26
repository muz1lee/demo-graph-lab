from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def pipeline_status_to_trace(status: dict[str, Any]) -> dict[str, Any]:
    response = status.get("response") if isinstance(status.get("response"), dict) else status
    logs = response.get("logs") if isinstance(response, dict) else []
    events: list[dict[str, Any]] = []
    if isinstance(logs, list):
        for index, item in enumerate(logs):
            events.extend(_log_item_to_events(item, index_prefix=str(index)))
    return {
        "schema": "aspire.knowin.trace.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "num_events": len(events),
        "events": events,
        "pipeline_running": bool(response.get("running")) if isinstance(response, dict) else None,
        "pipeline_success": response.get("success") if isinstance(response, dict) else None,
        "raw_status": status,
    }


def _log_item_to_events(item: Any, *, index_prefix: str) -> list[dict[str, Any]]:
    if not isinstance(item, dict):
        return [
            {
                "event_id": index_prefix,
                "step": _event_step(index_prefix),
                "event_type": "log",
                "function": "log",
                "status": "unknown",
                "message": str(item),
            }
        ]
    raw_step = item.get("step") if isinstance(item.get("step"), dict) else {}
    action = raw_step.get("action") or item.get("action") or item.get("action_type") or item.get("skill_file") or "unknown"
    status = str(item.get("status") or "unknown")
    event = {
        "event_id": index_prefix,
        "step": _event_step(index_prefix),
        "event_type": "skill_step",
        "function": str(action),
        "label": str(item.get("description") or raw_step.get("description") or ""),
        "skill_file": item.get("skill_file") or item.get("skill") or item.get("file"),
        "status": status,
        "args": raw_step.get("args") or {},
        "result": {
            "action_type": item.get("action_type"),
            "system_time": item.get("system_time"),
            "skill_file": item.get("skill_file") or item.get("skill") or item.get("file"),
        },
        "message": item.get("message") or item.get("report") or "",
    }
    for key in (
        "output",
        "level",
        "step_idx",
        "step_path",
        "resolved_skill_file",
        "call_stack",
        "details",
        "duration_s",
    ):
        if item.get(key) is not None:
            event[key] = item.get(key)
    if raw_step.get("assert") is not None:
        event["assert"] = raw_step.get("assert")
    if item.get("error") is not None:
        event["error"] = item.get("error")
    elif status not in {"success", "submitted", "unknown"}:
        event["error"] = item.get("message") or status
    events = [event]
    nested = item.get("logs")
    if isinstance(nested, list):
        for index, child in enumerate(nested):
            events.extend(_log_item_to_events(child, index_prefix=f"{index_prefix}.{index}"))
    return events


def add_episode_event(trace: dict[str, Any], *, label: str, payload: dict[str, Any], status: str = "ok") -> dict[str, Any]:
    out = dict(trace)
    events = list(out.get("events") or [])
    events.append(
        {
            "event_id": f"episode.{len(events)}",
            "step": len(events),
            "event_type": "semantic",
            "function": "aspire_log_event",
            "label": label,
            "status": status,
            "result": payload,
        }
    )
    out["events"] = events
    out["num_events"] = len(events)
    return out


def add_visual_evidence_event(trace: dict[str, Any], visual_evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(visual_evidence, dict) or not visual_evidence:
        return trace
    analysis = visual_evidence.get("analysis") if isinstance(visual_evidence.get("analysis"), dict) else {}
    return add_episode_event(
        trace,
        label="visual_feedback",
        status=str(visual_evidence.get("status") or "unknown"),
        payload={
            "schema": visual_evidence.get("schema"),
            "provider": visual_evidence.get("provider"),
            "model": visual_evidence.get("model"),
            "analysis_available": bool(visual_evidence.get("analysis_available")),
            "analysis": analysis,
            "artifacts": visual_evidence.get("artifacts") or {},
            "source_policy": visual_evidence.get("source_policy"),
            "note": visual_evidence.get("note"),
        },
    )


def _event_step(index_prefix: str) -> int:
    head = str(index_prefix).split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 0
