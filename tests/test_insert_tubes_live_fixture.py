"""Reviewed live-contract checks for the insert-tubes research fixture."""

from __future__ import annotations

import json
from pathlib import Path
import re

from demo_graph_lab.demo.registry import validate_registry
from demo_graph_lab.graph.validate import (
    validate_final_graph,
    validate_live_hole_contract,
)


_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "graphs"
_TOTAL_FRAMES = 313


def _load(name: str):
    return json.loads((_FIXTURE_ROOT / name).read_text())


def test_insert_tubes_fixture_is_a_valid_reviewed_live_contract() -> None:
    graph = _load("insert_tubes.graph.json")
    objects = _load("insert_tubes.objects.json")
    registry_ids = {item["id"] for item in objects}
    stages = [
        {key: stage[key] for key in
         ("index", "name", "label", "start_sec", "end_sec")}
        for stage in graph["stages"]
    ]

    assert validate_registry(objects, _TOTAL_FRAMES) == []
    errors, _, checked = validate_final_graph(
        graph,
        stages,
        registry_ids,
        fps=25.0,
        total_frames=_TOTAL_FRAMES,
    )
    assert errors == []
    assert checked == 51
    assert validate_live_hole_contract(graph, registry_ids) == []


def test_insert_tubes_fixture_preserves_identity_and_hole_instances() -> None:
    graph = _load("insert_tubes.graph.json")
    objects = _load("insert_tubes.objects.json")
    stages = graph["stages"]

    tubes = [item for item in objects if item["category"] == "tube"]
    assert {item["id"] for item in tubes} == {
        "tube_left", "tube_mid", "tube_right"}
    assert {item["first_seen_frame"] for item in tubes} == {0}

    expected_tubes = (
        "tube_mid", "tube_mid", "tube_right",
        "tube_right", "tube_left", "tube_left",
    )
    assert tuple(
        stage["stage_objects"]["manipulated"] for stage in stages
    ) == expected_tubes
    for stage, expected_tube in zip(stages, expected_tubes):
        mentioned_tubes = set(re.findall(
            r"tube_(?:left|mid|right)", json.dumps(stage)))
        assert mentioned_tubes == {expected_tube}
        for hole in stage["holes"]:
            if (
                hole.get("resolver") in {"grasp_candidate", "principal_axis"}
                and hole.get("anchor", {}).get("object_id") == expected_tube
            ):
                assert hole["anchor"] == {
                    "object_id": expected_tube,
                    "part": "whole",
                }

    for stage_index, expected_instance in ((1, "center"), (3, "right"), (5, "left")):
        rack_geometry = {
            hole["resolver"]: hole
            for hole in stages[stage_index]["holes"]
            if hole.get("resolver") in {"part_center", "part_axis"}
        }
        center_anchor = rack_geometry["part_center"]["anchor"]
        axis_anchor = rack_geometry["part_axis"]["anchor"]
        assert center_anchor == axis_anchor == {
            "object_id": "rack",
            "part": "hole",
            "instance": expected_instance,
        }
