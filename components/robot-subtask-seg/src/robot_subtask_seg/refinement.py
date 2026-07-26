from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from robot_subtask_seg.contact_sheet import build_segment_contact_sheet
from robot_subtask_seg.normalize import normalize_segmentation
from robot_subtask_seg.prompts import action_refinement_prompt
from robot_subtask_seg.providers.base import TextVisionProvider
from robot_subtask_seg.quality import compound_segment_reason
from robot_subtask_seg.schema import SegmentEvidence, Trace, TraceSegment


def refine_trace_actions(
    trace: Trace,
    *,
    config: dict[str, Any],
    output_dir: str | Path,
    provider: TextVisionProvider,
) -> tuple[Trace, dict[str, Any]]:
    out = Path(output_dir)
    evidence_root = out / "refinement_evidence"
    refined_segments: list[TraceSegment] = []
    manifest_items: list[dict[str, Any]] = []
    cfg = config.get("refinement", {})

    for index, segment in enumerate(trace.segments):
        reason = _refinement_reason(segment)
        if reason is None:
            refined_segments.append(segment.model_copy(deep=True))
            continue

        previous = trace.segments[index - 1] if index > 0 else None
        next_segment = trace.segments[index + 1] if index + 1 < len(trace.segments) else None
        segment_dir = evidence_root / f"segment_{segment.index:03d}"
        segment_dir.mkdir(parents=True, exist_ok=True)
        image_paths, image_order = _build_visual_context(
            trace,
            segment,
            previous=previous,
            next_segment=next_segment,
            output_dir=segment_dir,
            cfg=cfg,
        )
        prompt = action_refinement_prompt(
            instruction=trace.instruction,
            task_class=trace.task_class,
            parent_start=segment.start_sec,
            parent_end=segment.end_sec,
            parent_segment=_segment_brief(segment),
            previous_segment=_segment_brief(previous),
            next_segment=_segment_brief(next_segment),
            image_order=image_order,
            reason=reason,
        )
        prompt_path = segment_dir / "prompt.txt"
        raw_path = segment_dir / "raw_response.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        raw = provider.generate_json(prompt=prompt, image_paths=image_paths)
        raw_path.write_text(raw.strip() + "\n", encoding="utf-8")

        children, warnings = _refined_children(
            raw,
            parent=segment,
            trace=trace,
            evidence_paths=[str(path) for path in image_paths],
        )
        if not children:
            fallback = segment.model_copy(deep=True)
            fallback.risk_flags = sorted(set(fallback.risk_flags + ["refinement_failed_kept_parent"]))
            refined_segments.append(fallback)
        else:
            refined_segments.extend(children)
        manifest_items.append(
            {
                "source_segment_index": segment.index,
                "reason": reason,
                "prompt_path": str(prompt_path),
                "raw_response_path": str(raw_path),
                "image_paths": [str(path) for path in image_paths],
                "image_order": image_order,
                "child_count": len(children),
                "warnings": warnings,
            }
        )

    for index, segment in enumerate(refined_segments):
        segment.index = index

    refined = trace.model_copy(
        deep=True,
        update={
            "trace_id": f"{trace.trace_id}__refined_actions",
            "segments": refined_segments,
            "model": provider.model,
            "provider": provider.name,
            "config": config,
            "raw_response_path": str(out / "refinement_manifest.json"),
        },
    )
    manifest = {
        "schema_version": "0.1",
        "source_trace_id": trace.trace_id,
        "refined_trace_id": refined.trace_id,
        "task_id": trace.task_id,
        "task_class": trace.task_class,
        "provider": provider.name,
        "model": provider.model,
        "refined_segment_count": len(manifest_items),
        "items": manifest_items,
    }
    return refined, manifest


