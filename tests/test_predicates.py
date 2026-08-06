"""三值谓词的纯逻辑测试。

每个可检验谓词至少 PASS/FAIL 各一例 + margin 符号检查;覆盖 UNKNOWN 路径
(缺参照 / 缺输入 / uncheckable_in_runtime / 词表外 / rim-handle / 谓词内部异常)。
轴类谓词另外钉住长轴语义:解 ``|R|·e = S`` 从世界 AABB 反求**局部**边长,取局部最长边
所在的轴(8/6 ep2:横躺管子的局部 +z 仍近竖直,靠局部 +z 判 axis_vertical 会给出假
PASS;8/6 ep3:拿世界边序号当局部轴序号,三根同姿态的管子会判出三种答案)。轴类实体
一律用 ``axis_ent_from_local`` 构造,并断言**已知答案**——不再靠两侧 parity 互证。
纯逻辑,无 cv2/网络/LLM。
"""

import math

import pytest

from demo_graph_lab.evaluation import predicates as P
from demo_graph_lab.selection import binding


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
# 绕 x 轴转 4.2°:局部 +z 只偏离竖直 4.2°——ep2 实测那根**横躺**管子的姿态。
_HALF = math.radians(4.2) / 2.0
NEAR_UPRIGHT_Q = [math.cos(_HALF), math.sin(_HALF), 0.0, 0.0]

# ep2 实测:那根没被碰过的管子,世界 AABB 111×85×37 mm(最长边在 x,是横躺的)。
EP2_LYING_EXTENTS = (0.111, 0.085, 0.037)
UPRIGHT_EXTENTS = (0.037, 0.037, 0.111)         # 同一根管子立起来
CUBOID_EXTENTS = (0.100, 0.090, 0.090)          # 近立方:次/最长 = 0.9 > 0.8


def axis_ent(extents, quat=IDENT_Q, center=(0.4, 0.1, 0.8)):
    """按世界 AABB 边长造实体(pos 取 AABB 中心,与仿真资产一致)。"""
    half = [item / 2.0 for item in extents]
    return {"pos": list(center), "quat": list(quat),
            "aabb": {"min": [center[i] - half[i] for i in range(3)],
                     "max": [center[i] + half[i] for i in range(3)]}}


def axis_ent_from_local(local_extents, quat=IDENT_Q, center=(0.4, 0.1, 0.8)):
    """按**局部**三边长 + 姿态造自洽实体:世界跨度用 S_j = Σ_k |R[j][k]|·e_k 正向算。

    姿态不轴对齐时世界 AABB 跨度 ≠ 局部边长,直接把局部边长当世界 AABB 写进快照
    造出的是物理上不可能的实体。轴类测试必须用这个构造,否则测的是不存在的物体。
    """
    cols = [binding._local_axis_in_world(list(quat), k) for k in range(3)]
    spans = [sum(abs(cols[k][j]) * local_extents[k] for k in range(3)) for j in range(3)]
    return axis_ent(spans, quat, center)


def C(name, **args):
    return {"name": name, "args": args}


