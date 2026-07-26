from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from robot_subtask_seg.contact_sheet import build_episode_contact_sheets
from robot_subtask_seg.normalize import extract_json_object
from robot_subtask_seg.prompts import operation_structure_prompt
from robot_subtask_seg.providers.base import TextVisionProvider
from robot_subtask_seg.schema import OperationStructureResponse, Trace


OPERATION_STRUCTURE_SCHEMA = "robot_subtask_seg.operation_structure.v1"
OperationStructureMode = Literal["visual_only", "evidence_guided"]


def build_operation_structure(
    trace: Trace,
    *,
    provider: TextVisionProvider,
    output_dir: str | Path,
    video_path: str | Path | None = None,
    mode: OperationStructureMode = "evidence_guided",
    demonstration_bundle: dict[str, Any] | None = None,
    sample_sec: float = 0.25,
    frame_width: int = 320,
    frames_per_sheet: int = 16,
    columns: int = 4,
    jpeg_quality: int = 95,
) -> dict[str, Any]:
    if mode == "evidence_guided" and demonstration_bundle is None:
        raise ValueError("evidence_guided mode requires a demonstration bundle")

    source_video = Path(video_path) if video_path is not None else Path(trace.video.path)
    if not source_video.exists():
        raise FileNotFoundError(source_video)

    out = Path(output_dir)
    sheet_dir = out / "contact_sheets"
    sheets = build_episode_contact_sheets(
        source_video,
        output_dir=sheet_dir,
        sample_sec=sample_sec,
        frame_width=frame_width,
        frames_per_sheet=frames_per_sheet,
        columns=columns,
        quality=jpeg_quality,
    )
    image_order = [
        f"{sheet.path.name}: {sheet.start_sec:.3f}s to {sheet.end_sec:.3f}s"
        for sheet in sheets
    ]
    prompt = operation_structure_prompt(
        instruction=trace.instruction,
        task_class=trace.task_class,
        duration_sec=trace.video.duration_sec,
        mode=mode,
        image_order=image_order,
        evidence_context=_evidence_context(
            trace,
            demonstration_bundle=demonstration_bundle if mode == "evidence_guided" else None,
        ),
    )
    out.mkdir(parents=True, exist_ok=True)
    prompt_path = out / "prompt.txt"
    raw_path = out / "raw_response.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    raw = provider.generate_json(prompt=prompt, image_paths=[sheet.path for sheet in sheets])
    raw_path.write_text(raw.strip() + "\n", encoding="utf-8")

    parsed = OperationStructureResponse.model_validate(extract_json_object(raw))
    result = _normalize_structure(
        parsed,
        trace=trace,
        mode=mode,
        provider=provider,
        prompt_path=prompt_path,
        raw_path=raw_path,
        image_paths=[sheet.path for sheet in sheets],
        source_video=source_video,
    )
    write_operation_structure(result, out / "operation_structure.json")
    return result


