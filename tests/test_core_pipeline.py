"""Pure logic tests that do not need video, network, or model dependencies."""

import pytest

from demo_graph_lab.demo.stages import from_trace
from demo_graph_lab.graph.extract import merge_samples
from demo_graph_lab.graph.validate import _is_metric_literal, check_item


def test_merge_majority_vote():
    c = lambda name, conf: {"name": name, "args": {"axis": "tube.long_axis"},
                            "confidence": conf, "evidence_frames": [1]}
    samples = [
        {"constraints": [c("axis_vertical", 0.9)], "acceptance": [], "holes": []},
        {"constraints": [c("axis_vertical", 0.8)], "acceptance": [], "holes": []},
        {"constraints": [c("axis_parallel", 0.9)], "acceptance": [], "holes": []},
    ]
    m = merge_samples(samples)
    names = [x["name"] for x in m["constraints"]]
    assert names == ["axis_vertical"], names          # 2/3 过半,1/3 淘汰
    assert m["constraints"][0]["votes"] == "2/3"


def test_merge_k1_keeps_all():
    s = [{"constraints": [{"name": "above", "args": {"obj_a": "tube0", "obj_b": "rack"},
                          "confidence": 0.7}], "acceptance": [], "holes": []}]
    assert len(merge_samples(s)["constraints"]) == 1


def test_metric_literal_rules():
    assert not _is_metric_literal("tube0")            # 带数字标识符 OK
    assert not _is_metric_literal("upper_body")
    assert _is_metric_literal(0.05)                   # 数值违规
    assert _is_metric_literal("0.05")
    assert _is_metric_literal("12 mm")
    assert _is_metric_literal({"offset": [0.1, 0.2]})


def test_check_item_vocab_and_literal():
    bad = {"name": "region_grasp", "args": {"obj": "tube0", "region": "somewhere"},
           "provenance": "demo_video"}
    errs = check_item(bad, 0, "constraints")
    assert any("非法 region" in e for e in errs)
    leak = {"name": "center_align", "args": {"obj_a": "bowl_a", "obj_b": 0.31},
            "provenance": "demo_video"}
    assert any("度量字面量" in e for e in check_item(leak, 1, "constraints"))
    good = {"name": "axis_vertical", "args": {"axis": "tube0.long_axis"},
            "provenance": "demo_video"}
    assert check_item(good, 2, "constraints") == []


def test_check_item_validates_argument_signature():
    wrong_key = {
        "name": "region_grasp",
        "args": {"object": "tube0", "region": "upper_body"},
        "provenance": "demo_video",
    }
    errors = check_item(wrong_key, 0, "constraints")
    assert any("缺少参数 ['obj']" in error for error in errors)
    assert any("未知参数 ['object']" in error for error in errors)

    extra = {
        "name": "axis_vertical",
        "args": {"axis": "tube0.long_axis", "bogus": "x"},
        "provenance": "demo_video",
    }
    assert any("未知参数 ['bogus']" in error
               for error in check_item(extra, 0, "constraints"))

    optional = {
        "name": "approach_direction",
        "args": {"cone": "side", "target": "tube0"},
        "provenance": "demo_video",
    }
    assert check_item(optional, 0, "constraints") == []


def test_carry_relation_accepts_registry_id_inside_snake_case_relation():
    item = {
        "name": "carry",
        "args": {"relation": "tube_right_in_gripper"},
        "provenance": "demo_video",
    }
    assert check_item(item, 0, "constraints", {"tube_right", "rack"}) == []

    item["args"]["relation"] = "unknown_object_in_gripper"
    errors = check_item(item, 0, "constraints", {"tube_right", "rack"})
    assert any("未引用 object registry" in error for error in errors)


def test_stages_from_trace():
    trace = {"segments": [
        {"index": 0, "start_sec": 0.0, "end_sec": 1.5, "label": "grasp tube",
         "motion_type": "grasp", "manipulated_object": "tube0", "role": "core"},
        {"index": 1, "start_sec": 1.5, "end_sec": 3.0, "label": "retract",
         "motion_type": "cleanup", "role": "cleanup"},
    ]}
    st = from_trace(trace)
    assert st[0]["name"] == "grasp" and st[0]["end_sec"] == 1.5
    assert st[1]["role"] == "cleanup"


