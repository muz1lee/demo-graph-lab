"""Stage scoping and sequential runner regressions."""

from __future__ import annotations

import time
from types import SimpleNamespace

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


class _ResolvingRuntime:
    """带 ``_resolve``(图名 → 实体键)的最小 runtime,用来验证 runner 的注入接线。"""

    def __init__(self) -> None:
        self.positions = {"tube0_prop": [0.4, 0.2, 0.80]}
        self._active_stage_index = None

    def _entities(self, max_age_s: float = 0.0) -> dict:
        return {key: {"pos": list(value)} for key, value in self.positions.items()}

    def _resolve(self, name: str) -> str:
        return {"tube_left": "tube0_prop"}[name]

    def begin_stage(self, stage: dict) -> None:
        self._active_stage_index = int(stage["index"])

    def verify(self, constraint: dict) -> bool:
        return True


def _lift_stage() -> dict:
    return {
        "index": 0, "name": "lift",
        "stage_objects": {"manipulated": "tube_left"},
        "holes": [], "constraints": [],
        "acceptance": [{"name": "carry", "args": {}}],
    }


def test_runner_passes_the_runtime_resolver_to_the_gate() -> None:
    """runner 从 runtime 取 ``_resolve`` 交给 gate,位移检查才认得图对象名。"""
    runtime = _ResolvingRuntime()
    graph = {"stages": [_lift_stage()]}

    def handler(rt) -> None:
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    result = run_policy({0: handler}, graph, runtime)

    gate = result["stages"][0]["gate"]
    assert gate["manipulated_entity"] == "tube0_prop"
    assert gate["effect_status"] == "PASS"
    assert result["ok"] is True


class _PlainRuntime(_ResolvingRuntime):
    """同一个场景但 runtime 不提供 ``_resolve``,用来钉住退化路径。"""

    _resolve = None

    def __getattribute__(self, name):
        if name == "_resolve":
            raise AttributeError(name)
        return super().__getattribute__(name)


def test_runner_without_a_resolver_keeps_the_unknown_verdict() -> None:
    """runtime 没有 ``_resolve`` 时行为与现状一致:图名对不上实体键 → UNKNOWN。"""

    def handler(rt) -> None:
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    result = run_policy({0: handler}, {"stages": [_lift_stage()]}, _PlainRuntime())

    gate = result["stages"][0]["gate"]
    assert gate["manipulated_entity"] is None
    assert gate["effect_status"] == "UNKNOWN"
    assert result["ok"] is False


def test_gate_effect_works_end_to_end_with_the_oracle_resolver() -> None:
    """ep1 形态的接线:三根同别名的管 + 真 ``OracleRuntime._resolve`` + gate 位移检查。

    这条把靶子 1(解析不塌缩)和靶子 4(gate 拿到映射)接在一起:图名 ``tube_right``
    经空间双射落到 ``tube2_prop``,gate 就能观测到正确那根管的位移。
    """
    entities = {f"tube{i}_prop": {"pos": [0.4, y, 0.8]}
                for i, y in enumerate((0.20, 0.00, -0.20))}
    # 三个同类图名都要在图里,双射才有完整的两侧;本阶段只操作 tube_right。
    graph = {"stages": [
        {"index": index, "name": "lift", "holes": [], "constraints": [],
         "stage_objects": {"manipulated": name, "target": "rack"},
         "acceptance": [{"name": "carry", "args": {}}]}
        for index, name in enumerate(("tube_right", "tube_left", "tube_third"))]}
    registry = [{"id": name, "category": "tube", "distinguishers": [],
                 "trace_aliases": ["tube"], "first_seen_frame": 0}
                for name in ("tube_left", "tube_third", "tube_right")]

    runtime = OracleRuntime(graph, objects=registry)
    runtime._ents_cache = (time.time() + 1e6, entities)
    runtime.verify3 = lambda constraint: SimpleNamespace(status="PASS")

    def handler(rt) -> None:
        entities["tube2_prop"]["pos"] = [0.4, -0.20, 0.92]

    result = run_policy({0: handler}, {"stages": [graph["stages"][0]]}, runtime)

    gate = result["stages"][0]["gate"]
    assert gate["manipulated_entity"] == "tube2_prop", \
        "最右那根图名必须映到最右那个实体,而不是塌到 tube0_prop"
    assert gate["effect_status"] == "PASS"
    assert gate["manip_displacement_m"] == pytest.approx(0.12, abs=1e-9)