def write_operation_structure(structure: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(structure, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output


def _evidence_context(
    trace: Trace,
    *,
    demonstration_bundle: dict[str, Any] | None,
) -> str:
    if demonstration_bundle is None:
        return (
            "Additional structured evidence: none. Infer only from timestamped contact sheets and "
            "report anything they cannot establish as an evidence gap."
        )

    compact = {
        "coarse_trace_segments": [
            {
                "index": segment.index,
                "start_sec": segment.start_sec,
                "end_sec": segment.end_sec,
                "label": segment.label,
                "actor_arm": segment.actor_arm,
                "eef_event": segment.eef_event,
                "motion_type": segment.motion_type,
                "manipulated_object": segment.manipulated_object,
                "target_object": segment.target_object,
                "requires_alignment": segment.requires_alignment,
                "visual_evidence": segment.visual_evidence,
            }
            for segment in trace.segments
        ],
        "tracked_objects": [
            {
                key: item.get(key)
                for key in (
                    "object_id",
                    "prompt",
                    "reliable_frame_fraction",
                    "displacement_xy_px",
                    "path_length_px",
                    "occlusion_intervals",
                )
                if key in item
            }
            for item in demonstration_bundle.get("objects", [])
            if isinstance(item, dict)
        ],
        "segment_motion_evidence": demonstration_bundle.get("segment_evidence", []),
        "known_evidence_gaps": demonstration_bundle.get("evidence_gaps", []),
    }
    return (
        "Additional structured evidence follows. It is supporting evidence, not ground truth; "
        "resolve conflicts in favor of the images and preserve uncertainty.\n"
        + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    )


def _normalize_structure(
    parsed: OperationStructureResponse,
    *,
    trace: Trace,
    mode: OperationStructureMode,
    provider: TextVisionProvider,
    prompt_path: Path,
    raw_path: Path,
    image_paths: list[Path],
    source_video: Path,
) -> dict[str, Any]:
    duration = trace.video.duration_sec
    procedures: list[dict[str, Any]] = []
    procedure_ids: set[str] = set()
    warnings = list(parsed.quality_warnings)

    for procedure in parsed.canonical_procedures:
        if procedure.procedure_id in procedure_ids:
            warnings.append(f"duplicate procedure id dropped: {procedure.procedure_id}")
            continue
        procedure_ids.add(procedure.procedure_id)
        procedures.append(procedure.model_dump())

    instances: list[dict[str, Any]] = []
    instance_ids: set[str] = set()
    for instance in parsed.instances:
        if instance.instance_id in instance_ids:
            warnings.append(f"duplicate instance id dropped: {instance.instance_id}")
            continue
        if instance.procedure_ref not in procedure_ids:
            warnings.append(
                f"instance {instance.instance_id} references unknown procedure "
                f"{instance.procedure_ref}"
            )
            continue
        start = _clamp_time(instance.start_sec, duration)
        end = _clamp_time(instance.end_sec, duration)
        if end <= start:
            warnings.append(f"invalid instance window dropped: {instance.instance_id}")
            continue

        template_ids = {
            phase["phase_id"]
            for procedure in procedures
            if procedure["procedure_id"] == instance.procedure_ref
            for phase in procedure["phase_template"]
        }
        phases: list[dict[str, Any]] = []
        for phase in instance.phases:
            phase_start = max(start, _clamp_time(phase.start_sec, duration))
            phase_end = min(end, _clamp_time(phase.end_sec, duration))
            if phase_end <= phase_start:
                warnings.append(
                    f"invalid phase window dropped: {instance.instance_id}/{phase.phase_ref}"
                )
                continue
            if phase.phase_ref and phase.phase_ref not in template_ids:
                warnings.append(
                    f"unmatched phase ref kept as evidence: "
                    f"{instance.instance_id}/{phase.phase_ref}"
                )
            payload = phase.model_dump()
            payload["start_sec"] = round(phase_start, 3)
            payload["end_sec"] = round(phase_end, 3)
            phases.append(payload)

        payload = instance.model_dump()
        payload["start_sec"] = round(start, 3)
        payload["end_sec"] = round(end, 3)
        payload["phases"] = sorted(phases, key=lambda item: (item["start_sec"], item["end_sec"]))
        instances.append(payload)
        instance_ids.add(instance.instance_id)

    if not procedures:
        raise ValueError("operation structure contains no valid canonical procedures")
    if not instances:
        raise ValueError("operation structure contains no valid instances")

    sequence = [item for item in parsed.sequence if item in instance_ids]
    if len(sequence) != len(instance_ids):
        sequence = [
            item["instance_id"]
            for item in sorted(instances, key=lambda item: (item["start_sec"], item["end_sec"]))
        ]
        warnings.append("instance sequence was incomplete and was rebuilt from timestamps")

    if len(procedures) >= len(instances) and len(instances) > 1:
        warnings.append(
            "no repeated operation was abstracted; inspect whether object instances were "
            "incorrectly treated as separate procedures"
        )

    return {
        "schema": OPERATION_STRUCTURE_SCHEMA,
        "task_id": trace.task_id,
        "task_class": trace.task_class,
        "instruction": trace.instruction,
        "source_trace_id": trace.trace_id,
        "mode": mode,
        "canonical_procedures": procedures,
        "instances": instances,
        "sequence": sequence,
        "evidence_gaps": parsed.evidence_gaps,
        "quality_warnings": warnings,
        "summary": {
            "procedure_count": len(procedures),
            "instance_count": len(instances),
            "phase_count": sum(len(item["phases"]) for item in instances),
            "reused_procedure_count": sum(
                1
                for procedure_id in procedure_ids
                if sum(item["procedure_ref"] == procedure_id for item in instances) > 1
            ),
        },
        "provenance": {
            "generated_by": "robot-subtask-seg.refine-operation-structure",
            "provider": provider.name,
            "model": provider.model,
            "prompt_path": str(prompt_path),
            "raw_response_path": str(raw_path),
            "image_paths": [str(path) for path in image_paths],
            "source_video": str(source_video),
            "claims_final_skill_graph": False,
            "claims_robot_control_targets": False,
        },
    }


def _clamp_time(value: float, duration: float | None) -> float:
    result = max(0.0, float(value))
    if duration is not None:
        result = min(result, float(duration))
    return result
