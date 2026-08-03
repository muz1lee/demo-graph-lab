"""harness.graspfunnel 单测(P0-10:两层漏斗组装)。

覆盖:L1 淘汰计数正确;L2 只改序不改集合;region/cone 双约束叠加排序稳定;
空集 REJECT_ALL + UnsolvedHole(funnel_empty);三态各一例;counts 字段齐全;L3 留位拒注入。
判据出处 docs/TODO.md §2 P0-10、§1.2 C-4/C-5;docs/PROPOSAL.md v4 §2.1/§2.3。
纯逻辑、离线、零 LLM/网络/机器人。pytest 或直接 python3 皆可跑(风格对齐 test_regions)。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import graspfunnel
from harness.binding import UnsolvedHole
from harness.graspfunnel import CandidateDecision, run_funnel


# --------------------------------------------------------------------------
# fixture:mock 候选。几何字段(height_fraction / approach_dir)供 L1/L2;
# provenance 字段供 CandidateSelector 冻结。构造数值是 fixture 不是答案。
# --------------------------------------------------------------------------
def _cand(cid, *, height_fraction=None, approach_dir=None, feasible=True):
    c = {
        "node_id": cid,
        "observation_revision": "rev-1",
        "observation_digest": "sha256:" + ("a" * 64),
        "perception_track": "mock_track",
        "frame": "world",
        "tcp_pose": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        "graph_constraints": ("mock_constraint",),
        "evidence_ids": ("ev-1",),
        "provenance_reference": "fixture",
        "feasible": feasible,
    }
    if height_fraction is not None:
        c["height_fraction"] = height_fraction
    if approach_dir is not None:
        c["approach_dir"] = approach_dir
    return c


def _feasible_pred(c):
    return c.get("feasible", True)


# ==========================================================================
# 1. L1 淘汰计数正确(唯一淘汰层)。
# ==========================================================================
def test_l1_elimination_counts():
    cands = [
        _cand("a", height_fraction=0.1, feasible=True),
        _cand("b", height_fraction=0.9, feasible=False),
        _cand("c", height_fraction=0.5, feasible=True),
        _cand("d", height_fraction=0.3, feasible=False),
    ]
    res = run_funnel(cands, feasibility_predicates=[_feasible_pred])
    assert res.counts.L1_in == 4
    assert res.counts.L1_out == 2          # b, d 被淘汰
    assert len(res.ranked) == 2


def test_l1_multiple_predicates_all_must_pass():
    # 两个谓词:一票否决 = 任一为假即淘汰。
    cands = [
        _cand("a", height_fraction=0.5, feasible=True),
        _cand("b", height_fraction=0.5, feasible=True),
    ]
    cands[0]["reachable"] = True
    cands[1]["reachable"] = False
    res = run_funnel(
        cands,
        feasibility_predicates=[_feasible_pred, lambda c: c.get("reachable", True)],
    )
    assert res.counts.L1_out == 1
    assert res.ranked[0]["node_id"] == "a"


def test_l1_no_predicates_keeps_all():
    cands = [_cand("a", height_fraction=0.2), _cand("b", height_fraction=0.8)]
    res = run_funnel(cands, feasibility_predicates=None)
    assert res.counts.L1_in == 2
    assert res.counts.L1_out == 2


# ==========================================================================
# 2. L2 只改序不改集合(不淘汰)。
# ==========================================================================
def test_l2_reorders_but_preserves_set():
    # upper_body 偏好 f(s)=s:高 s 在前。输入低→高,输出应翻转,集合不变。
    cands = [
        _cand("low", height_fraction=0.1),
        _cand("mid", height_fraction=0.5),
        _cand("high", height_fraction=0.9),
    ]
    res = run_funnel(cands, region="upper_body")
    ids_out = {c["node_id"] for c in res.ranked}
    assert ids_out == {"low", "mid", "high"}           # 集合不变
    assert len(res.ranked) == 3                          # 不淘汰
    assert [c["node_id"] for c in res.ranked] == ["high", "mid", "low"]  # 改序
    assert res.counts.L2_reordered == 1
    assert res.counts.top1_changed_by_L2 == 1


def test_l2_identity_when_already_sorted():
    # 输入已是 upper_body 最优序 → L2 不改序,计数为 0。
    cands = [
        _cand("high", height_fraction=0.9),
        _cand("mid", height_fraction=0.5),
        _cand("low", height_fraction=0.1),
    ]
    res = run_funnel(cands, region="upper_body")
    assert res.counts.L2_reordered == 0
    assert res.counts.top1_changed_by_L2 == 0


def test_l2_no_region_no_cone_is_identity():
    cands = [_cand("a", height_fraction=0.3), _cand("b", height_fraction=0.7)]
    res = run_funnel(cands)
    assert [c["node_id"] for c in res.ranked] == ["a", "b"]
    assert res.counts.L2_reordered == 0


# ==========================================================================
# 3. region/cone 双约束叠加排序稳定。
# ==========================================================================
def test_region_and_cone_stacked_stable():
    # top_down cone 参照轴 = -z;approach_dir 越接近 [0,0,-1] 分越高。
    # region=upper_body 为主键(高 s 在前),cone 为次键(等 s 内按对齐度)。
    cands = [
        _cand("hi_bad", height_fraction=0.9, approach_dir=[0, 0, 1]),   # 高 s,反向
        _cand("hi_good", height_fraction=0.9, approach_dir=[0, 0, -1]), # 高 s,对齐
        _cand("lo_good", height_fraction=0.1, approach_dir=[0, 0, -1]), # 低 s,对齐
    ]
    res = run_funnel(cands, region="upper_body", cone="top_down")
    order = [c["node_id"] for c in res.ranked]
    # 主键 region:两个高 s 在低 s 前。
    assert order.index("hi_bad") < order.index("lo_good")
    assert order.index("hi_good") < order.index("lo_good")
    # 次键 cone:等 s(两个 0.9)内,对齐的 hi_good 排在 hi_bad 前。
    assert order.index("hi_good") < order.index("hi_bad")


def test_stacked_deterministic_repeatable():
    cands = [
        _cand("a", height_fraction=0.4, approach_dir=[0, 0, -1]),
        _cand("b", height_fraction=0.6, approach_dir=[1, 0, 0]),
        _cand("c", height_fraction=0.6, approach_dir=[0, 0, -1]),
    ]
    r1 = run_funnel(cands, region="bottom", cone="top_down")
    r2 = run_funnel(cands, region="bottom", cone="top_down")
    assert [c["node_id"] for c in r1.ranked] == [c["node_id"] for c in r2.ranked]


# ==========================================================================
# 4. 空集处置:L1 后为空 → REJECT_ALL + UnsolvedHole(funnel_empty)。
# ==========================================================================
def test_empty_after_l1_rejects_all_and_raises():
    cands = [
        _cand("a", height_fraction=0.5, feasible=False),
        _cand("b", height_fraction=0.5, feasible=False),
    ]
    with pytest.raises(UnsolvedHole) as ei:
        run_funnel(cands, feasibility_predicates=[_feasible_pred],
                   hole={"name": "mock_hole"})
    assert ei.value.reason == "funnel_empty"
    assert ei.value.layer == "L2_bind"        # UnsolvedHole 归因层固定


def test_empty_input_rejects_all_and_raises():
    with pytest.raises(UnsolvedHole) as ei:
        run_funnel([], feasibility_predicates=[_feasible_pred])
    assert ei.value.reason == "funnel_empty"


# ==========================================================================
# 5. 三态各一例:SELECT / REJECT_ALL / REQUEST_EVIDENCE。
# ==========================================================================
def test_state_select():
    cands = [_cand("a", height_fraction=0.9), _cand("b", height_fraction=0.1)]
    res = run_funnel(cands, region="upper_body")
    assert res.selection.kind is CandidateDecision.SELECT
    assert res.selection.selected_index == 0
    assert res.selected["node_id"] == "a"      # top-1 = 最高 s


def test_state_reject_all():
    # REJECT_ALL 态由空集触发(同时抛 UnsolvedHole);此处验证 selection 对象本身。
    cands = [_cand("a", feasible=False)]
    try:
        run_funnel(cands, feasibility_predicates=[_feasible_pred])
    except UnsolvedHole:
        pass  # 期望:空集抛异常。REJECT_ALL 的 SelectionResult 语义见下一断言。
    # 直接验证选择器三态可达性:空候选 → REJECT_ALL。
    from method.demo_graph.candidates import CandidateSelector
    d = CandidateSelector().reject_all([], reason="funnel_empty")
    assert d.kind is CandidateDecision.REJECT_ALL


def test_state_request_evidence():
    cands = [_cand("a", height_fraction=0.5)]
    res = run_funnel(cands, request_evidence_reason="need better mask")
    assert res.selection.kind is CandidateDecision.REQUEST_EVIDENCE
    assert res.selected is None                # 非 SELECT 态无选中候选


# ==========================================================================
# 6. counts 字段齐全。
# ==========================================================================
def test_counts_fields_complete():
    cands = [
        _cand("a", height_fraction=0.1, feasible=True),
        _cand("b", height_fraction=0.9, feasible=True),
        _cand("c", height_fraction=0.5, feasible=False),
    ]
    res = run_funnel(cands, region="upper_body",
                     feasibility_predicates=[_feasible_pred])
    counts = res.counts
    assert counts.L1_in == 3
    assert counts.L1_out == 2
    assert counts.L2_reordered in (0, 1)
    assert counts.top1_changed_by_L2 in (0, 1)
    # 四字段全在(dataclass 冻结:属性访问不抛即齐全)。
    for fld in ("L1_in", "L1_out", "L2_reordered", "top1_changed_by_L2"):
        assert hasattr(counts, fld)


def test_result_layers_two_only():
    # MVS 阶段漏斗只有两层,报表不得写成三层。
    res = run_funnel([_cand("a", height_fraction=0.5)])
    assert res.layers == ("L1", "L2")


# ==========================================================================
# 7. L3 留位:downstream_rank_fn 默认 None;注入即拒(未交付 T-BP)。
# ==========================================================================
def test_l3_placeholder_default_none_ok():
    res = run_funnel([_cand("a", height_fraction=0.5)], downstream_rank_fn=None)
    assert res.selection.kind is CandidateDecision.SELECT


def test_l3_injection_rejected():
    with pytest.raises(NotImplementedError):
        run_funnel([_cand("a", height_fraction=0.5)],
                   downstream_rank_fn=lambda ranked: ranked)


# ==========================================================================
# 8. 红线:本模块源码零任务名/物体名/度量魔数(源级自检)。
# ==========================================================================
def test_source_has_no_task_or_object_names():
    src = Path(graspfunnel.__file__).read_text(encoding="utf-8")
    # 五个已测任务名 + 常见物体名,任一出现即失败(与 regions 同规的门禁自检)。
    banned = ["insert_tubes", "stack_bowls", "deposit_coin", "push_T",
              "tube", "bowl", "coin"]
    lowered = src.lower()
    hits = [w for w in banned if w in lowered]
    assert hits == [], f"源码出现任务/物体名:{hits}"
