"""harness.regions 单测(P0-03,改动 C-4/C-5)。

覆盖:六 region 偏好函数形状、稳定排序(等分保序)、rim/handle UNCHECKABLE 行为、
cone 角度偏好。纯逻辑、离线、pytest 或直接 python3 皆可跑(风格对齐 test_harness_units)。
判据出处 docs/TODO.md §1.2 C-4/C-5、§1.3 CC-1′。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import regions, vocab


# --------------------------------------------------------------------------
# 六 region 偏好函数形状(逐字对 TODO §1.2 C-4 表)。
# --------------------------------------------------------------------------
def test_pref_upper_body_monotone_increasing():
    f = regions.region_preference("upper_body")
    assert f(0.0) == 0.0 and f(1.0) == 1.0
    assert f(0.2) < f(0.5) < f(0.8)                 # 严格单调增


def test_pref_bottom_monotone_decreasing():
    f = regions.region_preference("bottom")
    assert f(0.0) == 1.0 and f(1.0) == 0.0
    assert f(0.2) > f(0.5) > f(0.8)                 # 严格单调减


def test_pref_middle_triangular_peak_at_half():
    f = regions.region_preference("middle")
    assert f(0.5) == pytest.approx(1.0)             # 峰在 s=0.5
    assert f(0.0) == pytest.approx(0.0) and f(1.0) == pytest.approx(0.0)
    assert f(0.3) < f(0.5) and f(0.7) < f(0.5)      # 两侧下降
    assert f(0.3) == pytest.approx(f(0.7))          # 对称


def test_pref_top_convex_increasing():
    f = regions.region_preference("top")
    assert f(0.0) == 0.0 and f(1.0) == 1.0
    assert f(0.5) == pytest.approx(0.25)            # s² 的凸性:中点分数被压低
    # 凸性:比同端点的线性(upper_body)在中段更低
    assert f(0.5) < regions.region_preference("upper_body")(0.5)


# --------------------------------------------------------------------------
# rim/handle → UNCHECKABLE(不许悄悄当 middle)。
# --------------------------------------------------------------------------
def test_rim_handle_uncheckable():
    for r in ("rim", "handle"):
        assert regions.region_preference(r) is regions.UNCHECKABLE


def test_uncheckable_ranking_is_identity_and_flagged():
    cands = [{"id": f"c{i}", "height_fraction": s} for i, s in enumerate([0.9, 0.1, 0.5])]
    for r in ("rim", "handle"):
        ranked, meta = regions.rank_by_region(cands, r, with_meta=True)
        assert [c["id"] for c in ranked] == ["c0", "c1", "c2"]   # 恒等,原序
        assert meta["uncheckable"] is True and meta["status"] == regions.UNCHECKABLE


def test_unknown_region_raises():
    with pytest.raises(ValueError):
        regions.region_preference("nonexistent_region")
    with pytest.raises(ValueError):
        regions.rank_by_region([], "nonexistent_region")


def test_all_grasp_regions_handled():
    """vocab.GRASP_REGIONS 每个值都被处理(可检查→函数;rim/handle→UNCHECKABLE),无遗漏。"""
    for r in vocab.GRASP_REGIONS:
        pref = regions.region_preference(r)
        assert pref is regions.UNCHECKABLE or callable(pref)


# --------------------------------------------------------------------------
# rank_by_region:排序方向 + 稳定性(等分保序)+ 不淘汰。
# --------------------------------------------------------------------------
def _cands(fractions):
    return [{"id": f"c{i}", "height_fraction": s} for i, s in enumerate(fractions)]


def test_rank_upper_vs_bottom_opposite_top1():
    cands = _cands([0.10, 0.30, 0.50, 0.70, 0.90])
    top_upper = regions.rank_by_region(cands, "upper_body")[0]
    top_bottom = regions.rank_by_region(cands, "bottom")[0]
    assert top_upper["height_fraction"] > top_bottom["height_fraction"]
    assert top_upper["height_fraction"] == 0.90 and top_bottom["height_fraction"] == 0.10


def test_rank_no_elimination_preserves_count():
    cands = _cands([0.10, 0.30, 0.50, 0.70, 0.90])
    for r in ("upper_body", "bottom", "middle", "top"):
        assert len(regions.rank_by_region(cands, r)) == len(cands)   # 不淘汰


def test_rank_stable_on_ties():
    """偏好分逐比特相同的候选保持输入相对次序(稳定排序)。
    upper_body 是恒等 f(s)=s,两个 s 相同的候选分数逐比特相同,是干净的等分用例
    (不受 middle 三角函数的浮点尾差干扰)。"""
    cands = [{"id": "a", "height_fraction": 0.5},
             {"id": "b", "height_fraction": 0.5},
             {"id": "c", "height_fraction": 0.9}]
    ranked = regions.rank_by_region(cands, "upper_body")
    assert ranked[0]["id"] == "c"                    # 0.9 分最高
    assert [x["id"] for x in ranked[1:]] == ["a", "b"]   # a,b 同分,保输入序


def test_rank_empty_returns_empty():
    assert regions.rank_by_region([], "top") == []


def test_rank_unrankable_candidates_sink_and_preserve_order():
    """s 不可算的候选沉到末尾且彼此保序,不被丢弃。"""
    cands = [{"id": "no1"}, {"id": "hi", "height_fraction": 0.9}, {"id": "no2"}]
    ranked = regions.rank_by_region(cands, "upper_body")
    assert ranked[0]["id"] == "hi"
    assert [x["id"] for x in ranked[1:]] == ["no1", "no2"]   # 保输入序,未丢


# --------------------------------------------------------------------------
# _height_fraction:几何路径(s = (p·u−min)/(max−min),全边长)。
# --------------------------------------------------------------------------
def test_height_fraction_from_geometry_full_extent():
    # 竖直物体 z∈[0.72,0.88],候选点 z=0.80 → s=(0.80−0.72)/(0.88−0.72)=0.5
    c = {"xyz": [0.42, 0.11, 0.80],
         "extent": {"min": [0.40, 0.09, 0.72], "max": [0.44, 0.13, 0.88]}}
    assert regions._height_fraction(c) == pytest.approx(0.5)


def test_height_fraction_prefers_explicit_field():
    c = {"height_fraction": 0.33, "xyz": [0, 0, 0.80],
         "extent": {"min": [0, 0, 0.72], "max": [0, 0, 0.88]}}
    assert regions._height_fraction(c) == 0.33


def test_height_fraction_none_when_ungeometrable():
    assert regions._height_fraction({"id": "x"}) is None


# --------------------------------------------------------------------------
# cone 角度偏好(C-5):approach 方向与锥轴夹角越小越优。
# --------------------------------------------------------------------------
def test_cone_axis_known_and_unknown():
    for cone in vocab.APPROACH_CONES:
        ax = regions.cone_axis(cone)
        assert ax is not None and len(ax) == 3
    with pytest.raises(ValueError):
        regions.cone_axis("nonexistent_cone")


def test_cone_angle_topdown_perfect_and_opposite():
    assert regions.cone_angle_deg([0, 0, -1], "top_down") == pytest.approx(0.0)
    assert regions.cone_angle_deg([0, 0, 1], "top_down") == pytest.approx(180.0)


def test_cone_preference_smaller_angle_scores_higher():
    down = regions.cone_preference([0, 0, -1], "top_down")     # 夹角 0 → 分最高(+1)
    side = regions.cone_preference([1, 0, 0], "top_down")      # 夹角 90 → 0
    up = regions.cone_preference([0, 0, 1], "top_down")        # 夹角 180 → −1
    assert down > side > up


def test_rank_by_cone_topdown_prefers_down():
    cands = [{"id": "down", "approach_dir": [0, 0, -1]},
             {"id": "side", "approach_dir": [1, 0, 0]},
             {"id": "up", "approach_dir": [0, 0, 1]}]
    ranked = regions.rank_by_cone(cands, "top_down")
    assert [c["id"] for c in ranked] == ["down", "side", "up"]


def test_rank_by_cone_side_prefers_horizontal():
    cands = [{"id": "down", "approach_dir": [0, 0, -1]},
             {"id": "side", "approach_dir": [1, 0, 0]}]
    ranked = regions.rank_by_cone(cands, "side")
    assert ranked[0]["id"] == "side"


def test_rank_by_cone_no_elimination():
    cands = [{"id": "a", "approach_dir": [0, 0, -1]},
             {"id": "b", "approach_dir": [1, 0, 0]},
             {"id": "c"}]                              # 无方向 → 沉末尾但不丢
    ranked = regions.rank_by_cone(cands, "top_down")
    assert len(ranked) == 3 and ranked[-1]["id"] == "c"


# --------------------------------------------------------------------------
# 纪律护栏:regions.py 源码零任务名/物体名(与门禁 grep 同规,自测一份)。
# --------------------------------------------------------------------------
def test_no_task_or_object_names_in_source():
    src = (Path(__file__).resolve().parents[1] / "harness" / "regions.py").read_text("utf-8").lower()
    for bad in ("insert_tube", "stack_bowl", "deposit", "push_t",
                "tube", "bowl", "coin", "rack", "slot"):
        assert bad not in src, f"regions.py 出现禁用词 {bad!r}(任务名/物体名硬失败)"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {str(e).splitlines()[0]}")
    print(f"{len(fns) - failed} passed, {failed} failed")
