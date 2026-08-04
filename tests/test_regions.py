"""任务无关 region 偏好的纯逻辑测试。

覆盖:六 region 偏好函数形状、稳定排序(等分保序)、rim/handle UNCHECKABLE 行为、
cone 角度偏好。纯逻辑、离线，由 pytest 运行。
These tests pin the task-independent preference semantics.
"""

from pathlib import Path

import pytest

from demo_graph_lab.graph import vocab
from demo_graph_lab.selection import regions
from demo_graph_lab.selection.candidates import CandidateBundle, deterministic_select


# --------------------------------------------------------------------------
# 六 region 偏好函数形状。
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


def test_region_meta_distinguishes_missing_and_partial_features():
    missing, missing_meta = regions.rank_by_region(
        [{"id": "a"}, {"id": "b"}], "upper_body", with_meta=True,
    )
    partial, partial_meta = regions.rank_by_region(
        [{"id": "a"}, {"id": "b", "height_fraction": 0.8}],
        "upper_body",
        with_meta=True,
    )

    assert [item["id"] for item in missing] == ["a", "b"]
    assert missing_meta["status"] == regions.UNCHECKABLE
    assert missing_meta["available_count"] == 0
    assert [item["id"] for item in partial] == ["b", "a"]
    assert partial_meta["status"] == regions.PARTIAL
    assert partial_meta["available_count"] == 1


@pytest.mark.parametrize("value", [True, "high", -0.1, 1.1, float("nan")])
def test_explicit_height_fraction_must_be_finite_and_normalized(value):
    with pytest.raises(ValueError):
        regions.rank_by_region(
            [{"id": "bad", "height_fraction": value}], "upper_body"
        )


# --------------------------------------------------------------------------
# _height_fraction:几何路径(s = (p·u−min)/(max−min),全边长)。
# --------------------------------------------------------------------------
def test_height_fraction_from_geometry_full_extent():
    # 竖直物体 z∈[0.72,0.88],候选点 z=0.80 → s=(0.80−0.72)/(0.88−0.72)=0.5
    c = {"xyz": [0.42, 0.11, 0.80],
         "extent": {"min": [0.40, 0.09, 0.72], "max": [0.44, 0.13, 0.88]}}
    assert regions._height_fraction(c) == pytest.approx(0.5)


def test_height_fraction_projects_aabb_along_mixed_sign_axis():
    diagonal = 2 ** -0.5
    candidate = {
        "xyz": [0.75, 0.25, 0.0],
        "axis_up": [diagonal, -diagonal, 0.0],
        "extent": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
    }

    assert regions._height_fraction(candidate) == pytest.approx(0.75)


def test_height_fraction_zero_axis_is_uncheckable():
    candidate = {
        "xyz": [0.5, 0.5, 0.5],
        "axis_up": [0.0, 0.0, 0.0],
        "extent": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
    }

    assert regions._height_fraction(candidate) is None


@pytest.mark.parametrize("field", ["axis_up", "xyz"])
def test_height_fraction_rejects_explicit_empty_geometry(field):
    candidate = {
        "xyz": [0.5, 0.5, 0.5],
        "axis_up": [0.0, 0.0, 1.0],
        "extent": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
    }
    candidate[field] = []

    with pytest.raises(ValueError):
        regions._height_fraction(candidate)


def test_height_fraction_rejects_inverted_extent():
    candidate = {
        "xyz": [0.5, 0.5, 0.8],
        "axis_up": [0.0, 0.0, 1.0],
        "extent": {"min": [0.0, 0.0, 1.0], "max": [1.0, 1.0, 0.0]},
    }

    with pytest.raises(ValueError, match="min values"):
        regions._height_fraction(candidate)


def test_height_fraction_prefers_explicit_field():
    c = {"height_fraction": 0.33, "xyz": [0, 0, 0.80],
         "extent": {"min": [0, 0, 0.72], "max": [0, 0, 0.88]}}
    assert regions._height_fraction(c) == 0.33


def test_height_fraction_none_when_ungeometrable():
    assert regions._height_fraction({"id": "x"}) is None


def test_candidate_bundle_nested_geometry_remains_rankable():
    candidates = [
        CandidateBundle(
            candidate_id=name,
            observation_id="obs-1",
            hole_values={},
            features={
                "xyz": [0.0, 0.0, height],
                "extent": {
                    "min": [0.0, 0.0, 0.0],
                    "max": [0.0, 0.0, 1.0],
                },
            },
            evidence_refs=(f"candidates/{name}.json",),
        )
        for name, height in (("low", 0.2), ("high", 0.8))
    ]

    result = deterministic_select(candidates, region="upper_body")

    assert [item.candidate_id for item in result.ranked] == ["high", "low"]
    assert result.region_meta["status"] == "ranked"


