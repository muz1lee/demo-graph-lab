"""Deterministic demo-bundle to non-metric constraint-graph extraction."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from adapters.demo_bundle import (
    DemoBundle,
    RefinedTrace,
    load_demo_bundle,
    load_refined_traces,
)

from .models import Constraint, ConstraintGraph, Node, TypedHole
from .provenance import Provenance, ProvenanceSource


_PICK_MOTIONS = frozenset({"pick", "grasp"})
_CARRY_MOTIONS = frozenset({"transport", "move"})
_INSERT_MOTIONS = frozenset({"insert", "insertion"})


@dataclass(frozen=True, slots=True)
class _OperationCycle:
    index: int
    grasp_segment: Mapping[str, Any]
    insert_segment: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    graph: ConstraintGraph
    diagnostics: Mapping[str, Any]


def _segment_index(segment: Mapping[str, Any]) -> int:
    return int(segment.get("index", -1))


def _segment_confidence(segment: Mapping[str, Any]) -> float:
    value = float(segment.get("confidence", 1.0))
    return min(1.0, max(0.0, value))


def _is_pick(segment: Mapping[str, Any]) -> bool:
    motion = str(segment.get("motion_type") or "").lower()
    event = str(segment.get("eef_event") or "").lower()
    label = str(segment.get("label") or "").lower()
    return (
        motion in _PICK_MOTIONS
        or event in {"grasp", "lift"}
        or "grasp" in label
        or "pick" in label
    )


def _is_carry(segment: Mapping[str, Any]) -> bool:
    motion = str(segment.get("motion_type") or "").lower()
    label = str(segment.get("label") or "").lower()
    return motion in _CARRY_MOTIONS and ("lift" in label or "transport" in label)


def _is_insert(segment: Mapping[str, Any]) -> bool:
    motion = str(segment.get("motion_type") or "").lower()
    event = str(segment.get("eef_event") or "").lower()
    label = str(segment.get("label") or "").lower()
    return (
        motion in _INSERT_MOTIONS
        or event == "insert"
        or "insert" in label
    )


def _operation_cycles(
    segments: Sequence[Mapping[str, Any]],
) -> tuple[_OperationCycle, ...]:
    cycles: list[_OperationCycle] = []
    pending_grasp: Mapping[str, Any] | None = None
    for segment in segments:
        if _is_pick(segment) or _is_carry(segment):
            pending_grasp = segment
        if not _is_insert(segment):
            continue
        grasp_segment = pending_grasp or segment
        cycles.append(
            _OperationCycle(
                index=len(cycles),
                grasp_segment=grasp_segment,
                insert_segment=segment,
            )
        )
        pending_grasp = None
    if not cycles:
        raise ValueError("demo trace contains no grasp/carry-to-insert operation cycle")
    return tuple(cycles)


def _demo_provenance(
    segment: Mapping[str, Any],
    claim: str,
    *,
    confidence: float | None = None,
) -> Provenance:
    index = _segment_index(segment)
    return Provenance(
        source=ProvenanceSource.DEMO_VIDEO,
        reference=f"segment:{index}:{claim}",
        confidence=(
            _segment_confidence(segment)
            if confidence is None
            else min(1.0, max(0.0, confidence))
        ),
    )


def _hole(
    *,
    hole_id: str,
    value_type: str,
    solver: str,
    search_domain: Mapping[str, Any],
    provenance: Provenance,
    shape: tuple[int, ...],
    unit: str,
    frame: str,
    required_inputs: tuple[str, ...],
    runtime_verification: tuple[str, ...],
) -> TypedHole:
    return TypedHole(
        hole_id=hole_id,
        value_type=value_type,
        solver=solver,
        search_domain=search_domain,
        provenance=provenance,
        shape=shape,
        unit=unit,
        frame=frame,
        required_inputs=required_inputs,
        runtime_verification=runtime_verification,
    )


def _relation_description(
    relation: Mapping[str, Any] | None,
) -> tuple[str, str, str]:
    if relation is None:
        return (
            "demo_contact_region_unresolved",
            "demo_approach_axis_unresolved",
            "demo_closing_direction_unresolved",
        )
    region = str((relation.get("region") or {}).get("label") or "unknown_region")
    approach = str(
        (relation.get("approach_axis") or {}).get("relation")
        or "unknown_approach"
    )
    closing = str(
        (relation.get("closing_direction") or {}).get("relation")
        or "unknown_closing_direction"
    )
    return region, approach, closing


def _pick_node(
    cycle: _OperationCycle,
    *,
    next_node: str,
    relation: Mapping[str, Any] | None,
) -> Node:
    number = cycle.index + 1
    segment = cycle.grasp_segment
    provenance = _demo_provenance(segment, "grasp_or_carry_event")
    region, approach, closing = _relation_description(relation)
    grasp_pose = _hole(
        hole_id=f"grasp_pose_{number}",
        value_type="pose_se3",
        solver="runtime_grasp_proposals",
        search_domain={
            "region": region,
            "approach_relation": approach,
            "closing_relation": closing,
            "metric_value": "unresolved_at_demo_time",
        },
        provenance=_demo_provenance(segment, "metric_grasp_pose_left_unresolved"),
        shape=(7,),
        unit="m_and_unit_quaternion",
        frame="runtime_robot_base",
        required_inputs=("current_rgbd", "instance_mask", "robot_kinematics"),
        runtime_verification=("reachability", "collision_free", "lift_retention"),
    )
    grasp_dof = _hole(
        hole_id=f"grasp_dof_{number}",
        value_type="dof_mask",
        solver="runtime_multiview_dof_estimator",
        search_domain={
            "locked": "infer_from_demo_relation_and_current_geometry",
            "free": "verify_before_execution",
        },
        provenance=_demo_provenance(segment, "grasp_dof_left_unresolved"),
        shape=(6,),
        unit="boolean_mask",
        frame="object_relative",
        required_inputs=("current_multiview_observation", "grasp_candidates"),
        runtime_verification=("candidate_preserves_downstream_insertion_axis",),
    )
    return Node(
        node_id=f"pick_{number}",
        action="pick",
        goal=f"tube_{number}_attached",
        controller_ref="trusted.pick",
        constraints=(
            Constraint(
                constraint_id=f"grasp_region_{number}",
                description=(
                    f"grasp region={region}; approach={approach}; "
                    f"closing_direction={closing}; no metric pose is copied"
                ),
                hole_ids=(grasp_pose.hole_id,),
                provenance=_demo_provenance(segment, "relative_grasp_relation"),
            ),
            Constraint(
                constraint_id=f"grasp_dof_constraint_{number}",
                description=(
                    "lock the demonstrated approach and closing relation; "
                    "leave symmetry/free DoF unresolved until runtime verification"
                ),
                hole_ids=(grasp_pose.hole_id, grasp_dof.hole_id),
                provenance=_demo_provenance(segment, "grasp_dof_requirement"),
            ),
        ),
        provenance=provenance,
        holes=(grasp_pose, grasp_dof),
        postconditions=(f"tube_{number}_attached", f"tube_{number}_lift_retained"),
        invariants=(f"tube_{number}_is_manipulated_object",),
        evidence_refs=(f"segment:{_segment_index(segment)}",),
        max_attempts=2,
        next_node=next_node,
        on_recoverable=f"pick_{number}",
    )


def _reorient_node(
    cycle: _OperationCycle,
    *,
    next_node: str,
) -> Node:
    number = cycle.index + 1
    segment = cycle.insert_segment
    tube_axis = _hole(
        hole_id=f"tube_axis_{number}",
        value_type="direction3",
        solver="runtime_object_axis_estimator",
        search_domain={"polarity": "resolve_from_current_sensor_evidence"},
        provenance=_demo_provenance(segment, "tube_axis_left_unresolved"),
        shape=(3,),
        unit="unitless",
        frame="runtime_robot_base",
        required_inputs=("current_rgbd", "tube_instance_mask"),
        runtime_verification=("axis_is_compatible_with_insertion_relation",),
    )
    return Node(
        node_id=f"reorient_{number}",
        action="reorient",
        goal=f"tube_{number}_insertion_compatible",
        controller_ref="trusted.reorient",
        constraints=(
            Constraint(
                constraint_id=f"reorientation_{number}",
                description=(
                    "tube long axis must become compatible with the demonstrated "
                    "insertion direction; skip if the relation already holds"
                ),
                hole_ids=(tube_axis.hole_id,),
                provenance=_demo_provenance(segment, "pre_insert_reorientation"),
            ),
            Constraint(
                constraint_id=f"carry_constraint_{number}",
                description="preserve the grasp relation while reorienting",
                provenance=_demo_provenance(segment, "carried_object_relation"),
            ),
        ),
        provenance=_demo_provenance(segment, "reorientation_event"),
        holes=(tube_axis,),
        preconditions=(f"tube_{number}_attached",),
        postconditions=(f"tube_{number}_insertion_compatible",),
        invariants=(f"tube_{number}_attached",),
        evidence_refs=(f"segment:{_segment_index(segment)}",),
        max_attempts=2,
        next_node=next_node,
        on_recoverable=f"reorient_{number}",
    )


def _align_node(
    cycle: _OperationCycle,
    *,
    next_node: str,
) -> Node:
    number = cycle.index + 1
    segment = cycle.insert_segment
    target_region = _hole(
        hole_id=f"target_region_{number}",
        value_type="region3",
        solver="runtime_empty_slot_detector",
        search_domain={
            "semantic_region": "empty_rack_slot",
            "resource_rule": "distinct_from_previously_used_slots",
        },
        provenance=_demo_provenance(segment, "target_region_left_unresolved"),
        shape=(3,),
        unit="m",
        frame="runtime_robot_base",
        required_inputs=("current_rgbd", "rack_detection", "occupied_slot_evidence"),
        runtime_verification=("target_is_empty", "target_is_inside_rack"),
    )
    insertion_axis = _hole(
        hole_id=f"insertion_axis_{number}",
        value_type="direction3",
        solver="runtime_slot_axis_estimator",
        search_domain={"relation": "tube_axis_parallel_to_slot_axis"},
        provenance=_demo_provenance(segment, "insertion_axis_left_unresolved"),
        shape=(3,),
        unit="unitless",
        frame="runtime_robot_base",
        required_inputs=("current_rgbd", "target_region"),
        runtime_verification=("tube_axis_aligned", "lateral_clearance_visible"),
    )
    return Node(
        node_id=f"align_{number}",
        action="align",
        goal=f"tube_{number}_aligned_to_empty_slot",
        controller_ref="trusted.align",
        constraints=(
            Constraint(
                constraint_id=f"placement_dof_{number}",
                description=(
                    "lock lateral alignment and relative axis orientation; "
                    "translation along the insertion axis remains free for insertion"
                ),
                hole_ids=(target_region.hole_id, insertion_axis.hole_id),
                provenance=_demo_provenance(segment, "placement_dof_relation"),
            ),
            Constraint(
                constraint_id=f"target_region_or_axis_{number}",
                description=(
                    "select an empty rack slot and align the tube long axis with "
                    "the observed slot axis"
                ),
                hole_ids=(target_region.hole_id, insertion_axis.hole_id),
                provenance=_demo_provenance(segment, "target_and_axis_relation"),
            ),
            Constraint(
                constraint_id=f"axis_clearance_{number}",
                description=(
                    "approach the selected empty slot without intersecting the "
                    "rack or tubes inserted by earlier graph nodes"
                ),
                hole_ids=(target_region.hole_id, insertion_axis.hole_id),
                provenance=_demo_provenance(segment, "pre_insert_clearance"),
            ),
        ),
        provenance=_demo_provenance(segment, "alignment_event"),
        holes=(target_region, insertion_axis),
        preconditions=(f"tube_{number}_attached",),
        postconditions=(f"tube_{number}_aligned_to_empty_slot",),
        invariants=(f"tube_{number}_attached",),
        evidence_refs=(f"segment:{_segment_index(segment)}",),
        max_attempts=2,
        next_node=next_node,
        on_recoverable=f"align_{number}",
    )


def _insert_node(
    cycle: _OperationCycle,
    *,
    next_node: str,
) -> Node:
    number = cycle.index + 1
    segment = cycle.insert_segment
    insertion_depth = _hole(
        hole_id=f"insertion_depth_{number}",
        value_type="length",
        solver="runtime_bounded_insertion",
        search_domain={
            "lower_bound": "current_contact_surface",
            "upper_bound": "runtime_geometry_and_robot_safety_envelope",
        },
        provenance=_demo_provenance(segment, "insertion_depth_left_unresolved"),
        shape=(1,),
        unit="m",
        frame="target_axis_relative",
        required_inputs=("aligned_pose", "runtime_progress_observation"),
        runtime_verification=("inserted_postcondition", "upright_postcondition"),
    )
    return Node(
        node_id=f"insert_{number}",
        action="insert",
        goal=f"tube_{number}_inserted",
        controller_ref="trusted.insert",
        constraints=(
            Constraint(
                constraint_id=f"insert_axis_{number}",
                description=(
                    "move only along the runtime-bound insertion axis while "
                    "preserving tube-to-slot alignment"
                ),
                hole_ids=(insertion_depth.hole_id,),
                provenance=_demo_provenance(segment, "insertion_axis_motion"),
            ),
            Constraint(
                constraint_id=f"insertion_clearance_{number}",
                description=(
                    "maintain clearance from rack boundaries and previously "
                    "inserted tubes; do not reuse an occupied slot"
                ),
                provenance=_demo_provenance(segment, "insertion_clearance"),
            ),
        ),
        provenance=_demo_provenance(segment, "insertion_event"),
        holes=(insertion_depth,),
        preconditions=(f"tube_{number}_aligned_to_empty_slot",),
        postconditions=(f"tube_{number}_inserted", f"tube_{number}_upright"),
        invariants=(f"tube_{number}_aligned_during_insertion",),
        evidence_refs=(f"segment:{_segment_index(segment)}",),
        max_attempts=2,
        next_node=next_node,
        on_recoverable=f"align_{number}",
    )


def _verify_node(
    cycle: _OperationCycle,
    *,
    next_node: str | None,
) -> Node:
    number = cycle.index + 1
    segment = cycle.insert_segment
    return Node(
        node_id=f"verify_{number}",
        action="verify",
        goal=f"tube_{number}_inserted_and_upright",
        controller_ref="trusted.verify",
        constraints=(
            Constraint(
                constraint_id=f"postcondition_{number}",
                description=(
                    "verify from allowed observations that the tube is inserted "
                    "and upright before advancing to the next tube"
                ),
                provenance=_demo_provenance(segment, "release_and_retract_outcome"),
            ),
        ),
        provenance=_demo_provenance(segment, "postcondition_event"),
        preconditions=(f"tube_{number}_inserted",),
        postconditions=(f"tube_{number}_inserted_and_upright",),
        evidence_refs=(f"segment:{_segment_index(segment)}",),
        max_attempts=1,
        next_node=next_node,
        on_recoverable=f"align_{number}",
    )


def _relations_by_segment(
    relations: Sequence[Mapping[str, Any]] | None,
) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for relation in relations or ():
        if "segment_index" not in relation:
            continue
        result[int(relation["segment_index"])] = relation
    return result


def _matching_trace(
    bundle: DemoBundle,
    refined_traces: Sequence[RefinedTrace],
) -> RefinedTrace | None:
    for trace in refined_traces:
        if trace.task_id == bundle.task_id:
            return trace
    return None


def extract_constraint_graph(
    bundle: DemoBundle,
    *,
    refined_traces: Sequence[RefinedTrace] = (),
    grasp_relations: Sequence[Mapping[str, Any]] | None = None,
) -> ExtractionResult:
    """Extract a non-metric repeated insertion graph from demo evidence."""

    matching = _matching_trace(bundle, refined_traces)
    segments = matching.segments if matching is not None else bundle.segments
    if matching is not None and bundle.segments:
        bundle_labels = tuple(str(item.get("label")) for item in bundle.segments)
        refined_labels = tuple(str(item.get("label")) for item in matching.segments)
        if bundle_labels != refined_labels:
            raise ValueError("bundle trace and matching refined trace disagree")
    cycles = _operation_cycles(segments)
    relation_map = _relations_by_segment(grasp_relations)
    nodes: list[Node] = []
    for cycle in cycles:
        number = cycle.index + 1
        next_cycle = (
            f"pick_{number + 1}" if cycle.index + 1 < len(cycles) else None
        )
        relation = relation_map.get(_segment_index(cycle.grasp_segment))
        nodes.extend(
            (
                _pick_node(cycle, next_node=f"reorient_{number}", relation=relation),
                _reorient_node(cycle, next_node=f"align_{number}"),
                _align_node(cycle, next_node=f"insert_{number}"),
                _insert_node(cycle, next_node=f"verify_{number}"),
                _verify_node(cycle, next_node=next_cycle),
            )
        )
    graph = ConstraintGraph(
        graph_id=f"{bundle.task_id}_constraint_graph",
        entry_node="pick_1",
        nodes=tuple(nodes),
        provenance=Provenance(
            source=ProvenanceSource.DEMO_VIDEO,
            reference=f"demonstration_bundle:{bundle.digest}",
        ),
    )
    constraint_ids = {
        constraint.constraint_id
        for node in graph.nodes
        for constraint in node.constraints
    }
    diagnostics = {
        "task_id": bundle.task_id,
        "bundle_digest": bundle.digest,
        "refined_trace_count": len(refined_traces),
        "matched_refined_trace": matching.path if matching is not None else None,
        "segment_count": len(segments),
        "operation_cycle_count": len(cycles),
        "node_count": len(graph.nodes),
        "typed_hole_count": sum(len(node.holes) for node in graph.nodes),
        "relation_count": len(relation_map),
        "coverage": {
            "grasp_region": any(item.startswith("grasp_region_") for item in constraint_ids),
            "grasp_dof": any(
                item.startswith("grasp_dof_constraint_") for item in constraint_ids
            ),
            "reorientation": any(
                item.startswith("reorientation_") for item in constraint_ids
            ),
            "placement_dof": any(
                item.startswith("placement_dof_") for item in constraint_ids
            ),
            "target_axis_clearance": any(
                item.startswith("axis_clearance_") for item in constraint_ids
            ),
            "postcondition": any(
                item.startswith("postcondition_") for item in constraint_ids
            ),
            "recovery": all(
                node.on_recoverable is not None
                for node in graph.nodes
                if node.action in {"pick", "reorient", "align", "insert"}
            ),
        },
    }
    return ExtractionResult(graph=graph, diagnostics=diagnostics)


def _load_relations(path: str | Path | None) -> tuple[Mapping[str, Any], ...]:
    if path is None:
        return ()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    relations = raw.get("relations") if isinstance(raw, Mapping) else raw
    if not isinstance(relations, list) or not all(
        isinstance(item, Mapping) for item in relations
    ):
        raise TypeError("relations input must be a list of objects")
    return tuple(dict(item) for item in relations)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract a non-metric ConstraintGraph from a demo bundle."
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--refined-root", required=True)
    parser.add_argument("--relations")
    parser.add_argument("--output", required=True)
    parser.add_argument("--diagnostics", required=True)
    args = parser.parse_args(argv)

    bundle = load_demo_bundle(args.bundle)
    refined_traces = load_refined_traces(args.refined_root)
    result = extract_constraint_graph(
        bundle,
        refined_traces=refined_traces,
        grasp_relations=_load_relations(args.relations),
    )
    output = Path(args.output)
    diagnostics = Path(args.diagnostics)
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostics.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.graph.to_json() + "\n", encoding="utf-8")
    diagnostics.write_text(
        json.dumps(
            dict(result.diagnostics),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
