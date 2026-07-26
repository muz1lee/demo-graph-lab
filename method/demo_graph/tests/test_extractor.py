from __future__ import annotations

import json
from pathlib import Path

from adapters.demo_bundle import load_demo_bundle, load_refined_traces
from method.demo_graph.extractor import extract_constraint_graph
from method.demo_graph.models import ConstraintGraph
from method.demo_graph.provenance import ProvenanceSource


def _segments():
    return [
        {
            "index": 0,
            "label": "Grasp and lift the first tube",
            "motion_type": "pick",
            "eef_event": "lift",
            "confidence": 0.9,
        },
        {
            "index": 1,
            "label": "Insert the first tube",
            "motion_type": "insertion",
            "eef_event": "insert",
            "confidence": 0.9,
        },
        {
            "index": 2,
            "label": "Grasp and lift the second tube",
            "motion_type": "pick",
            "eef_event": "grasp",
            "confidence": 0.9,
        },
        {
            "index": 3,
            "label": "Insert the second tube",
            "motion_type": "insertion",
            "eef_event": "insert",
            "confidence": 0.9,
        },
        {
            "index": 4,
            "label": "Lift and transport the third tube",
            "motion_type": "transport",
            "eef_event": "move",
            "confidence": 0.8,
        },
        {
            "index": 5,
            "label": "Insert the third tube",
            "motion_type": "insertion",
            "eef_event": "insert",
            "confidence": 0.9,
        },
    ]


def _write_inputs(tmp_path: Path):
    segments = _segments()
    bundle_path = tmp_path / "demonstration_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "task_id": "robodojo_insert_tubes__insert-tubes",
                "task_class": "insert_tubes",
                "instruction": "Insert the tubes one by one.",
                "trace": {"segments": segments},
                "objects": [{"object_id": "tube_track"}],
                "segment_evidence": [{"segment_index": 0}],
                "evidence_gaps": [
                    {"capability": "metric_depth", "reason": "RGB only"}
                ],
                "summary": {"segment_count": 6},
                "artifact_refs": {},
            }
        ),
        encoding="utf-8",
    )
    trace_path = (
        tmp_path
        / "refined"
        / "insert_tubes"
        / "robodojo_insert_tubes__insert-tubes"
        / "trace.json"
    )
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "task_id": "robodojo_insert_tubes__insert-tubes",
                "segments": segments,
            }
        ),
        encoding="utf-8",
    )
    return bundle_path, tmp_path / "refined"


def test_nested_bundle_and_refined_trace_feed_constraint_extractor(tmp_path: Path):
    bundle_path, refined_root = _write_inputs(tmp_path)
    bundle = load_demo_bundle(bundle_path)
    traces = load_refined_traces(refined_root)
    relations = [
        {
            "segment_index": index,
            "region": {"label": "upper_body"},
            "approach_axis": {"relation": "axial"},
            "closing_direction": {"relation": "cross_axis"},
        }
        for index in (0, 2, 4)
    ]
    result = extract_constraint_graph(
        bundle,
        refined_traces=traces,
        grasp_relations=relations,
    )

    graph = result.graph
    graph.assert_action_sequence(
        (
            "pick",
            "reorient",
            "align",
            "insert",
            "verify",
        )
        * 3
    )
    assert len(graph.nodes) == 15
    assert result.diagnostics["operation_cycle_count"] == 3
    assert all(result.diagnostics["coverage"].values())
    assert "region=upper_body" in graph.node("pick_1").constraints[0].description


def test_extracted_graph_has_complete_holes_and_demo_provenance(tmp_path: Path):
    bundle_path, refined_root = _write_inputs(tmp_path)
    result = extract_constraint_graph(
        load_demo_bundle(bundle_path),
        refined_traces=load_refined_traces(refined_root),
    )
    graph = ConstraintGraph.from_json(result.graph.to_json())

    assert graph.provenance.source is ProvenanceSource.DEMO_VIDEO
    for node in graph.nodes:
        assert node.provenance.source is ProvenanceSource.DEMO_VIDEO
        for constraint in node.constraints:
            assert constraint.provenance.source is ProvenanceSource.DEMO_VIDEO
        for hole in node.holes:
            assert hole.provenance.source is ProvenanceSource.DEMO_VIDEO
            assert hole.shape
            assert hole.unit != "unspecified"
            assert hole.frame != "unspecified"
            assert hole.required_inputs
            assert hole.runtime_verification
