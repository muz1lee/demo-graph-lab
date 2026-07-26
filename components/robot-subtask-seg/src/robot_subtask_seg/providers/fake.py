from __future__ import annotations

import json
import re
from pathlib import Path


class FakeProvider:
    name = "fake"
    model = "fake-segmenter"

    def generate_json(self, *, prompt: str, image_paths: list[Path]) -> str:
        if "Build a reuse-aware operational trace" in prompt:
            return _fake_operation_structure_response(prompt)
        if "Refine one parent segment" in prompt:
            return _fake_refinement_response(prompt)
        duration = _duration_from_prompt(prompt) or 6.0
        if duration <= 3.0:
            segments = [
                {
                    "start_sec": 0.0,
                    "end_sec": duration,
                    "subtask": "perform task",
                    "eef_event": "move",
                    "motion_type": "unknown",
                    "role": "core",
                }
            ]
        else:
            a = round(duration * 0.33, 3)
            b = round(duration * 0.66, 3)
            segments = [
                {
                    "start_sec": 0.0,
                    "end_sec": a,
                    "subtask": "approach and grasp object",
                    "eef_event": "grasp",
                    "motion_type": "pick",
                    "manipulated_object": "object",
                    "role": "core",
                },
                {
                    "start_sec": a,
                    "end_sec": b,
                    "subtask": "move object toward target",
                    "eef_event": "move",
                    "motion_type": "transport",
                    "manipulated_object": "object",
                    "target_object": "target",
                    "role": "core",
                },
                {
                    "start_sec": b,
                    "end_sec": round(duration, 3),
                    "subtask": "place or align object",
                    "eef_event": "place",
                    "motion_type": "place",
                    "manipulated_object": "object",
                    "target_object": "target",
                    "role": "core",
                },
            ]
        return json.dumps(
            {
                "demonstration_method": ["grasp object", "move object to target", "place object"],
                "quality_warnings": [],
                "segments": segments,
            }
        )


def _duration_from_prompt(prompt: str) -> float | None:
    match = re.search(r"Video duration:\s*([0-9.]+)s", prompt)
    if not match:
        return None
    return float(match.group(1))


def _fake_refinement_response(prompt: str) -> str:
    start, end = _parent_window_from_prompt(prompt)
    split = round(start + (end - start) * 0.4, 3)
    return json.dumps(
        {
            "demonstration_method": ["grasp object", "place object at target"],
            "quality_warnings": [],
            "segments": [
                {
                    "start_sec": start,
                    "end_sec": split,
                    "subtask": "grasp object",
                    "actor_arm": "unknown",
                    "receiver_arm": "none",
                    "eef_event": "grasp",
                    "motion_type": "pick",
                    "manipulated_object": "object",
                    "target_object": "surface",
                    "target_role": "surface",
                    "requires_bimanual": False,
                    "requires_alignment": False,
                    "role": "core",
                    "confidence": 0.5,
                    "visual_evidence": "fake provider visual prior",
                    "risk_flags": [],
                    "method_note": "fake grasp",
                },
                {
                    "start_sec": split,
                    "end_sec": end,
                    "subtask": "place object at target",
                    "actor_arm": "unknown",
                    "receiver_arm": "none",
                    "eef_event": "place",
                    "motion_type": "place",
                    "manipulated_object": "object",
                    "target_object": "target",
                    "target_role": "receptacle",
                    "requires_bimanual": False,
                    "requires_alignment": False,
                    "role": "core",
                    "confidence": 0.5,
                    "visual_evidence": "fake provider visual prior",
                    "risk_flags": [],
                    "method_note": "fake place",
                },
            ],
        }
    )


def _parent_window_from_prompt(prompt: str) -> tuple[float, float]:
    match = re.search(r"Parent time window:\s*([0-9.]+)s\s*to\s*([0-9.]+)s", prompt)
    if match:
        return float(match.group(1)), float(match.group(2))
    return 0.0, 2.0


def _fake_operation_structure_response(prompt: str) -> str:
    duration = _duration_from_prompt(prompt) or 6.0
    midpoint = round(duration / 2.0, 3)
    phase_template = [
        {
            "phase_id": "acquire",
            "intent": "establish a stable grasp on the bound object",
            "entry_state": ["object is supported by the scene"],
            "exit_state": ["object is held"],
            "constraints": ["grasp remains stable before transport"],
            "observable_evidence": ["gripper closes and object begins moving with it"],
            "optional": False,
        },
        {
            "phase_id": "place",
            "intent": "move the held object into the bound target relation and release",
            "entry_state": ["object is held"],
            "exit_state": ["object remains at the target after release"],
            "constraints": ["target relation is maintained through release"],
            "observable_evidence": ["object stops at target while gripper retreats"],
            "optional": False,
        },
    ]

    def instance(index: int, start: float, end: float) -> dict[str, object]:
        split = round(start + (end - start) * 0.4, 3)
        return {
            "instance_id": f"instance_{index}",
            "procedure_ref": "relocate_object",
            "start_sec": start,
            "end_sec": end,
            "bindings": {
                "manipulated_object": f"object_{index}",
                "target_object": "target",
                "actor_arm": "unknown",
            },
            "phases": [
                {
                    "phase_ref": "acquire",
                    "start_sec": start,
                    "end_sec": split,
                    "description": f"grasp object {index}",
                    "evidence_basis": ["timestamped contact sheet"],
                    "evidence_refs": [f"instance:{index}:acquire"],
                    "confidence": 0.7,
                },
                {
                    "phase_ref": "place",
                    "start_sec": split,
                    "end_sec": end,
                    "description": f"place object {index}",
                    "evidence_basis": ["timestamped contact sheet"],
                    "evidence_refs": [f"instance:{index}:place"],
                    "confidence": 0.7,
                },
            ],
            "deviations": [],
            "evidence_gaps": [],
        }

    return json.dumps(
        {
            "canonical_procedures": [
                {
                    "procedure_id": "relocate_object",
                    "name": "relocate object to target",
                    "reusable_intent": "establish the same target relation for a bound object",
                    "parameters": ["manipulated_object", "target_object", "actor_arm"],
                    "phase_template": phase_template,
                }
            ],
            "instances": [
                instance(0, 0.0, midpoint),
                instance(1, midpoint, round(duration, 3)),
            ],
            "sequence": ["instance_0", "instance_1"],
            "evidence_gaps": [],
            "quality_warnings": [],
        }
    )