def write_refined_trace(trace: Trace, manifest: dict[str, Any], output_dir: str | Path) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "trace.json").write_text(trace.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (out / "refinement_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _refinement_reason(segment: TraceSegment) -> str | None:
    reason = compound_segment_reason(segment)
    if reason:
        return reason
    label = segment.label.lower()
    if "release" in label and segment.role != "cleanup":
        return "core segment includes an embedded release/cleanup tail"
    return None


def _build_visual_context(
    trace: Trace,
    segment: TraceSegment,
    *,
    previous: TraceSegment | None,
    next_segment: TraceSegment | None,
    output_dir: Path,
    cfg: dict[str, Any],
) -> tuple[list[Path], list[str]]:
    frame_width = int(cfg.get("frame_width", 336))
    max_frames = int(cfg.get("max_frames_per_segment", 5))
    columns = int(cfg.get("columns", 3))
    quality = int(cfg.get("jpeg_quality", 95))
    include_context = bool(cfg.get("include_context", True))
    pad = float(cfg.get("context_pad_sec", 0.0))

    image_paths: list[Path] = []
    image_order: list[str] = []
    if include_context and previous is not None:
        path = output_dir / "previous.jpg"
        _build_sheet(trace, previous, path, frame_width, max_frames, columns, quality, pad)
        image_paths.append(path)
        image_order.append(f"previous context segment {previous.index}: {_segment_brief(previous)}")

    current_path = output_dir / "current.jpg"
    _build_sheet(trace, segment, current_path, frame_width, max_frames, columns, quality, pad)
    image_paths.append(current_path)
    image_order.append(f"current parent segment {segment.index} to refine: {_segment_brief(segment)}")

    if include_context and next_segment is not None:
        path = output_dir / "next.jpg"
        _build_sheet(trace, next_segment, path, frame_width, max_frames, columns, quality, pad)
        image_paths.append(path)
        image_order.append(f"next context segment {next_segment.index}: {_segment_brief(next_segment)}")
    return image_paths, image_order


def _build_sheet(
    trace: Trace,
    segment: TraceSegment,
    path: Path,
    frame_width: int,
    max_frames: int,
    columns: int,
    quality: int,
    pad: float,
) -> None:
    start = max(0.0, segment.start_sec - pad)
    end = segment.end_sec + pad
    build_segment_contact_sheet(
        trace.video.path,
        output_path=path,
        start_sec=start,
        end_sec=end,
        frame_width=frame_width,
        max_frames=max_frames,
        columns=columns,
        quality=quality,
    )


def _refined_children(
    raw: str,
    *,
    parent: TraceSegment,
    trace: Trace,
    evidence_paths: list[str],
) -> tuple[list[TraceSegment], list[str]]:
    normalized = normalize_segmentation(raw, duration_sec=trace.video.duration_sec)
    warnings = list(normalized.quality_warnings)
    children: list[TraceSegment] = []
    for segment in normalized.segments:
        start, end = _coerce_child_times(segment.start_sec, segment.end_sec, parent)
        if end <= start:
            warnings.append(f"dropped invalid refined segment: {segment.subtask}")
            continue
        if start < parent.start_sec - 0.15 or end > parent.end_sec + 0.15:
            warnings.append(
                f"dropped out-of-window refined segment: {segment.start_sec}-{segment.end_sec}"
            )
            continue
        children.append(
            TraceSegment(
                index=0,
                start_sec=max(parent.start_sec, round(start, 3)),
                end_sec=min(parent.end_sec, round(end, 3)),
                label=segment.subtask,
                seed_label=parent.label,
                actor_arm=segment.actor_arm,
                receiver_arm=segment.receiver_arm,
                eef_event=segment.eef_event,
                motion_type=segment.motion_type,
                manipulated_object=segment.manipulated_object,
                target_object=segment.target_object,
                target_role=segment.target_role,
                requires_bimanual=segment.requires_bimanual,
                requires_alignment=segment.requires_alignment,
                role=segment.role,
                confidence=segment.confidence,
                visual_evidence=segment.visual_evidence,
                risk_flags=sorted(set(segment.risk_flags + ["model_refined_from_parent"])),
                method_note=segment.method_note
                or f"Model-refined visual substep: {segment.subtask}",
                evidence=SegmentEvidence(
                    contact_sheets=evidence_paths,
                    timestamps=[],
                ),
            )
        )
    return children, warnings


def _coerce_child_times(start: float, end: float, parent: TraceSegment) -> tuple[float, float]:
    parent_duration = parent.end_sec - parent.start_sec
    looks_like_local_time = (
        parent.start_sec > 0
        and 0 <= start <= parent_duration + 0.2
        and end <= parent_duration + 0.2
        and start < max(parent.start_sec - 0.05, 0.05)
    )
    if looks_like_local_time:
        return parent.start_sec + start, parent.start_sec + end
    return start, end


def _segment_brief(segment: TraceSegment | None) -> str:
    if segment is None:
        return "(none)"
    return (
        f"{segment.start_sec:.3f}-{segment.end_sec:.3f}s; "
        f"label={segment.label!r}; actor_arm={segment.actor_arm}; "
        f"event={segment.eef_event}; motion={segment.motion_type}; "
        f"object={segment.manipulated_object}; target={segment.target_object}; role={segment.role}"
    )