def test_enrich_propagation_and_order():
    from demo_graph_lab.graph.enrich import add_order, propagate
    mk = lambda i, manip, cons: {"index": i, "name": "insertion", "role": "core",
                                 "stage_objects": {"manipulated": manip, "target": "rack"},
                                 "constraints": list(cons), "acceptance": []}
    inside = lambda o: {"name": "inside", "args": {"obj_a": o, "obj_b": "rack.hole"},
                        "holds": "at_end", "provenance": "demo_video",
                        "confidence": 0.8}
    graph = {"stages": [mk(1, "tube_a", [inside("tube_a")]),
                        mk(3, "tube_b", [inside("tube_b")]),
                        mk(5, "tube_c", [])]}
    n = propagate(graph)
    assert n == 1
    added = graph["stages"][2]["constraints"][0]
    assert added["args"]["obj_a"] == "tube_c" and added["provenance"] == "derived"
    assert added["holds"] == "at_end"
    assert add_order(graph)
    order = next(c for c in graph["stages"][0]["constraints"] if c["name"] == "order")
    assert order["holds"] == "throughout"
    assert propagate(graph) == 0        # derived 不作为来源,不链式扩散


def test_enrich_keeps_different_holds_patterns_separate():
    from demo_graph_lab.graph.enrich import propagate

    def stage(index, holds=None):
        constraints = [] if holds is None else [{
            "name": "inside",
            "args": {"obj_a": f"tube_{index}", "obj_b": "rack.hole"},
            "holds": holds,
            "provenance": "demo_video",
        }]
        return {
            "index": index,
            "name": "insertion",
            "role": "core",
            "stage_objects": {"manipulated": f"tube_{index}", "target": "rack"},
            "constraints": constraints,
            "acceptance": [],
        }

    graph = {"stages": [
        stage(0, "at_end"), stage(1, "at_end"),
        stage(2, "throughout"), stage(3),
    ]}
    assert propagate(graph) == 0
    assert graph["stages"][3]["constraints"] == []


def test_enrich_repairs_legacy_derived_holds_from_majority_sources():
    from demo_graph_lab.graph.enrich import add_order, propagate

    def stage(index, constraint):
        return {
            "index": index,
            "name": "insertion",
            "role": "core",
            "stage_objects": {"manipulated": f"tube_{index}", "target": "rack"},
            "constraints": [constraint] if constraint else [],
            "acceptance": [],
        }

    def inside(index, **extra):
        return {
            "name": "inside",
            "args": {"obj_a": f"tube_{index}", "obj_b": "rack.hole"},
            **extra,
        }

    graph = {"stages": [
        stage(0, inside(0, holds="at_end", provenance="demo_video")),
        stage(1, inside(1, holds="at_end", provenance="demo_video")),
        stage(2, inside(2, provenance="derived", derived_from=[0, 1])),
    ]}
    assert propagate(graph) == 1
    repaired = graph["stages"][2]["constraints"]
    assert len(repaired) == 1
    assert repaired[0]["holds"] == "at_end"
    assert repaired[0]["derived_from"] == [0, 1]

    graph["stages"][0]["constraints"].append({
        "name": "order", "args": {"stage_sequence": "s0<s1<s2"},
        "provenance": "derived",
    })
    assert add_order(graph) is False
    assert graph["stages"][0]["constraints"][-1]["holds"] == "throughout"


def test_enrich_does_not_propagate_one_vote_across_two_stages():
    from demo_graph_lab.graph.enrich import propagate

    def stage(index, cone=None):
        constraints = [] if cone is None else [{
            "name": "approach_direction",
            "args": {"cone": cone, "target": f"tube_{index}"},
            "provenance": "demo_video",
        }]
        return {
            "index": index,
            "name": "pick",
            "role": "core",
            "stage_objects": {"manipulated": f"tube_{index}", "target": None},
            "constraints": constraints,
            "acceptance": [],
        }

    graph = {"stages": [stage(0, "top_down"), stage(1)]}
    assert propagate(graph) == 0
    assert graph["stages"][1]["constraints"] == []


