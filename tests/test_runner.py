"""Stage scoping and sequential runner regressions."""

from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

import pytest

from demo_graph_lab.evaluation import predicates
from demo_graph_lab.execution.oracle_runtime import OracleRuntime
from demo_graph_lab.execution.runner import _CTX_VECTOR_OPS, _stage_ctx, run_policy
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


# ==========================================================================
# ctx 接线:runner 把 runtime 本阶段记下的抓取点/接近方向交给 gate。
# 没有这一步,region_grasp / approach_direction 的几何实现虽然完整,却因为拿不到
# 输入而永远 UNKNOWN(ep1/ep2 两集实测)。
# ==========================================================================
class _RecordingRuntime:
    """带 ``calls`` 记录的最小 runtime;verify3 直通真谓词,ctx 到没到一目了然。"""

    _TUBE = {"tube0": {"pos": [0.4, 0.2, 0.8],
                       "aabb": {"min": [0.0, 0.0, 0.0], "max": [0.1, 0.1, 1.0]}}}

    def __init__(self) -> None:
        self.calls: list = []
        self.positions = {"tube0_prop": [0.4, 0.2, 0.80]}

    def _entities(self, max_age_s: float = 0.0) -> dict:
        return {key: {"pos": list(value)} for key, value in self.positions.items()}

    def verify3(self, constraint: dict, **ctx):
        return predicates.check(constraint, self._TUBE, **ctx)


def _grasp_stage() -> dict:
    return {"index": 0, "name": "grasp", "holes": [], "constraints": [],
            "stage_objects": {},
            "acceptance": [{"name": "region_grasp",
                            "args": {"obj": "tube0", "region": "upper_body"}}]}


def _run_grasp(handler) -> dict:
    runtime = _RecordingRuntime()
    result = run_policy({0: handler}, {"stages": [_grasp_stage()]}, runtime,
                        max_attempts=1)
    return result["stages"][0]["gate"]


def test_runner_feeds_the_recorded_grasp_point_to_the_gate() -> None:
    def handler(rt) -> None:
        rt.calls.append({"op": "grasp_point", "xyz": [0.05, 0.05, 0.90]})
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    gate = _run_grasp(handler)
    assert gate["acceptance_hold"] is True          # 抓在上段 → 真的判出 PASS
    assert gate["n_unknown"] == 0


def test_runner_ctx_does_not_make_the_gate_lenient() -> None:
    """拿到抓取点不等于放行:抓在下段就该 FAIL。"""
    def handler(rt) -> None:
        rt.calls.append({"op": "grasp_point", "xyz": [0.05, 0.05, 0.10]})
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    gate = _run_grasp(handler)
    assert gate["acceptance_hold"] is False
    assert gate["passed"] is False


def test_runner_without_a_recorded_grasp_point_keeps_the_unknown_verdict() -> None:
    """runtime 没记抓取点 → 维持现状:UNKNOWN,不猜一个点。"""
    def handler(rt) -> None:
        rt.calls.append({"op": "grasp_close", "angle": 0.0})
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    gate = _run_grasp(handler)
    assert gate["acceptance_hold"] is None
    assert gate["n_unknown"] == 1
    assert gate["passed"] is False


def test_runner_ignores_records_from_before_this_stage_attempt() -> None:
    """窗口按 attempt 划:上一阶段留下的抓取点不能拿来给这一阶段验收。"""
    runtime = _RecordingRuntime()
    runtime.calls.append({"op": "grasp_point", "xyz": [0.05, 0.05, 0.90]})

    def handler(rt) -> None:
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    result = run_policy({0: handler}, {"stages": [_grasp_stage()]}, runtime,
                        max_attempts=1)
    assert result["stages"][0]["gate"]["acceptance_hold"] is None


def test_runner_takes_the_latest_record_and_rejects_malformed_ones() -> None:
    """同一阶段记了多次 → 取最近一次;形态不对的记录当作没记。"""
    def handler(rt) -> None:
        rt.calls.append({"op": "grasp_point", "xyz": [0.05, 0.05, 0.10]})
        rt.calls.append({"op": "grasp_point", "xyz": [0.05, 0.05, 0.90]})
        rt.calls.append({"op": "approach_dir", "dir": "down"})        # 不是向量
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    gate = _run_grasp(handler)
    assert gate["acceptance_hold"] is True          # 用了最后那次(上段)

    def only_bad(rt) -> None:
        rt.calls.append({"op": "grasp_point", "xyz": [0.05, "x", 0.90]})
        rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]

    assert _run_grasp(only_bad)["acceptance_hold"] is None


# ==========================================================================
# ctx 通道的字段名必须和生产端对得上(8/6 ep3)。
#
# runtime 记的是 ``{"op": <名字>, **载荷}``:``{"op":"grasp_point","xyz":[...]}`` /
# ``{"op":"approach_dir","dir":[...]}``。消费端原先做的是 ``record.get("grasp_point")``
# ——名字在 ``op`` 里、向量在 ``xyz``/``dir`` 里,那个同名字段在仓里没有任何生产者写过,
# 于是 ctx 恒空、两条谓词永远 UNKNOWN。下面直接喂**真实记录形状**,不再自己发明形状。
# ==========================================================================
_REAL_GRASP_RECORD = {"t": 1.0, "op": "grasp_point", "xyz": [0.4013, 0.1988, 0.8102]}
# ep3 实测的接近方向(下探段起止回读位移归一化)。
_EP3_APPROACH_DIR = [-0.046, -0.016, -0.999]
_REAL_APPROACH_RECORD = {"t": 1.1, "op": "approach_dir", "dir": _EP3_APPROACH_DIR,
                         "source": "measured_descent"}


def test_stage_ctx_reads_the_real_runtime_record_shape() -> None:
    runtime = SimpleNamespace(calls=[_REAL_GRASP_RECORD, _REAL_APPROACH_RECORD])

    ctx = _stage_ctx(runtime, 0)

    assert ctx == {"grasp_point": pytest.approx(_REAL_GRASP_RECORD["xyz"]),
                   "approach_dir": pytest.approx(_EP3_APPROACH_DIR)}


def test_the_same_named_field_reading_would_see_nothing() -> None:
    """反向验证:按 ``record.get("grasp_point")`` 读真实记录,两个键都取不到。"""
    for record in (_REAL_GRASP_RECORD, _REAL_APPROACH_RECORD):
        assert record.get("grasp_point") is None
        assert record.get("approach_dir") is None


def test_ep3_measured_approach_dir_reaches_the_predicate_and_passes() -> None:
    """ep3 实测值回归:这条方向离竖直向下只有 2.8°,接上通道后必须判 PASS。"""
    runtime = SimpleNamespace(calls=[_REAL_APPROACH_RECORD])
    ctx = _stage_ctx(runtime, 0)

    p = predicates.check({"name": "approach_direction", "args": {"cone": "top_down"}},
                         {}, **ctx)

    assert p.status == predicates.PASS
    assert float(p.detail.split("angle=")[1].split()[0]) == pytest.approx(2.8, abs=0.1)


def test_oracle_runtime_evidence_records_match_the_consumed_field_names() -> None:
    """生产端与消费端同源对账:oracle 记的 op 名与载荷字段名就是 runner 读的那两组。"""
    assert _CTX_VECTOR_OPS == {"grasp_point": "xyz", "approach_dir": "dir"}
    source = inspect.getsource(OracleRuntime._log_grasp_evidence)
    for op, field in _CTX_VECTOR_OPS.items():
        assert f'self._log("{op}"' in source, f"生产端不再记 {op}"
        assert f"{field}=" in source, f"生产端不再用 {field} 装向量"
