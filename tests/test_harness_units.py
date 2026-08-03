"""harness 纯逻辑单测(不需 cv2/openai/网络)。pytest 或直接 python3 运行均可。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.extract import merge_samples
from harness.stages import from_trace
from harness.validate import _is_metric_literal, check_item


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
    from harness.enrich import add_order, propagate
    mk = lambda i, manip, cons: {"index": i, "name": "insertion", "role": "core",
                                 "stage_objects": {"manipulated": manip, "target": "rack"},
                                 "constraints": list(cons), "acceptance": []}
    inside = lambda o: {"name": "inside", "args": {"obj_a": o, "obj_b": "rack.hole"},
                        "provenance": "demo_video", "confidence": 0.8}
    graph = {"stages": [mk(1, "tube_a", [inside("tube_a")]),
                        mk(3, "tube_b", [inside("tube_b")]),
                        mk(5, "tube_c", [])]}
    n = propagate(graph)
    assert n == 1
    added = graph["stages"][2]["constraints"][0]
    assert added["args"]["obj_a"] == "tube_c" and added["provenance"] == "derived"
    assert add_order(graph)
    assert any(c["name"] == "order" for c in graph["stages"][0]["constraints"])
    assert propagate(graph) == 0        # derived 不作为来源,不链式扩散


def test_gates_vacuity_and_effect():
    """入口即为真的约束算空洞;操作类阶段必须有物体位移才放行。"""
    from harness import gates

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
    assert v["constraints_hold"] and not v["passed"]
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
    from harness.compilepolicy import static_check
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


def test_compile_dry_run_mini():
    from harness.compilepolicy import dry_run
    graph = {"stages": [{"index": 0, "name": "pick",
                         "acceptance": [{"name": "carry", "args": {}}],
                         "holes": [{"name": "grasp_pose", "type": "pose_se3"}]}]}
    code = ("def stage_0(rt):\n    rt.grasp_at(rt.solve('grasp_pose'))\n"
            "STAGES = {0: stage_0}\n")
    r = dry_run(code, graph)
    assert r["normal"]["ok"] and r["holes_solved"] == ["grasp_pose"]
    assert r["retry_injection"]["ok"]        # 注入一次 gate 失败 → 重试后通过
    assert r["retry_injection"]["stages"][0]["status"] == "passed_retry1"


def test_fakerun_push_raises_not_noop():
    """P0-06/G4:fake 运行时 push 必须 raise(与 kwadapter 硬 stub 同语义,D-14 挂起),
    不许被 __getattr__ 吞成 no-op;其余白名单原语仍 no-op 记日志。"""
    import pytest

    from harness.fakerun import FakeRuntime
    graph = {"stages": [{"index": 0, "name": "s", "acceptance": [], "holes": []}]}
    rt = FakeRuntime(graph)
    # push:干跑就红
    with pytest.raises(NotImplementedError):
        rt.push("objA", "contact", "toward")
    # 白名单原语:仍 no-op 且进日志
    rt.grasp_at("p")
    rt.approach("p")
    assert [c["op"] for c in rt.calls] == ["grasp_at", "approach"]
    # 契约外 API 仍 AttributeError
    with pytest.raises(AttributeError):
        rt.teleport


def test_compile_dry_run_push_is_red():
    """P0-06:调 push 的 policy 干跑必炸(NotImplementedError 冒泡),
    绝不再吞成绿。run() 层把它记为 dryrun_error(仍是红,非 normal.ok)。"""
    import pytest

    from harness.compilepolicy import dry_run
    graph = {"stages": [{"index": 0, "name": "shove",
                         "acceptance": [{"name": "carry", "args": {}}],
                         "holes": [{"name": "push_dir", "type": "axis_3d"}]}]}
    code = ("def stage_0(rt):\n    rt.push(rt.solve('push_dir'), None, None)\n"
            "STAGES = {0: stage_0}\n")
    with pytest.raises(NotImplementedError):
        dry_run(code, graph)


def test_norm_item_salvages_toplevel_args():
    from harness.extract import _norm_item
    it = {"name": "region_grasp", "object": "tube0", "region": "upper_body",
          "confidence": 0.8, "evidence_frames": [1]}
    _norm_item(it)
    assert it["args"]["region"] == "upper_body"
    assert it["args"]["object"] == "tube0"
    assert "confidence" not in it["args"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