def test_enrich_adds_a_dedicated_lower_stop_control_hole():
    from demo_graph_lab.graph.enrich import add_control_holes

    graph = {"stages": [{
        "index": 1,
        "name": "insertion",
        "stage_objects": {"manipulated": "tube_left", "target": "rack"},
        "constraints": [{"name": "inside"}],
        "acceptance": [],
        "holes": [{"name": "insertion_depth", "type": "scalar"}],
    }]}
    assert add_control_holes(graph) == 1
    control = graph["stages"][0]["holes"][-1]
    assert control == {
        "name": "tube_left_lower_stop_condition",
        "type": "runtime_condition",
        "solver_hint": "non_privileged_contact_or_motion_plateau",
        "frame": "runtime",
        "purpose": "lower_stop",
        "votes": "derived",
    }
    assert add_control_holes(graph) == 0


def test_gates_vacuity_and_effect():
    """入口即为真的约束算空洞;操作类阶段必须有物体位移才放行。"""
    from demo_graph_lab.evaluation import gates

    class FakeRT:
        def __init__(self, positions, verdicts):
            self.positions, self.verdicts = positions, verdicts

        def _entities(self, max_age_s=0.0):
            return {k: {"pos": v} for k, v in self.positions.items()}

        def verify(self, c):
            return self.verdicts.get(c["name"], True)

    stage = {"index": 0, "name": "pick",
             "stage_objects": {"manipulated": "bowl0", "target": "table"},
             "acceptance": [{"name": "carry", "args": {}}]}

    # 场景 A:约束入口即为真、物体没动 → 空洞,不放行
    rt = FakeRT({"bowl0_prop": [0.5, 0.0, 0.79]}, {"carry": True})
    entry = gates.snapshot(rt, stage)
    v = gates.evaluate(rt, stage, entry)
    assert v["acceptance_hold"] and not v["passed"]   # 验收成立但缺少新 effect
    assert v["constraints_hold"]   # 该 stage 无 constraints → 空合取恒真
    assert v["vacuous_pass"] == 1 and v["informative_pass"] == 0
    assert "vacuous" in v["reason"]

    # 场景 B:物体真的被抬起 → 放行
    rt2 = FakeRT({"bowl0_prop": [0.5, 0.0, 0.79]}, {"carry": True})
    entry2 = gates.snapshot(rt2, stage)
    rt2.positions["bowl0_prop"] = [0.5, 0.0, 0.91]
    v2 = gates.evaluate(rt2, stage, entry2)
    assert v2["passed"] and v2["manip_displacement_m"] >= 0.05

    # 场景 C:非操作类阶段(retreat)不要求位移
    stage_c = dict(stage, name="retreat")
    rt3 = FakeRT({"bowl0_prop": [0.5, 0.0, 0.79]}, {"carry": True})
    v3 = gates.evaluate(rt3, stage_c, gates.snapshot(rt3, stage_c))
    assert v3["passed"] and not v3["needs_effect"]


def test_compile_static_check_rules():
    from demo_graph_lab.policy.compiler import static_check
    good = ("def stage_0(rt):\n    p = rt.solve('grasp_pose')\n    rt.grasp_at(p)\n"
            "STAGES = {0: stage_0}\n")
    assert static_check(good) == []
    assert any("数字字面量" in e for e in static_check(
        "def stage_0(rt):\n    x = 0.05\nSTAGES = {0: stage_0}\n"))
    assert any("import" in e for e in static_check("import os\nSTAGES = {}\n"))
    assert any("契约外" in e for e in static_check(
        "def stage_0(rt):\n    rt.teleport('x')\nSTAGES = {0: stage_0}\n"))
    assert any("只准调用" in e for e in static_check(
        "def stage_0(rt):\n    print('hi')\nSTAGES = {0: stage_0}\n"))
    assert any("契约外" in e for e in static_check(
        "def stage_0(rt):\n    rt.push('x', 'y', 'z')\nSTAGES = {0: stage_0}\n"))
    assert any("契约外" in e for e in static_check(
        "def stage_0(rt):\n    rt.verify({})\nSTAGES = {0: stage_0}\n"))
    assert any("禁止下标读取" in e for e in static_check(
        "def stage_0(rt):\n    h = rt.solve('p')\n    rt.approach(h['xyz'])\n"
        "STAGES = {0: stage_0}\n"))
    assert any("禁止属性读取" in e for e in static_check(
        "def stage_0(rt):\n    rt.approach(rt.pipe)\nSTAGES = {0: stage_0}\n"))


