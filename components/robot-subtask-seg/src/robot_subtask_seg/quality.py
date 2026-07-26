from __future__ import annotations

from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

from robot_subtask_seg.audit import apply_trace_audit
from robot_subtask_seg.schema import Trace, TraceSegment


REQUIRED_ACTION_FIELDS = [
    "actor_arm",
    "eef_event",
    "motion_type",
    "manipulated_object",
    "target_object",
    "target_role",
    "role",
    "confidence",
    "visual_evidence",
    "method_note",
]

PICK_WORD_RE = re.compile(r"\b(pick|pick up|grasp|grab|lift)\b", re.IGNORECASE)
TERMINAL_WORD_RE = re.compile(
    r"\b(place|drop|stack|insert|pour)\b|\bplug\s+(?:in|into)\b",
    re.IGNORECASE,
)


def audit_run(output_dir: str | Path, *, base_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(output_dir)
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    traces: list[dict[str, Any]] = []
    run_issues: list[dict[str, Any]] = []

    summary_path = root / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:
            run_issues.append(
                issue(
                    "error",
                    "summary_parse_error",
                    f"summary.json is not valid JSON: {type(exc).__name__}: {exc}",
                    trace_path=str(summary_path),
                )
            )
            summary_len = None
        else:
            summary_len = len(summary) if hasattr(summary, "__len__") else None
    else:
        summary_len = None
        run_issues.append(
            issue(
                "warning",
                "summary_missing",
                "summary.json is missing",
                trace_path=str(summary_path),
            )
        )

    error_files = sorted(root.glob("*/*/error.json"))
    for path in error_files:
        run_issues.append(
            issue(
                "error",
                "per_video_error",
                "per-video error.json exists",
                trace_path=str(path),
            )
        )

    trace_paths = sorted(root.glob("*/*/trace.json"))
    if not trace_paths:
        run_issues.append(
            issue(
                "error",
                "trace_missing",
                "no trace.json files found under output directory",
                trace_path=str(root),
            )
        )

    for trace_path in trace_paths:
        traces.append(audit_trace_path(trace_path, base_dir=base))

    all_issues = run_issues + [item for trace in traces for item in trace["issues"]]
    counts = Counter(item["category"] for item in all_issues)
    error_count = sum(1 for item in all_issues if item["severity"] == "error")
    action_issue_count = sum(
        1
        for item in all_issues
        if item["category"] in {"compound_segment", "embedded_cleanup"}
    )
    status = "pass"
    if error_count:
        status = "failed"
    elif action_issue_count:
        status = "needs_action_refinement"

    return {
        "schema_version": "0.1",
        "run_dir": str(root),
        "status": status,
        "video_trace_ready": error_count == 0,
        "execution_ready": error_count == 0 and action_issue_count == 0,
        "archive_recommended": error_count > 0,
        "trace_count": len(trace_paths),
        "summary_count": summary_len,
        "error_file_count": len(error_files),
        "issue_count": len(all_issues),
        "error_count": error_count,
        "action_refinement_issue_count": action_issue_count,
        "issue_counts": dict(sorted(counts.items())),
        "run_issues": run_issues,
        "traces": traces,
    }


def audit_trace_path(trace_path: str | Path, *, base_dir: str | Path | None = None) -> dict[str, Any]:
    path = Path(trace_path)
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    try:
        trace = Trace.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "trace_path": str(path),
            "status": "failed",
            "task_class": None,
            "trace_id": None,
            "segment_count": 0,
            "issues": [
                issue(
                    "error",
                    "schema_error",
                    f"trace.json does not match schema: {type(exc).__name__}: {exc}",
                    trace_path=str(path),
                )
            ],
        }

    issues = audit_trace(trace, trace_path=path, base_dir=base)
    status = "pass"
    if any(item["severity"] == "error" for item in issues):
        status = "failed"
    elif any(item["category"] in {"compound_segment", "embedded_cleanup"} for item in issues):
        status = "needs_action_refinement"
    return {
        "trace_path": str(path),
        "status": status,
        "task_class": trace.task_class,
        "trace_id": trace.trace_id,
        "segment_count": len(trace.segments),
        "issues": issues,
    }