# --------------------------------------------------------------------------
# cone 角度偏好:approach 方向与目标倾角的误差越小越优。
# --------------------------------------------------------------------------
def test_cone_tilt_known_and_unknown():
    assert set(regions._CONE_TILT_DEG) == set(vocab.APPROACH_CONES)
    with pytest.raises(ValueError):
        regions.cone_angle_deg([0, 0, -1], "nonexistent_cone")


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


def test_rank_by_cone_side_is_azimuth_symmetric_and_prefers_horizontal():
    cands = [{"id": "west", "approach_dir": [-1, 0, 0]},
             {"id": "down", "approach_dir": [0, 0, -1]},
             {"id": "east", "approach_dir": [1, 0, 0]}]
    ranked = regions.rank_by_cone(cands, "side")
    assert [item["id"] for item in ranked] == ["west", "east", "down"]
    assert regions.cone_angle_deg([-1, 0, 0], "side") == pytest.approx(0.0)
    assert regions.cone_angle_deg([1, 0, 0], "side") == pytest.approx(0.0)


def test_rank_by_cone_oblique_is_azimuth_symmetric():
    cands = [{"id": "down", "approach_dir": [0, 0, -1]},
             {"id": "oblique_east", "approach_dir": [1, 0, -1]},
             {"id": "oblique_west", "approach_dir": [-1, 0, -1]}]
    ranked = regions.rank_by_cone(cands, "oblique")
    assert [item["id"] for item in ranked] == [
        "oblique_east", "oblique_west", "down",
    ]
    assert regions.cone_angle_deg([1, 0, -1], "oblique") == pytest.approx(0.0)
    assert regions.cone_angle_deg([-1, 0, -1], "oblique") == pytest.approx(0.0)


def test_rank_by_cone_no_elimination():
    cands = [{"id": "a", "approach_dir": [0, 0, -1]},
             {"id": "b", "approach_dir": [1, 0, 0]},
             {"id": "c"}]                              # 无方向 → 沉末尾但不丢
    ranked = regions.rank_by_cone(cands, "top_down")
    assert len(ranked) == 3 and ranked[-1]["id"] == "c"


def test_cone_meta_distinguishes_missing_and_partial_features():
    _, missing_meta = regions.rank_by_cone(
        [{"id": "a"}, {"id": "b"}], "top_down", with_meta=True,
    )
    _, partial_meta = regions.rank_by_cone(
        [{"id": "a"}, {"id": "b", "approach_dir": [0, 0, -1]}],
        "top_down",
        with_meta=True,
    )

    assert missing_meta["status"] == regions.UNCHECKABLE
    assert partial_meta["status"] == regions.PARTIAL
    assert partial_meta["available_count"] == 1


@pytest.mark.parametrize("value", ["down", [0, 0], [0, 0, 0], [0, 0, float("inf")]])
def test_candidate_approach_direction_must_be_finite_nonzero_vector(value):
    with pytest.raises(ValueError):
        regions.rank_by_cone(
            [{"id": "bad", "approach_dir": value}], "top_down"
        )


def test_gravity_tilt_ranking_is_frame_independent_and_strict():
    candidates = [
        {"id": "side", "approach_tilt_deg": 90.0},
        {"id": "down", "approach_tilt_deg": 0.0},
        {"id": "missing"},
    ]

    ranked, meta = regions.rank_by_gravity_tilt(
        candidates, "top_down", with_meta=True
    )

    assert [item["id"] for item in ranked] == ["down", "side", "missing"]
    assert meta["status"] == regions.PARTIAL
    with pytest.raises(ValueError, match=r"\[0, 180\]"):
        regions.rank_by_gravity_tilt(
            [{"id": "bad", "approach_tilt_deg": 181.0}], "top_down"
        )


def test_planning_selector_rejects_frame_less_approach_direction():
    candidate = CandidateBundle(
        candidate_id="bad-direction",
        observation_id="obs-1",
        hole_values={},
        features={"approach_dir": [0.0, 0.0, -1.0]},
        evidence_refs=("candidates/bad-direction.json",),
    )

    with pytest.raises(ValueError, match="frame-less approach_dir"):
        deterministic_select((candidate,), cone="top_down")


# --------------------------------------------------------------------------
# 护栏:regions.py 源码不得包含任务名或物体名。
# --------------------------------------------------------------------------
def test_no_task_or_object_names_in_source():
    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "demo_graph_lab"
        / "selection"
        / "regions.py"
    ).read_text("utf-8").lower()
    for bad in ("insert_tube", "stack_bowl", "deposit", "push_t",
                "tube", "bowl", "coin", "rack", "slot"):
        assert bad not in src, f"regions.py 出现禁用词 {bad!r}(任务名/物体名硬失败)"
