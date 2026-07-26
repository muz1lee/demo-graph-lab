from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from robot_subtask_seg.quality import compound_segment_reason
from robot_subtask_seg.schema import Trace, TraceSegment


def materialize_action_trace(
    trace: Trace,
    *,
    include_cleanup: bool = True,
    split_compound: bool = True,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    filtered_segments: list[dict[str, Any]] = []
    for segment in trace.segments:
        if segment.role == "cleanup" and not include_cleanup:
            filtered_segments.append(
                {
                    "source_segment_index": segment.index,
                    "label": segment.label,
                    "reason": "cleanup_filtered",
                }
            )
            continue
        derived = _split_compound_segment(segment) if split_compound else None
        if derived:
            steps.extend(derived)
        else:
            steps.append(_action_step(segment, boundary_source="model"))

    for index, step in enumerate(steps):
        step["index"] = index

    return {
        "schema_version": "0.1",
        "source_trace_id": trace.trace_id,
        "task_id": trace.task_id,
        "task_class": trace.task_class,
        "instruction": trace.instruction,
        "video": trace.video.model_dump(),
        "execution_ready": not any(step["boundary_source"] == "heuristic_split" for step in steps),
        "heuristic_split_count": sum(1 for step in steps if step["boundary_source"] == "heuristic_split"),
        "filtered_segment_count": len(filtered_segments),
        "filtered_segments": filtered_segments,
        "steps": steps,
    }


def write_action_trace(action_trace: dict[str, Any], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(action_trace, indent=2) + "\n", encoding="utf-8")


def _split_compound_segment(segment: TraceSegment) -> list[dict[str, Any]] | None:
    reason = compound_segment_reason(segment)
    if not reason:
        return None
    label = segment.label.lower()
    if "align" in label and "insert" in label:
        return _split_align_terminal(segment, terminal_event="insert", terminal_motion="insertion")
    if "align" in label and "pour" in label:
        return _split_align_terminal(segment, terminal_event="pour", terminal_motion="pour")
    if any(word in label for word in ["pick", "grasp", "grab", "lift"]):
        return _split_grasp_terminal(segment)
    return None


def _split_align_terminal(
    segment: TraceSegment,
    *,
    terminal_event: str,
    terminal_motion: str,
) -> list[dict[str, Any]]:
    split = _split_time(segment, ratio=0.45)
    align = _action_step(
        segment,
        start_sec=segment.start_sec,
        end_sec=split,
        label=f"align {segment.manipulated_object} with {segment.target_object}",
        eef_event="align",
        motion_type="fine_alignment",
        requires_alignment=True,
        boundary_source="heuristic_split",
        derivation_rule="split_align_terminal",
    )
    terminal = _action_step(
        segment,
        start_sec=split,
        end_sec=segment.end_sec,
        label=f"{terminal_event} {segment.manipulated_object} into {segment.target_object}",
        eef_event=terminal_event,
        motion_type=terminal_motion,
        boundary_source="heuristic_split",
        derivation_rule="split_align_terminal",
    )
    return [align, terminal]


def _split_grasp_terminal(segment: TraceSegment) -> list[dict[str, Any]]:
    split = _split_time(segment, ratio=0.35)
    grasp = _action_step(
        segment,
        start_sec=segment.start_sec,
        end_sec=split,
        label=f"grasp {segment.manipulated_object}",
        eef_event="grasp",
        motion_type="pick",
        target_object="surface",
        target_role="surface",
        requires_alignment=False,
        boundary_source="heuristic_split",
        derivation_rule="split_grasp_terminal",
    )
    terminal = _action_step(
        segment,
        start_sec=split,
        end_sec=segment.end_sec,
        label=_terminal_label(segment),
        boundary_source="heuristic_split",
        derivation_rule="split_grasp_terminal",
    )
    return [grasp, terminal]


def _terminal_label(segment: TraceSegment) -> str:
    obj = segment.manipulated_object or "object"
    target = segment.target_object or "target"
    event = (segment.eef_event or segment.motion_type or "place").lower()
    if event == "stack":
        return f"stack {obj} onto {target}"
    if event == "insert":
        return f"insert {obj} into {target}"
    if event == "pour":
        return f"pour {obj} into {target}"
    return f"place {obj} at {target}"


def _action_step(
    segment: TraceSegment,
    *,
    start_sec: float | None = None,
    end_sec: float | None = None,
    label: str | None = None,
    actor_arm: str | None = None,
    receiver_arm: str | None = None,
    eef_event: str | None = None,
    motion_type: str | None = None,
    manipulated_object: str | None = None,
    target_object: str | None = None,
    target_role: str | None = None,
    requires_bimanual: bool | None = None,
    requires_alignment: bool | None = None,
    boundary_source: str,
    derivation_rule: str | None = None,
) -> dict[str, Any]:
    step = {
        "index": -1,
        "source_segment_index": segment.index,
        "start_sec": round(float(start_sec if start_sec is not None else segment.start_sec), 3),
        "end_sec": round(float(end_sec if end_sec is not None else segment.end_sec), 3),
        "label": label or segment.label,
        "actor_arm": actor_arm or segment.actor_arm,
        "receiver_arm": receiver_arm if receiver_arm is not None else segment.receiver_arm,
        "eef_event": eef_event or segment.eef_event,
        "motion_type": motion_type or segment.motion_type,
        "manipulated_object": manipulated_object or segment.manipulated_object,
        "target_object": target_object if target_object is not None else segment.target_object,
        "target_role": target_role if target_role is not None else segment.target_role,
        "requires_bimanual": (
            segment.requires_bimanual if requires_bimanual is None else requires_bimanual
        ),
        "requires_alignment": (
            segment.requires_alignment if requires_alignment is None else requires_alignment
        ),
        "role": segment.role,
        "confidence": segment.confidence,
        "source_label": segment.label,
        "boundary_source": boundary_source,
    }
    if derivation_rule:
        step["derivation_rule"] = derivation_rule
    return step


def _split_time(segment: TraceSegment, *, ratio: float) -> float:
    return round(segment.start_sec + (segment.end_sec - segment.start_sec) * ratio, 3)
