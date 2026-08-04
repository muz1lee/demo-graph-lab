"""Stage scoping and sequential runner regressions."""

from __future__ import annotations

import pytest

from demo_graph_lab.execution.oracle_runtime import OracleRuntime
from demo_graph_lab.execution.runner import run_policy
from demo_graph_lab.policy.fake_runtime import FakeRuntime
from demo_graph_lab.selection.binding import UnsolvedHole


def _stage(index: int, hole_type: str = "scalar") -> dict:
    return {
        "index": index,
        "name": "inspect",
        "stage_objects": {},
        "holes": [{"name": "target_pose", "type": hole_type}],
        "constraints": [],
        "acceptance": [{"name": "axis_vertical", "args": {}}],
    }


def test_runner_scopes_reused_hole_names_by_stage() -> None:
    graph = {"stages": [_stage(0), _stage(1)]}
    runtime = FakeRuntime(graph)
    solved = []

    def handler(rt) -> None:
        solved.append((rt._active_stage_index, rt.solve("target_pose").name))

    result = run_policy({0: handler, 1: handler}, graph, runtime)

    assert result["ok"] is True
    assert solved == [(0, "target_pose"), (1, "target_pose")]


def test_oracle_runtime_rejects_ambiguous_hole_without_stage() -> None:
    graph = {"stages": [_stage(0, "scalar"), _stage(1, "runtime_condition")]}
    runtime = OracleRuntime(graph)

    with pytest.raises(UnsolvedHole) as error:
        runtime.solve("target_pose")
    assert error.value.reason == "ambiguous_hole"

    runtime.begin_stage(graph["stages"][0])
    assert runtime.solve("target_pose")["kind"] == "scalar"
    runtime.begin_stage(graph["stages"][1])
    assert runtime.solve("target_pose")["kind"] == "condition"


def test_runner_stops_when_a_stage_handler_is_missing() -> None:
    graph = {"stages": [_stage(0), _stage(1)]}
    runtime = FakeRuntime(graph)
    later_stage_ran = False

    def later_handler(rt) -> None:
        nonlocal later_stage_ran
        later_stage_ran = True

    result = run_policy({1: later_handler}, graph, runtime)

    assert result["ok"] is False
    assert result["failed_at"] == 0
    assert result["stages"] == [
        {"index": 0, "name": "inspect", "status": "no_handler"}
    ]
    assert later_stage_ran is False


def test_runner_rejects_zero_attempts() -> None:
    graph = {"stages": [_stage(0)]}
    with pytest.raises(ValueError, match="max_attempts"):
        run_policy({0: lambda rt: None}, graph, FakeRuntime(graph), max_attempts=0)


def test_runtimes_reject_duplicate_stage_indices() -> None:
    graph = {"stages": [_stage(0), _stage(0)]}
    with pytest.raises(ValueError, match="duplicate stage index"):
        FakeRuntime(graph)
    with pytest.raises(ValueError, match="duplicate stage index"):
        OracleRuntime(graph)
