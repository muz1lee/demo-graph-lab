from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .io import read_json
from .feedback_attribution import (
    build_feedback_memory,
    build_task_analysis_state,
    current_or_rebuilt_attribution,
)
from .sanitize import sanitize_aspire_output


def scan_episode_reports(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    reports: list[dict[str, Any]] = []
    for path in sorted(root_path.rglob("episode_report.json")):
        try:
            data = read_json(path)
        except Exception as exc:
            data = {
                "candidate_id": path.parent.name,
                "success": False,
                "failure_signature": "malformed_episode_report",
                "error": repr(exc),
            }
        if isinstance(data, dict):
            item = sanitize_aspire_output(dict(data))
            item["feedback_attribution"] = current_or_rebuilt_attribution(item)
            item["report_path"] = str(path)
            reports.append(item)
    total = len(reports)
    completed = sum(1 for item in reports if bool(item.get("success")))
    failures = Counter(str(item.get("failure_signature") or "unknown_failure") for item in reports)
    by_candidate: dict[str, dict[str, Any]] = {}
    for item in reports:
        candidate_id = str(item.get("candidate_id") or "unknown")
        record = by_candidate.setdefault(
            candidate_id,
            {
                "candidate_id": candidate_id,
                "num_trials": 0,
                "task_completed": 0,
                "success_rate": 0.0,
                "failure_breakdown": {},
                "trace_failure_breakdown": {},
                "subgoal_failure_breakdown": {},
                "recurring_summaries": {},
                "evidence_gaps": {},
                "reports": [],
            },
        )
        record["num_trials"] += 1
        if bool(item.get("success")):
            record["task_completed"] += 1
        failure = str(item.get("failure_signature") or "unknown_failure")
        record["failure_breakdown"][failure] = record["failure_breakdown"].get(failure, 0) + 1
        attribution = item.get("feedback_attribution") if isinstance(item.get("feedback_attribution"), dict) else {}
        for name, count in (attribution.get("trace_failure_breakdown") or {}).items():
            bucket = record["trace_failure_breakdown"]
            bucket[str(name)] = bucket.get(str(name), 0) + int(count)
        for name, count in (attribution.get("subgoal_failure_breakdown") or {}).items():
            bucket = record["subgoal_failure_breakdown"]
            bucket[str(name)] = bucket.get(str(name), 0) + int(count)
        summary = str(attribution.get("recurring_summary") or "")
        if summary:
            bucket = record["recurring_summaries"]
            bucket[summary] = bucket.get(summary, 0) + 1
        for gap in attribution.get("evidence_gaps") or []:
            bucket = record["evidence_gaps"]
            bucket[str(gap)] = bucket.get(str(gap), 0) + 1
        record["reports"].append(item["report_path"])
    for record in by_candidate.values():
        trials = int(record["num_trials"])
        record["success_rate"] = float(record["task_completed"]) / trials if trials else 0.0
        record["failure_breakdown"] = dict(sorted(record["failure_breakdown"].items()))
        record["trace_failure_breakdown"] = dict(sorted(record["trace_failure_breakdown"].items()))
        record["subgoal_failure_breakdown"] = dict(sorted(record["subgoal_failure_breakdown"].items()))
        record["recurring_summaries"] = dict(sorted(record["recurring_summaries"].items(), key=lambda item: (-item[1], item[0]))[:5])
        record["evidence_gaps"] = dict(sorted(record["evidence_gaps"].items()))
    candidates = sorted(
        by_candidate.values(),
        key=lambda item: (-float(item["success_rate"]), str(item["candidate_id"])),
    )
    feedback_memory = build_feedback_memory(reports)
    task_ids = sorted(dict.fromkeys(str(item.get("task_id") or "") for item in reports if item.get("task_id")))
    return {
        "root": str(root_path),
        "num_trials": total,
        "task_completed": completed,
        "success_rate": completed / total if total else 0.0,
        "failure_breakdown": dict(sorted(failures.items())),
        "candidates": candidates,
        "reports": reports,
        "feedback_memory": feedback_memory,
        "task_analysis_state": build_task_analysis_state(
            suite_id=root_path.name or "scan",
            task_ids=task_ids,
            stage="scan",
            manifest_path=str(root_path),
            run_dir=str(root_path),
            success_threshold=1.0,
            reports=reports,
            candidates=candidates,
            feedback_memory=feedback_memory,
        ),
    }


def markdown_leaderboard(summary: dict[str, Any]) -> str:
    lines = [
        "# KSM ASPIRE-KW Leaderboard",
        "",
        f"- Root: `{summary.get('root')}`",
        f"- Trials: {int(summary.get('num_trials') or 0)}",
        f"- Task completed: {int(summary.get('task_completed') or 0)}",
        f"- Success rate: {float(summary.get('success_rate') or 0.0):.3f}",
        "",
        "| Rank | Candidate | Success | Trials | Failures |",
        "|---:|---|---:|---:|---|",
    ]
    for rank, record in enumerate(summary.get("candidates") or [], start=1):
        failures = ", ".join(f"{name}:{count}" for name, count in (record.get("failure_breakdown") or {}).items())
        lines.append(
            f"| {rank} | `{record.get('candidate_id')}` | "
            f"{float(record.get('success_rate') or 0.0):.3f} | "
            f"{int(record.get('task_completed') or 0)}/{int(record.get('num_trials') or 0)} | "
            f"`{failures or 'none'}` |"
        )
    lines.append("")
    return "\n".join(lines)