def test_runtime_api_surface_is_small_and_explicit():
    from demo_graph_lab.policy.compiler import _contract_methods

    assert _contract_methods() == {
        "solve",
        "approach",
        "grasp_at",
        "lift",
        "transport",
        "align",
        "lower_until",
        "release",
        "retreat",
    }


def test_compile_dry_run_mini():
    from demo_graph_lab.policy.compiler import dry_run
    graph = {"stages": [{"index": 0, "name": "pick",
                         "acceptance": [{"name": "carry", "args": {}}],
                         "holes": [{"name": "grasp_pose", "type": "pose_se3"}]}]}
    code = ("def stage_0(rt):\n    rt.grasp_at(rt.solve('grasp_pose'))\n"
            "STAGES = {0: stage_0}\n")
    r = dry_run(code, graph)
    assert r["normal"]["ok"] and r["holes_solved"] == ["grasp_pose"]
    assert r["retry_injection"]["ok"]        # 注入一次 gate 失败 → 重试后通过
    assert r["retry_injection"]["stages"][0]["status"] == "passed_retry1"


def test_policy_loader_requires_exact_stage_handlers():
    from demo_graph_lab.policy.compiler import load_handlers

    graph = {"stages": [{"index": 0}, {"index": 1}]}
    code = "def stage_0(rt):\n    rt.release()\nSTAGES = {0: stage_0}\n"
    with pytest.raises(ValueError, match="do not match graph"):
        load_handlers(code, graph)


def test_fake_runtime_exposes_only_supported_actions():
    from demo_graph_lab.policy.fake_runtime import FakeRuntime
    graph = {"stages": [{"index": 0, "name": "s", "acceptance": [], "holes": []}]}
    rt = FakeRuntime(graph)

    rt.grasp_at("p")
    rt.approach("p")
    assert [c["op"] for c in rt.calls] == ["grasp_at", "approach"]
    for unavailable in ("push", "teleport", "residual"):
        with pytest.raises(AttributeError):
            getattr(rt, unavailable)


def test_norm_item_salvages_toplevel_args():
    from demo_graph_lab.graph.extract import _norm_item
    it = {"name": "region_grasp", "object": "tube0", "region": "upper_body",
          "confidence": 0.8, "evidence_frames": [1]}
    _norm_item(it)
    assert it["args"]["region"] == "upper_body"
    assert it["args"]["object"] == "tube0"
    assert "confidence" not in it["args"]

    approach = {"name": "approach_direction", "args": ["side", "tube0"]}
    _norm_item(approach)
    assert approach["args"] == {"cone": "side", "target": "tube0"}


def test_as_numbers_parses_numpy_style_string():
    """回归:pipeline 对 ndarray 直接 str(),回来是空格分隔、无逗号的字符串。

    json.loads / literal_eval 都无法解析该形态;若力信号因此变成 None,
    接触检测将无法工作。这条测试钉住空格分隔数值的解析行为。
    """
    from demo_graph_lab.execution.oracle_runtime import _as_numbers
    got = _as_numbers("[[-25.47078369 -11.25156104  38.69975227]]")
    assert got == pytest.approx([-25.47078369, -11.25156104, 38.69975227])
    assert max(abs(v) for v in got) == pytest.approx(38.69975227)
    # 常规形态。
    assert _as_numbers([[1.0, -2.0], [3.0]]) == pytest.approx([1.0, -2.0, 3.0])
    assert _as_numbers("1.5e-3 -2") == pytest.approx([0.0015, -2.0])
    # 解析不出数字 → 空列表(调用方判"读不到",不 fail-open 成 0)
    assert _as_numbers("nothing here") == []
    assert _as_numbers(None) == []
