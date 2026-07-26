from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from robot_subtask_seg.audit import apply_trace_audit
from robot_subtask_seg.contact_sheet import TimestampedContactSheet, build_episode_contact_sheets
from robot_subtask_seg.normalize import normalize_segmentation, overlap_warnings
from robot_subtask_seg.prompts import segmentation_prompt
from robot_subtask_seg.providers.base import TextVisionProvider
from robot_subtask_seg.schema import SegmentEvidence, Trace, TraceSegment, VideoInfo, VideoItem
from robot_subtask_seg.video import get_video_duration


def segment_video_item(
    item: VideoItem,
    *,
    config: dict[str, Any],
    output_dir: str | Path,
    provider: TextVisionProvider,
) -> Trace:
    task_dir = Path(output_dir) / item.task_class / _safe_name(item.task_id)
    sheets_dir = task_dir / "contact_sheets"
    task_dir.mkdir(parents=True, exist_ok=True)

    duration = get_video_duration(item.video_path)
    segmentation_cfg = config.get("segmentation", {})
    sheets = build_episode_contact_sheets(
        item.video_path,
        output_dir=sheets_dir,
        sample_sec=float(segmentation_cfg.get("sample_sec", 0.5)),
        frame_width=int(segmentation_cfg.get("frame_width", 224)),
        frames_per_sheet=int(segmentation_cfg.get("frames_per_sheet", 20)),
        columns=int(segmentation_cfg.get("columns", 5)),
        quality=int(segmentation_cfg.get("jpeg_quality", 95)),
    )

    prompt = segmentation_prompt(
        instruction=item.instruction,
        duration_sec=duration,
        task_class=item.task_class,
    )
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    raw = provider.generate_json(prompt=prompt, image_paths=[sheet.path for sheet in sheets])
    raw_path = task_dir / "raw_response.json"
    raw_path.write_text(raw.strip() + "\n", encoding="utf-8")

    normalized = normalize_segmentation(raw, duration_sec=duration)
    segments = normalized.segments
    warnings = overlap_warnings(segments)
    if warnings:
        (task_dir / "warnings.txt").write_text("\n".join(warnings) + "\n", encoding="utf-8")

    trace_segments: list[TraceSegment] = []
    for index, segment in enumerate(segments):
        matched_sheets = _sheets_for_segment(sheets, segment.start_sec, segment.end_sec)
        trace_segments.append(
            TraceSegment(
                index=index,
                start_sec=segment.start_sec,
                end_sec=segment.end_sec,
                label=segment.subtask,
                seed_label=segment.subtask,
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
                risk_flags=segment.risk_flags,
                method_note=segment.method_note,
                evidence=SegmentEvidence(
                    contact_sheets=[str(sheet.path) for sheet in matched_sheets],
                    timestamps=_timestamps_for_segment(matched_sheets, segment.start_sec, segment.end_sec),
                ),
            )
        )

    trace = Trace(
        trace_id=f"{item.task_class}__{Path(item.video_path).stem}",
        task_id=item.task_id,
        task_class=item.task_class,
        instruction=item.instruction,
        video=VideoInfo(path=item.video_path, duration_sec=duration),
        demonstration_method=normalized.demonstration_method,
        quality_warnings=normalized.quality_warnings,
        segments=trace_segments,
        model=provider.model,
        provider=provider.name,
        config=config,
        raw_response_path=str(raw_path),
    )
    trace = apply_trace_audit(trace)
    (task_dir / "trace.json").write_text(trace.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (task_dir / "item.json").write_text(item.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return trace


def _sheets_for_segment(
    sheets: list[TimestampedContactSheet],
    start_sec: float,
    end_sec: float,
) -> list[TimestampedContactSheet]:
    selected = [
        sheet
        for sheet in sheets
        if sheet.end_sec + 1e-6 >= start_sec and sheet.start_sec - 1e-6 <= end_sec
    ]
    return selected or sheets


def _timestamps_for_segment(
    sheets: list[TimestampedContactSheet],
    start_sec: float,
    end_sec: float,
) -> list[float]:
    timestamps: list[float] = []
    for sheet in sheets:
        timestamps.extend(t for t in sheet.timestamps if start_sec - 1e-6 <= t <= end_sec + 1e-6)
    return sorted(set(round(float(t), 3) for t in timestamps))


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_")


def trace_summary(trace: Trace) -> dict[str, Any]:
    return {
        "trace_id": trace.trace_id,
        "task_class": trace.task_class,
        "instruction": trace.instruction,
        "segment_count": len(trace.segments),
        "duration_sec": trace.video.duration_sec,
        "demonstration_method": trace.demonstration_method,
        "quality_warnings": trace.quality_warnings,
        "segments": [
            {
                "start_sec": segment.start_sec,
                "end_sec": segment.end_sec,
                "label": segment.label,
                "role": segment.role,
                "actor_arm": segment.actor_arm,
                "receiver_arm": segment.receiver_arm,
                "eef_event": segment.eef_event,
                "motion_type": segment.motion_type,
                "manipulated_object": segment.manipulated_object,
                "target_object": segment.target_object,
                "target_role": segment.target_role,
                "requires_bimanual": segment.requires_bimanual,
                "requires_alignment": segment.requires_alignment,
                "confidence": segment.confidence,
                "risk_flags": segment.risk_flags,
                "visual_evidence": segment.visual_evidence,
                "method_note": segment.method_note,
            }
            for segment in trace.segments
        ],
    }


def write_trace_summary(trace: Trace, path: str | Path) -> None:
    Path(path).write_text(json.dumps(trace_summary(trace), indent=2) + "\n", encoding="utf-8")
