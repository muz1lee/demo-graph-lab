"""三值谓词的纯逻辑测试。

每个可检验谓词至少 PASS/FAIL 各一例 + margin 符号检查;覆盖 UNKNOWN 路径
(缺参照 / 缺输入 / uncheckable_in_runtime / 词表外 / rim-handle / 谓词内部异常)。
纯逻辑,无 cv2/网络/LLM。
"""

from demo_graph_lab.evaluation import predicates as P


# ---- 实体快照工具:pos + 可选 quat(wxyz) + 可选 aabb ----
def ent(pos, quat=None, aabb=None):
    e = {"pos": list(pos)}
    if quat is not None:
        e["quat"] = list(quat)
    if aabb is not None:
        e["aabb"] = aabb
    return e


IDENT_Q = [1.0, 0.0, 0.0, 0.0]                 # 单位四元数:局部 +z = 世界 +z
# 绕 x 轴转 90°(wxyz):局部 +z 转到世界 -y(轴变水平)
TILT_Q = [0.70710678, 0.70710678, 0.0, 0.0]


def C(name, **args):
    return {"name": name, "args": args}


# ==========================================================================
# axis_vertical
# ==========================================================================
def test_axis_vertical_pass():
    ents = {"tube": ent([0, 0, 1], quat=IDENT_Q)}
    p = P.check(C("axis_vertical", axis="tube.long_axis"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_axis_vertical_fail():
    ents = {"tube": ent([0, 0, 1], quat=TILT_Q)}     # 轴水平 → 与竖直夹角 90°
    p = P.check(C("axis_vertical", axis="tube.long_axis"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_axis_vertical_unknown_missing_ref():
    p = P.check(C("axis_vertical", axis="ghost.long_axis"), {})
    assert p.status == P.UNKNOWN and p.reason == "ref_unresolved" and p.margin is None


# ==========================================================================
# axis_parallel
# ==========================================================================
def test_axis_parallel_pass():
    ents = {"a": ent([0, 0, 0], quat=IDENT_Q), "b": ent([1, 0, 0], quat=IDENT_Q)}
    p = P.check(C("axis_parallel", axis_a="a.z", axis_b="b.z"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_axis_parallel_fail():
    ents = {"a": ent([0, 0, 0], quat=IDENT_Q), "b": ent([1, 0, 0], quat=TILT_Q)}
    p = P.check(C("axis_parallel", axis_a="a.z", axis_b="b.z"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_axis_parallel_unknown():
    p = P.check(C("axis_parallel", axis_a="ghost.z", axis_b="b.z"), {})
    assert p.status == P.UNKNOWN


# ==========================================================================
# center_align
# ==========================================================================
def test_center_align_pass():
    ents = {"a": ent([0.50, 0.00, 0.8]), "b": ent([0.51, 0.01, 0.2])}
    p = P.check(C("center_align", obj_a="a", obj_b="b"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_center_align_fail():
    ents = {"a": ent([0.50, 0.00, 0.8]), "b": ent([0.70, 0.20, 0.2])}
    p = P.check(C("center_align", obj_a="a", obj_b="b"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_center_align_unknown():
    ents = {"a": ent([0.5, 0.0, 0.8])}               # 缺 b
    p = P.check(C("center_align", obj_a="a", obj_b="b"), ents)
    assert p.status == P.UNKNOWN and p.reason == "ref_unresolved"


# ==========================================================================
# above
# ==========================================================================
def test_above_pass():
    ents = {"a": ent([0, 0, 1.0]), "b": ent([0, 0, 0.3])}
    p = P.check(C("above", obj_a="a", obj_b="b"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_above_fail():
    ents = {"a": ent([0, 0, 0.2]), "b": ent([0, 0, 0.8])}
    p = P.check(C("above", obj_a="a", obj_b="b"), ents)
    assert p.status == P.FAIL and p.margin < 0


# ==========================================================================
# inside
# ==========================================================================
def test_inside_pass():
    ents = {"a": ent([0.5, 0.5, 0.5]),
            "box": ent([0.5, 0.5, 0.0], aabb={"min": [0, 0, 0], "max": [1, 1, 1]})}
    p = P.check(C("inside", obj_a="a", obj_b="box"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_inside_fail():
    ents = {"a": ent([2.0, 0.5, 0.5]),               # x 远出框
            "box": ent([0.5, 0.5, 0.0], aabb={"min": [0, 0, 0], "max": [1, 1, 1]})}
    p = P.check(C("inside", obj_a="a", obj_b="box"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_inside_unknown_no_aabb():
    ents = {"a": ent([0.5, 0.5, 0.5]), "box": ent([0.5, 0.5, 0.0])}   # box 无 aabb
    p = P.check(C("inside", obj_a="a", obj_b="box"), ents)
    assert p.status == P.UNKNOWN and p.reason == "no_aabb"


# ==========================================================================
# clearance(AABB 间隙近似)
# ==========================================================================
def test_clearance_pass():
    ents = {"a": ent([0, 0, 0], aabb={"min": [0, 0, 0], "max": [1, 1, 1]}),
            "b": ent([3, 0, 0], aabb={"min": [3, 0, 0], "max": [4, 1, 1]})}   # x 间隙 2
    p = P.check(C("clearance", obj_a="a", obj_b="b"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_clearance_fail_overlap():
    ents = {"a": ent([0, 0, 0], aabb={"min": [0, 0, 0], "max": [1, 1, 1]}),
            "b": ent([0.5, 0, 0], aabb={"min": [0.5, 0, 0], "max": [1.5, 1, 1]})}  # 重叠
    p = P.check(C("clearance", obj_a="a", obj_b="b"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_clearance_unknown():
    p = P.check(C("clearance", obj_a="a", obj_b="b"), {})
    assert p.status == P.UNKNOWN


# ==========================================================================
# region_grasp(必须可检查:复用 binding/regions 归一化竖直带)
# ==========================================================================
_BOX = {"min": [0, 0, 0.0], "max": [0.1, 0.1, 1.0]}   # 竖直跨度 1.0,归一化直接 = z


def test_region_grasp_upper_body_pass():
    ents = {"obj": ent([0, 0, 0.0], aabb=_BOX)}
    # 抓取点 z=0.9 → s=0.9;upper_body 偏好 f(s)=s=0.9 > 0.5 → PASS
    p = P.check(C("region_grasp", obj="obj", region="upper_body"), ents,
                grasp_point=[0, 0, 0.9])
    assert p.status == P.PASS and p.margin > 0


def test_region_grasp_upper_body_fail():
    ents = {"obj": ent([0, 0, 0.0], aabb=_BOX)}
    # 抓取点 z=0.1 → s=0.1;upper_body f(s)=0.1 < 0.5 → FAIL
    p = P.check(C("region_grasp", obj="obj", region="upper_body"), ents,
                grasp_point=[0, 0, 0.1])
    assert p.status == P.FAIL and p.margin < 0


def test_region_grasp_bottom_pass():
    ents = {"obj": ent([0, 0, 0.0], aabb=_BOX)}
    # bottom f(s)=1-s;s=0.1 → 0.9 > 0.5 → PASS
    p = P.check(C("region_grasp", obj="obj", region="bottom"), ents,
                grasp_point=[0, 0, 0.1])
    assert p.status == P.PASS and p.margin > 0


def test_region_grasp_unknown_no_grasp_point():
    ents = {"obj": ent([0, 0, 0.0], aabb=_BOX)}
    p = P.check(C("region_grasp", obj="obj", region="upper_body"), ents)
    assert p.status == P.UNKNOWN and p.reason == "no_grasp_point"


def test_region_grasp_rim_uncheckable():
    ents = {"obj": ent([0, 0, 0.0], aabb=_BOX)}
    p = P.check(C("region_grasp", obj="obj", region="rim"), ents,
                grasp_point=[0, 0, 0.9])
    assert p.status == P.UNKNOWN and p.reason == "region_uncheckable"


# ==========================================================================
# approach_direction(复用 regions.cone_angle_deg)
# ==========================================================================
def test_approach_direction_top_down_pass():
    # top_down 参照轴 = -z;approach 方向也向下 → 夹角 0 → PASS
    p = P.check(C("approach_direction", cone="top_down", target="obj"), {},
                approach_dir=[0, 0, -1])
    assert p.status == P.PASS and p.margin > 0


def test_approach_direction_top_down_fail():
    # approach 方向水平 → 与 -z 夹角 90° > 25° → FAIL
    p = P.check(C("approach_direction", cone="top_down", target="obj"), {},
                approach_dir=[1, 0, 0])
    assert p.status == P.FAIL and p.margin < 0


def test_approach_direction_unknown_no_dir():
    p = P.check(C("approach_direction", cone="top_down", target="obj"), {})
    assert p.status == P.UNKNOWN and p.reason == "no_approach_dir"


# ==========================================================================
# UNCHECKABLE_IN_RUNTIME(carry / order)
# ==========================================================================
def test_carry_uncheckable_in_runtime():
    p = P.check(C("carry", relation="held"), {})
    assert p.status == P.UNKNOWN and p.reason == "uncheckable_in_runtime"


def test_order_uncheckable_in_runtime():
    p = P.check(C("order", stage_sequence="0<1<2"), {})
    assert p.status == P.UNKNOWN and p.reason == "uncheckable_in_runtime"


# ==========================================================================
# 词表外 / 谓词内部异常 → UNKNOWN(绝不 fail-open 成 PASS)
# ==========================================================================
def test_not_in_vocab_unknown():
    p = P.check(C("frobnicate", x="y"), {})
    assert p.status == P.UNKNOWN and p.reason == "not_in_vocab"


def test_predicate_internal_error_is_unknown_not_pass():
    # above 谓词读 pos[2],给 a 一个没有 pos 的畸形实体 → 内部异常 → UNKNOWN(不 fail-open)
    ents = {"a": {"quat": IDENT_Q}, "b": ent([0, 0, 0])}
    p = P.check(C("above", obj_a="a", obj_b="b"), ents)
    assert p.status == P.UNKNOWN and p.reason == "predicate_error"


# ==========================================================================
# 覆盖表:≥8/10 checkable,region_grasp 在其中,carry/order 是被允许的不可检查项
# ==========================================================================
def test_coverage_meets_cc3():
    cov = P.coverage()
    assert len(cov) == 10
    checkable = [k for k, v in cov.items() if v == "checkable"]
    assert len(checkable) >= 8
    assert "region_grasp" in checkable
    assert cov["carry"] == "uncheckable_in_runtime"
    assert cov["order"] == "uncheckable_in_runtime"


# ==========================================================================
# ok 属性三值语义:PASS→True / FAIL→False / UNKNOWN→None(调用方须区分)
# ==========================================================================
def test_ok_property_three_valued():
    assert P.Predicate("x", P.PASS, 0.1).ok is True
    assert P.Predicate("x", P.FAIL, -0.1).ok is False
    assert P.Predicate("x", P.UNKNOWN).ok is None
