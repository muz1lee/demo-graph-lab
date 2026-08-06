"""Derive the constraint references that a CaP stage may compose explicitly.

The algorithm is intentionally small: a grasp stage gets its own qualitative
grasp preferences plus later constraints that mention the same manipulated
object.  Those later constraints are the finite-sample preimage specification
used to reject a locally valid grasp that makes the next stage impossible.
"""

from __future__ import annotations

from typing import Iterable


MODES = ("vanilla", "local", "backchain")
RANKING_CONSTRAINTS = frozenset({"region_grasp", "approach_direction"})


def constraint_ref(stage_index: int, offset: int, constraint: dict) -> str:
    """Stable human-readable reference used by generated CaP programs."""
    return f"s{stage_index}:c{offset}:{constraint.get('name')}"


def constraint_table(graph: dict) -> dict[str, tuple[int, dict]]:
    table: dict[str, tuple[int, dict]] = {}
    for stage in graph.get("stages", []):
        index = stage.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        for offset, constraint in enumerate(stage.get("constraints", [])):
            if not isinstance(constraint, dict):
                continue
            ref = constraint_ref(index, offset, constraint)
            table[ref] = (index, constraint)
    return table


def _mentions(value, object_id: str) -> bool:
    if isinstance(value, str):
        return value == object_id or value.startswith(object_id + ".")
    if isinstance(value, dict):
        return any(_mentions(item, object_id) for item in value.values())
    if isinstance(value, list):
        return any(_mentions(item, object_id) for item in value)
    return False


def _is_demo_constraint(constraint: dict) -> bool:
    # Derived cross-stage copies are useful graph facts but not independent demo
    # preferences.  Feeding both the original and its derived inverse to CaP can
    # manufacture a contradiction (for example top_down plus derived side).
    return constraint.get("provenance") != "derived"


def _candidate_dependent(constraint: dict, object_id: str) -> bool:
    """Whether a later constraint can change with the current grasp choice.

    Most such constraints name the manipulated object directly.  Gripper-to-
    environment clearance is the important indirect case: the rigid grasp
    transform decides where the gripper ends up relative to the rack even
    though the constraint arguments say ``gripper`` rather than the tube ID.
    """

    args = constraint.get("args", {})
    return (
        _mentions(args, object_id)
        or (
            constraint.get("name") == "clearance"
            and isinstance(args, dict)
            and args.get("obj_a") == "gripper"
        )
    )


def _grasp_holes(stage: dict) -> list[str]:
    return [
        hole["name"]
        for hole in stage.get("holes", [])
        if (isinstance(hole, dict)
            and isinstance(hole.get("name"), str)
            and hole.get("type") == "pose_se3"
            and hole.get("resolver") == "grasp_candidate")
    ]


def _refs(
    stage: dict,
    constraints: Iterable[dict],
    predicate,
) -> list[str]:
    index = stage["index"]
    result = []
    for offset, constraint in enumerate(constraints):
        if isinstance(constraint, dict) and predicate(constraint):
            result.append(constraint_ref(index, offset, constraint))
    return result


def selection_context(graph: dict, *, mode: str = "backchain") -> dict[int, dict]:
    """Return the exact constraint vocabulary exposed to each grasp stage.

    ``vanilla`` exposes no demo information. ``local`` exposes only the current
    stage's qualitative grasp preferences. ``backchain`` additionally exposes
    later constraints that mention the same manipulated object.
    """
    if mode not in MODES:
        raise ValueError(f"unknown selection mode {mode!r}; expected {MODES}")

    stages = graph.get("stages", [])
    result: dict[int, dict] = {}
    for position, stage in enumerate(stages):
        if not isinstance(stage, dict) or not _grasp_holes(stage):
            continue
        index = stage.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        manipulated = (stage.get("stage_objects") or {}).get("manipulated")
        current = []
        downstream = []
        if mode in {"local", "backchain"}:
            current = _refs(
                stage,
                stage.get("constraints", []),
                lambda item: (
                    item.get("name") in RANKING_CONSTRAINTS
                    and _is_demo_constraint(item)
                ),
            )
        if mode == "backchain" and isinstance(manipulated, str) and manipulated:
            for later in stages[position + 1:]:
                if not isinstance(later, dict):
                    continue
                if (later.get("stage_objects") or {}).get("manipulated") != manipulated:
                    continue
                downstream.extend(_refs(
                    later,
                    later.get("constraints", []),
                    lambda item: (
                        _is_demo_constraint(item)
                        and _candidate_dependent(item, manipulated)
                    ),
                ))
        result[index] = {
            "grasp_holes": _grasp_holes(stage),
            "current_constraints": current,
            "downstream_constraints": downstream,
        }
    return result