# ==========================================================================
# axis_vertical(判据是**真实长轴**,不是物体局部 +z)
# ==========================================================================
def test_axis_vertical_pass():
    ents = {"tube": axis_ent(UPRIGHT_EXTENTS, quat=IDENT_Q)}
    p = P.check(C("axis_vertical", axis="tube.long_axis"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_axis_vertical_fail():
    # 长轴(局部 x)水平 → 与竖直夹角 90°
    ents = {"tube": axis_ent_from_local(EP2_LYING_EXTENTS, quat=TILT_Q)}
    p = P.check(C("axis_vertical", axis="tube.long_axis"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_axis_vertical_lying_tube_is_not_a_false_pass():
    """ep2 靶子:管子横躺(长轴在 x),但资产局部 +z 只偏离竖直 4.2°。

    旧实现读局部 +z → angle≈4.2° → 对一根根本没被碰过的管子报 PASS。
    换真实长轴后必须判 FAIL。
    """
    ents = {"tube": axis_ent_from_local(EP2_LYING_EXTENTS, quat=NEAR_UPRIGHT_Q)}
    p = P.check(C("axis_vertical", axis="tube.long_axis"), ents)
    assert p.status == P.FAIL, "横躺管子必须判 FAIL,不能因为局部 +z 近竖直而假 PASS"
    assert float(p.detail.split("=")[1]) == pytest.approx(90.0, abs=0.5)


def test_axis_vertical_unknown_when_extents_are_ambiguous():
    """近立方:主方向不可辨 → UNKNOWN 带 reason,不猜一个轴。"""
    ents = {"box": axis_ent(CUBOID_EXTENTS, quat=IDENT_Q)}
    p = P.check(C("axis_vertical", axis="box.long_axis"), ents)
    assert p.status == P.UNKNOWN and p.reason == "axis_ambiguous_extents"
    assert p.margin is None


def test_axis_vertical_unknown_without_aabb():
    """只有 quat 没有 AABB → 长轴读不出来 → UNKNOWN(不退回局部 +z)。"""
    ents = {"tube": ent([0, 0, 1], quat=IDENT_Q)}
    p = P.check(C("axis_vertical", axis="tube.long_axis"), ents)
    assert p.status == P.UNKNOWN and p.reason == "no_aabb"


def test_axis_vertical_unknown_missing_ref():
    p = P.check(C("axis_vertical", axis="ghost.long_axis"), {})
    assert p.status == P.UNKNOWN and p.reason == "ref_unresolved" and p.margin is None


# ==========================================================================
# axis_parallel(同样消费真实长轴)
# ==========================================================================
def test_axis_parallel_pass():
    ents = {"a": axis_ent(UPRIGHT_EXTENTS, quat=IDENT_Q),
            "b": axis_ent(UPRIGHT_EXTENTS, quat=IDENT_Q)}
    p = P.check(C("axis_parallel", axis_a="a.z", axis_b="b.z"), ents)
    assert p.status == P.PASS and p.margin > 0


def test_axis_parallel_fail():
    # a 立着(长轴局部 z),b 横躺(长轴局部 x)→ 夹角 90°
    ents = {"a": axis_ent(UPRIGHT_EXTENTS, quat=IDENT_Q),
            "b": axis_ent(EP2_LYING_EXTENTS, quat=IDENT_Q)}
    p = P.check(C("axis_parallel", axis_a="a.z", axis_b="b.z"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_axis_parallel_lying_pair_is_not_a_false_pass():
    """两根都横躺、但局部 +z 都近竖直:旧实现判「平行」,长轴口径下 a 立 b 躺 → FAIL。"""
    ents = {"a": axis_ent_from_local(UPRIGHT_EXTENTS, quat=NEAR_UPRIGHT_Q),
            "b": axis_ent_from_local(EP2_LYING_EXTENTS, quat=NEAR_UPRIGHT_Q)}
    p = P.check(C("axis_parallel", axis_a="a.z", axis_b="b.z"), ents)
    assert p.status == P.FAIL and p.margin < 0


def test_axis_parallel_unknown():
    p = P.check(C("axis_parallel", axis_a="ghost.z", axis_b="b.z"), {})
    assert p.status == P.UNKNOWN

    only_a = {"a": axis_ent(UPRIGHT_EXTENTS, quat=IDENT_Q)}
    p = P.check(C("axis_parallel", axis_a="a.z", axis_b="ghost.z"), only_a)
    assert p.status == P.UNKNOWN and p.reason == "ref_unresolved"


def test_axis_parallel_unknown_names_the_ambiguous_side():
    """哪一侧分不出主方向就报哪一侧,reason 不被另一侧掩盖。"""
    ents = {"a": axis_ent(UPRIGHT_EXTENTS, quat=IDENT_Q),
            "b": axis_ent(CUBOID_EXTENTS, quat=IDENT_Q)}
    p = P.check(C("axis_parallel", axis_a="a.z", axis_b="b.z"), ents)
    assert p.status == P.UNKNOWN and p.reason == "axis_ambiguous_extents"
    assert "axis_b=b" in p.detail


# ==========================================================================
# 长轴推断与 selection.binding 的对接
# ==========================================================================
# 【8/6 ep3 的教训,别删这段】上一版这里是两份同构实现之间的「逐值 parity」测试。
# 它全绿,而两侧同时是错的:都把**世界** AABB 的边序号当成**局部**轴序号。parity
# 只能保证「两侧一致」,永远保证不了「两侧正确」——用它当唯一护栏,等于把同一个
# bug 钉死在两边。现在 `P._long_axis_world` 已经薄封装 `binding.long_axis_world`
# (只有一份实现,一致性是结构性的),这里改成**已知答案**:先定死局部边长与姿态,
# 用 S = |R|·e 正向造世界 AABB,再要求实现把 e 解回来。判别性用例在
# tests/test_long_axis_band.py(三管同答案 + 反向验证)。
_KNOWN_AXIS_CASES = [
    (EP2_LYING_EXTENTS, IDENT_Q, 0),
    (EP2_LYING_EXTENTS, NEAR_UPRIGHT_Q, 0),
    (EP2_LYING_EXTENTS, TILT_Q, 0),
    (UPRIGHT_EXTENTS, IDENT_Q, 2),
    (UPRIGHT_EXTENTS, NEAR_UPRIGHT_Q, 2),
    ((0.037, 0.111, 0.037), TILT_Q, 1),         # 局部最长边在 y
]


@pytest.mark.parametrize("local_extents,quat,long_index", _KNOWN_AXIS_CASES)
def test_long_axis_recovers_the_known_local_axis(local_extents, quat, long_index):
    entity = axis_ent_from_local(local_extents, quat=quat)

    mine, reason = P._long_axis_world(entity)
    theirs, length = binding._long_axis(entity, {"name": "long_axis"})

    assert reason is None
    assert mine == pytest.approx(theirs, abs=1e-12), "两侧同源,数值必须逐位相同"
    assert length == pytest.approx(max(local_extents), abs=1e-9)
    truth = binding._local_axis_in_world(quat, long_index)
    norm = math.sqrt(sum(v * v for v in truth))
    assert abs(sum(mine[i] * truth[i] / norm for i in range(3))) == pytest.approx(
        1.0, abs=1e-9), "世界向量必须与已知的局部长轴同线"


def test_long_axis_parity_on_rejection():
    """拒绝面也要 parity:binding 抛 UnsolvedHole 的情形,谓词侧给同名 reason。"""
    entity = axis_ent(CUBOID_EXTENTS, quat=IDENT_Q)
    mine, reason = P._long_axis_world(entity)
    assert mine is None and reason == "axis_ambiguous_extents"
    with pytest.raises(binding.UnsolvedHole) as error:
        binding._long_axis(entity, {"name": "long_axis"})
    assert error.value.reason == reason

    # 非单位四元数,第 2 列退化成零向量:姿态读不出来,长轴方向无从谈起。
    degenerate = axis_ent(UPRIGHT_EXTENTS, quat=[0.0, 0.5, 0.5, 0.0])
    mine, reason = P._long_axis_world(degenerate)
    assert mine is None and reason == "axis_unobserved"
    with pytest.raises(binding.UnsolvedHole) as error:
        binding._long_axis(degenerate, {"name": "long_axis"})
    assert error.value.reason == reason


def test_long_axis_dominance_ratio_matches_binding():
    """判据常量本身也对齐:阈值单边漂移会让两侧对同一物体给不同结论。"""
    assert P._AXIS_DOMINANCE_MAX_RATIO == binding._AXIS_DOMINANCE_MAX_RATIO


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
