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
