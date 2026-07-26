from __future__ import annotations

from robot_subtask_seg.schema import Trace, TraceSegment


TASK_AUDIT_RULES: dict[str, dict[str, object]] = {
    "deposit_coin": {
        "static_targets": ["coin bank", "coin_bank", "coin-bank", "slot"],
        "requires_bimanual": True,
        "requires_alignment": True,
    },
    "insert_key": {
        "static_targets": ["key slot", "slot"],
        "requires_bimanual": True,
        "requires_alignment": True,
    },
    "insert_tubes": {
        "static_targets": ["rack"],
        "requires_alignment": True,
    },
    "plug_in_charger": {
        "static_targets": ["power strip", "socket", "outlet"],
        "requires_bimanual": True,
        "requires_alignment": True,
    },
    "pour_balls_into_vase": {
        "static_targets": ["vase"],
        "requires_bimanual": True,
    },
    "put_bottles_into_dustbin": {
        "static_targets": ["dustbin", "bin"],
    },
    "push_T": {
        "static_targets": ["pad"],
    },
    "push_T_random": {
        "static_targets": ["pad"],
    },
}


def apply_trace_audit(trace: Trace) -> Trace:
    rules = TASK_AUDIT_RULES.get(trace.task_class, {})
    warnings = list(trace.quality_warnings)
    static_targets = [str(item).lower() for item in rules.get("static_targets", [])]

    for segment in trace.segments:
        _audit_static_target_role(segment, static_targets, warnings)
        if segment.role == "cleanup":
            _append_once(segment.risk_flags, "cleanup_not_core")

    if rules.get("requires_bimanual") and not any(s.requires_bimanual for s in trace.segments):
        warnings.append("expected_bimanual_or_handover_not_detected")
    if rules.get("requires_alignment") and not any(s.requires_alignment for s in trace.segments):
        warnings.append("expected_fine_alignment_not_detected")

    trace.quality_warnings = _dedupe(warnings)
    return trace


def _audit_static_target_role(
    segment: TraceSegment,
    static_targets: list[str],
    warnings: list[str],
) -> None:
    manipulated = (segment.manipulated_object or "").lower()
    if not manipulated:
        return
    for target in static_targets:
        if target and target in manipulated:
            _append_once(segment.risk_flags, "object_role_inversion")
            warnings.append(
                f"possible object-role inversion: manipulated_object={segment.manipulated_object!r}"
            )
            return


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