def audit_trace(
    trace: Trace,
    *,
    trace_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    path_text = str(trace_path) if trace_path is not None else trace.trace_id
    issues: list[dict[str, Any]] = []

    audited = apply_trace_audit(trace.model_copy(deep=True))
    for warning in audited.quality_warnings:
        issues.append(
            issue(
                "error" if "object-role inversion" in warning else "warning",
                "trace_audit_warning",
                warning,
                trace_path=path_text,
            )
        )

    if trace.raw_response_path and not _resolve_path(trace.raw_response_path, base).exists():
        issues.append(
            issue(
                "error",
                "raw_response_missing",
                f"raw_response_path does not exist: {trace.raw_response_path}",
                trace_path=path_text,
            )
        )

    previous: TraceSegment | None = None
    for segment in trace.segments:
        issues.extend(_segment_field_issues(segment, path_text))
        issues.extend(_segment_time_issues(segment, previous, trace, path_text))
        issues.extend(_segment_evidence_issues(segment, path_text, base))
        reason = compound_segment_reason(segment)
        if reason:
            issues.append(
                issue(
                    "warning",
                    "compound_segment",
                    reason,
                    trace_path=path_text,
                    segment_index=segment.index,
                )
            )
        if segment.role == "cleanup":
            issues.append(
                issue(
                    "info",
                    "cleanup_segment",
                    "cleanup/retract/release segment retained as an explicit terminal action",
                    trace_path=path_text,
                    segment_index=segment.index,
                )
            )
        elif "release" in segment.label.lower() and segment.motion_type != "release":
            issues.append(
                issue(
                    "warning",
                    "embedded_cleanup",
                    "core segment label contains release; execution materialization should drop cleanup tail",
                    trace_path=path_text,
                    segment_index=segment.index,
                )
            )
        previous = segment
    return issues


def compound_segment_reason(segment: TraceSegment) -> str | None:
    label = segment.label.lower()
    if "align" in label and "insert" in label:
        return "segment merges fine alignment and insertion"
    if "align" in label and "pour" in label:
        return "segment merges fine alignment and pouring"
    if PICK_WORD_RE.search(label) and TERMINAL_WORD_RE.search(label):
        return "segment merges grasp/pick with a terminal placement/insertion/stack/pour event"
    return None


def write_quality_report(report: dict[str, Any], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def issue(
    severity: str,
    category: str,
    message: str,
    *,
    trace_path: str,
    segment_index: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "severity": severity,
        "category": category,
        "message": message,
        "trace_path": trace_path,
    }
    if segment_index is not None:
        payload["segment_index"] = segment_index
    return payload


def _segment_field_issues(segment: TraceSegment, trace_path: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for field in REQUIRED_ACTION_FIELDS:
        value = getattr(segment, field)
        if value in (None, "", []):
            issues.append(
                issue(
                    "error",
                    "missing_action_field",
                    f"segment is missing {field}",
                    trace_path=trace_path,
                    segment_index=segment.index,
                )
            )
    return issues


def _segment_time_issues(
    segment: TraceSegment,
    previous: TraceSegment | None,
    trace: Trace,
    trace_path: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if segment.end_sec <= segment.start_sec:
        issues.append(
            issue(
                "error",
                "bad_time",
                f"end_sec <= start_sec ({segment.start_sec} -> {segment.end_sec})",
                trace_path=trace_path,
                segment_index=segment.index,
            )
        )
    if previous is not None:
        gap = segment.start_sec - previous.end_sec
        if gap < -1e-6:
            issues.append(
                issue(
                    "error",
                    "overlap",
                    f"segment overlaps previous by {-gap:.3f}s",
                    trace_path=trace_path,
                    segment_index=segment.index,
                )
            )
        elif gap > 1.0:
            issues.append(
                issue(
                    "warning",
                    "large_gap",
                    f"gap from previous segment is {gap:.3f}s",
                    trace_path=trace_path,
                    segment_index=segment.index,
                )
            )
    if trace.video.duration_sec is not None and segment.end_sec > trace.video.duration_sec + 0.6:
        issues.append(
            issue(
                "error",
                "past_duration",
                f"segment ends after video duration ({segment.end_sec} > {trace.video.duration_sec})",
                trace_path=trace_path,
                segment_index=segment.index,
            )
        )
    return issues


def _segment_evidence_issues(
    segment: TraceSegment,
    trace_path: str,
    base_dir: Path,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not segment.evidence.contact_sheets:
        issues.append(
            issue(
                "warning",
                "evidence_missing",
                "segment has no contact sheet evidence",
                trace_path=trace_path,
                segment_index=segment.index,
            )
        )
        return issues
    for sheet in segment.evidence.contact_sheets:
        if not _resolve_path(sheet, base_dir).exists():
            issues.append(
                issue(
                    "error",
                    "contact_sheet_missing",
                    f"contact sheet does not exist: {sheet}",
                    trace_path=trace_path,
                    segment_index=segment.index,
                )
            )
    return issues


def _resolve_path(path: str | Path, base_dir: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return base_dir / candidate
