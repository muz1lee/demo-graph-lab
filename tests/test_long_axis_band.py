"""长轴推断与 region 区带必须跟着物体的真实长轴走。

8/6 ep1 两次复现的实测:管子是**横躺**的(AABB 的 z 跨度就是直径 33.6 mm),但
``solve_axis_3d`` 拿物体**局部 +z** 当长轴——横躺资产的局部 +z 仍近竖直(偏离
4.3°),于是

  - ``_grasp_quat`` 的 yaw 跟着那 4.3° 倾斜的方位角抖,两次 attempt 的抓取四元数
    能差几十度;
  - ``upper_body``(s=0.80)沿 AABB 的**世界 z** 取点,抓取点高出赤道约 1 cm,
    而半径才 16.8 mm——光滑圆柱在赤道以上夹必滑出。

8/6 ep3 又推翻了 ep1 那版修复本身:它把**世界** AABB 的边序号直接当成**局部**轴序号,
只有姿态轴对齐时两者才碰巧相等。正解是解 ``|R|·e = S`` 从世界 AABB 跨度反求局部三边长
(``binding._local_extents``),长轴 = 局部最长边所在的轴。

本文件钉死修复后的语义:局部边长可反求(已知答案逐分量对);三根同资产同姿态、只差 yaw
的平躺管子必须给同一个答案(判别性用例 + 反向验证对照组);区带沿真实长轴参数化;横躺
时哪一端算 upper 没有可靠信号,取段中点并记 ``end_ambiguous``;物体立着时行为与旧实现
一致;近立方 / |R| 奇异 / 解出负边长一律拒绝,不猜。

纯逻辑、离线、不触 sim/网络/LLM。
"""

import math

import pytest

from demo_graph_lab.evaluation import predicates
from demo_graph_lab.execution import oracle_runtime
from demo_graph_lab.execution.oracle_runtime import OracleRuntime
from demo_graph_lab.selection import binding


# ep1 实测尺寸:管长 110 mm、直径 33.6 mm。
_TUBE_LEN = 0.110
_TUBE_DIA = 0.0336
_CENTER = (0.4, 0.1, 0.8)


def _entity(extents, quat=(1.0, 0.0, 0.0, 0.0), center=_CENTER):
    """按世界系 AABB 边长构造实体(pos 取 AABB 中心,与仿真资产一致)。

    只在 ``|R|`` 为置换矩阵(轴对齐姿态)时才和 ``_entity_from_local`` 等价;姿态一斜
    就必须用 ``_entity_from_local``,否则造出的是物理上不可能的 (AABB, quat) 组合。
    """
    half = [item / 2.0 for item in extents]
    return {"pos": list(center), "quat": list(quat),
            "aabb": {"min": [center[i] - half[i] for i in range(3)],
                     "max": [center[i] + half[i] for i in range(3)]}}


def _world_spans(local_extents, quat):
    """已知局部三边长与姿态,**正向**算世界 AABB 跨度:S_j = Σ_k |R[j][k]|·e_k。

    这是长轴反求的真值来源。测试只用这条正向公式造数据,再要求实现把 ``e`` 解回来
    ——「已知答案」测试,和被测实现没有共享任何代码路径。
    """
    cols = [binding._local_axis_in_world(list(quat), k) for k in range(3)]
    return [sum(abs(cols[k][j]) * local_extents[k] for k in range(3)) for j in range(3)]


def _entity_from_local(local_extents, quat=(1.0, 0.0, 0.0, 0.0), center=_CENTER):
    """由「局部边长 + 姿态」造出自洽实体(世界 AABB 用 ``_world_spans`` 正向算)。"""
    return _entity(_world_spans(local_extents, quat), quat, center)


class _Stub:
    """binding 的求解器只经 ``rt._ent(name)`` 取实体。"""

    def __init__(self, ent):
        self.ent = ent

    def _ent(self, name):
        return self.ent


def _axis_stage():
    return {"index": 0, "name": "align",
            "stage_objects": {"manipulated": "tube", "target": "rack"},
            "constraints": [{"name": "axis_vertical",
                             "args": {"axis": "tube.long_axis"}}]}


def _grasp_stage(region="upper_body"):
    return {"index": 0, "name": "grasp",
            "stage_objects": {"manipulated": "tube", "target": "rack"},
            "constraints": [{"name": "region_grasp",
                             "args": {"obj": "tube", "region": region}}]}


def _solve_axis(ent):
    stage = _axis_stage()
    return binding.solve_hole({"name": "tube_axis", "type": "axis_3d"},
                              stage, stage["constraints"], _Stub(ent))


def _solve_pose(ent, region="upper_body"):
    stage = _grasp_stage(region)
    return binding.solve_hole({"name": "grasp_pose", "type": "pose_se3"},
                              stage, stage["constraints"], _Stub(ent))


def _quat_about_x(deg):
    """绕世界 x 转 deg 的物体四元数(wxyz)。长轴沿 x 时这就是绕管轴自转。"""
    half = math.radians(deg) / 2.0
    return [math.cos(half), math.sin(half), 0.0, 0.0]


def _quat_about_y(deg):
    half = math.radians(deg) / 2.0
    return [math.cos(half), 0.0, math.sin(half), 0.0]


def _quat_about_z(deg):
    half = math.radians(deg) / 2.0
    return [math.cos(half), 0.0, 0.0, math.sin(half)]


def _qmul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return [w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2]


def _angle_from_vertical(vec):
    """轴无向:与世界 +z 的最小夹角(度)。"""
    return math.degrees(math.acos(min(1.0, abs(vec[2]))))


def _legacy_local_z(quat):
    """旧实现:无条件取物体局部 +z 作长轴。"""
    w, x, y, z = quat
    return [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]


# ==========================================================================
# 已知答案测试:局部边长必须能从世界 AABB 反求出来。
#
# 8/6 ep3 的教训写在这里:上一轮的"真长轴"修复把**世界** AABB 的边序号直接当成
# **局部**轴序号喂给 `_local_axis_in_world`,两个索引空间根本不是一回事;而
# tests/test_predicates.py 的 parity 测试只对照两侧数值是否相同,把同一个错误钉在了
# 两边。parity 保证「一致」,保证不了「正确」——所以本节全部是**已知答案**测试:
# 先定死局部边长与姿态,用 S = |R|·e 正向算出世界 AABB 造实体,再要求实现把 e 解回来。
# ==========================================================================
_LOCAL_TUBE = [_TUBE_LEN, _TUBE_DIA, _TUBE_DIA]        # 局部轴 0 是长轴

_KNOWN_POSES = {
    "identity": [1.0, 0.0, 0.0, 0.0],
    "roll_+4.3": _quat_about_x(4.3),                    # 绕长轴自转,长轴方向不变
    "roll_-4.3": _quat_about_x(-4.3),
    "stand_up_90": _quat_about_y(90.0),                 # 长轴立起来
    "yaw_25": _quat_about_z(25.0),
    "compound": _qmul(_qmul(_quat_about_z(31.0), _quat_about_x(12.0)),
                      _quat_about_y(7.0)),
}


@pytest.mark.parametrize("name", sorted(_KNOWN_POSES))
def test_local_extents_are_recovered_from_the_world_aabb(name):
    quat = _KNOWN_POSES[name]
    ent = _entity_from_local(_LOCAL_TUBE, quat)

    extents, columns, reason = binding._local_extents(ent)

    assert reason is None
    assert extents == pytest.approx(_LOCAL_TUBE, abs=1e-9), \
        "局部边长必须逐分量解回真值,不是世界跨度"


@pytest.mark.parametrize("name", sorted(_KNOWN_POSES))
def test_long_axis_is_the_local_longest_edge_axis_in_world(name):
    quat = _KNOWN_POSES[name]
    ent = _entity_from_local(_LOCAL_TUBE, quat)

    vec, length, reason = binding.long_axis_world(ent)

    assert reason is None
    assert length == pytest.approx(_TUBE_LEN, abs=1e-9)
    # 真值:局部轴 0 在世界系的方向(该构造下局部长轴恒为 0 号)。
    truth = binding._local_axis_in_world(quat, 0)
    assert abs(sum(vec[i] * truth[i] for i in range(3))) == pytest.approx(1.0, abs=1e-9), \
        "世界向量必须与真长轴同线"


def test_world_aabb_spans_are_not_the_local_edge_lengths():
    """判别力自证:斜姿态下世界跨度确实 ≠ 局部边长,否则上面几条测不出东西。"""
    spans = _world_spans(_LOCAL_TUBE, _KNOWN_POSES["compound"])
    assert max(abs(spans[i] - _LOCAL_TUBE[i]) for i in range(3)) > 0.005


# ==========================================================================
# 三管同答案:同资产、同姿态、只差 yaw —— 必须给同一个答案。
# ep3 实测里旧实现给出 FAIL / 假 PASS / UNKNOWN 三种答案。
# ==========================================================================
# 局部轴 1 是长轴;绕世界 x 转 2.2° 把它放到近水平(离竖直 87.8°),再绕局部 y 自转
# 90°(同一根管子原地绕自己的长轴滚一下,长轴方向丝毫不变),最后套一个世界 yaw。
_LOCAL_TUBE_AXIS1 = [_TUBE_DIA, _TUBE_LEN, _TUBE_DIA]
_LYING_YAWS = (0.0, 40.0, 70.0)


def _lying_tube(yaw):
    quat = _qmul(_qmul(_quat_about_z(yaw), _quat_about_x(2.2)), _quat_about_y(90.0))
    return _entity_from_local(_LOCAL_TUBE_AXIS1, quat)


def test_three_lying_tubes_give_one_and_the_same_answer():
    answers = [binding.long_axis_world(_lying_tube(yaw)) for yaw in _LYING_YAWS]

    assert [reason for _v, _l, reason in answers] == [None, None, None], \
        "同一根管子换个 yaw 不该有的能判、有的判不了"
    lengths = [length for _v, length, _r in answers]
    assert lengths == pytest.approx([_TUBE_LEN] * 3, abs=1e-9)
    angles = [_angle_from_vertical(vec) for vec, _l, _r in answers]
    assert max(angles) - min(angles) < 1.0, f"三根管子的离竖直角必须一致,实得 {angles}"
    assert angles[0] == pytest.approx(87.8, abs=0.1)


def test_three_lying_tubes_all_fail_axis_vertical():
    ents = {f"tube{i}": _lying_tube(yaw) for i, yaw in enumerate(_LYING_YAWS)}
    verdicts = [predicates.check(
        {"name": "axis_vertical", "args": {"axis": f"{name}.long_axis"}}, ents)
        for name in ents]

    assert [v.status for v in verdicts] == [predicates.FAIL] * 3
    assert [v.margin for v in verdicts] == pytest.approx([verdicts[0].margin] * 3, abs=0.1)


def test_the_world_edge_index_reading_would_disagree_across_yaw():
    """反向验证:换回"世界边序号当局部轴序号",这三根管子就散成三个答案。

    没有这条对照,上面两条测试可能只是"两个错误实现也能一致"。
    """
    def world_edge_index_reading(ent):
        lo, hi = binding._aabb_bounds(ent)
        spans = [hi[i] - lo[i] for i in range(3)]
        order = sorted(range(3), key=lambda i: spans[i], reverse=True)
        if spans[order[1]] / spans[order[0]] > binding._AXIS_DOMINANCE_MAX_RATIO:
            return None                                   # 次/主闸拦下 → UNKNOWN
        vec = binding._local_axis_in_world(ent["quat"], order[0])
        norm = math.sqrt(sum(v * v for v in vec))
        return _angle_from_vertical([v / norm for v in vec])

    legacy = [world_edge_index_reading(_lying_tube(yaw)) for yaw in _LYING_YAWS]
    assert len(set(legacy)) == 3, f"对照组必须散开,实得 {legacy}"
    assert legacy[0] == pytest.approx(87.8, abs=0.1)      # FAIL
    assert legacy[1] is None                              # UNKNOWN(次/主闸)
    assert legacy[2] == pytest.approx(2.2, abs=0.1)       # 假 PASS:近竖直


# ==========================================================================
# 拒绝面:歧义 / 数值退化,都不猜。
# ==========================================================================
def test_standing_tube_passes_axis_vertical():
    ent = _entity_from_local(_LOCAL_TUBE, _quat_about_y(90.0))   # 局部 x 轴指向世界 z
    p = predicates.check({"name": "axis_vertical", "args": {"axis": "t.long_axis"}},
                         {"t": ent})
    assert p.status == predicates.PASS and p.margin > 0


def test_near_cube_local_extents_are_ambiguous_not_guessed():
    ent = _entity_from_local([0.100, 0.095, 0.090], _KNOWN_POSES["compound"])
    _vec, _length, reason = binding.long_axis_world(ent)
    assert reason == "axis_ambiguous_extents"
    p = predicates.check({"name": "axis_vertical", "args": {"axis": "t.long_axis"}},
                         {"t": ent})
    assert p.status == predicates.UNKNOWN and p.reason == "axis_ambiguous_extents"


def test_exact_45_degree_yaw_is_singular_and_refused():
    """绕竖直轴恰 45°:|R| 的前两行相同,世界 AABB 对两条局部边长完全无信息 → 拒绝。"""
    ent = _entity_from_local(_LOCAL_TUBE, _quat_about_z(45.0))
    _vec, _length, reason = binding.long_axis_world(ent)
    assert reason == "axis_extents_unrecoverable"
    p = predicates.check({"name": "axis_vertical", "args": {"axis": "t.long_axis"}},
                         {"t": ent})
    assert p.status == predicates.UNKNOWN and p.reason == "axis_extents_unrecoverable"


def test_inconsistent_aabb_and_quat_solve_negative_and_are_refused():
    """AABB 与 quat 对不上(解出负边长)→ 这组观测自相矛盾,拒绝,不取绝对值硬凑。"""
    ent = _entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA), _quat_about_z(30.0))
    _vec, _length, reason = binding.long_axis_world(ent)
    assert reason == "axis_extents_unrecoverable"


def test_degenerate_quat_is_unobserved():
    ent = _entity_from_local(_LOCAL_TUBE, [1.0, 0.0, 0.0, 0.0])
    ent["quat"] = [0.0, 0.5, 0.5, 0.0]        # 非单位四元数,第 2 列退化成零向量
    _vec, _length, reason = binding.long_axis_world(ent)
    assert reason == "axis_unobserved"


# ==========================================================================
# 横躺圆柱:长轴 = 世界 x / y,不是局部 +z。
# ==========================================================================
def test_lying_cylinder_long_axis_is_world_x():
    out = _solve_axis(_entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA)))
    assert out["vec"] == pytest.approx([1.0, 0.0, 0.0], abs=1e-12)
    assert out["axis_source"] == "local_extents_from_aabb"


def test_lying_cylinder_long_axis_follows_the_longest_edge_not_the_key_order():
    """管子改躺在 y 上 → 长轴跟着换成世界 y(判据是边长,不是轴序号)。"""
    out = _solve_axis(_entity((_TUBE_DIA, _TUBE_LEN, _TUBE_DIA)))
    assert out["vec"] == pytest.approx([0.0, 1.0, 0.0], abs=1e-12)


def test_lying_cylinder_axis_is_stable_against_asset_roll():
    """绕管轴自转 ±4.3°(实测那个倾斜量)不改变长轴;旧实现的局部 +z 会跟着翻。"""
    a = _solve_axis(_entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA), _quat_about_x(+4.3)))
    b = _solve_axis(_entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA), _quat_about_x(-4.3)))
    assert a["vec"] == pytest.approx(b["vec"], abs=1e-12)
    legacy = [_legacy_local_z(_quat_about_x(sign * 4.3)) for sign in (+1, -1)]
    assert legacy[0][1] != pytest.approx(legacy[1][1], abs=1e-3), \
        "对照组:旧实现的局部 +z 在这两个姿态下确实不同(否则本用例没有区分力)"


# ==========================================================================
# 靶子 3:_grasp_quat 消费新长轴后指轴与管轴正交且稳定。
# ==========================================================================
def _grasp_quat(vec):
    return OracleRuntime({"stages": []})._grasp_quat({"kind": "axis", "vec": vec})


def test_grasp_quat_finger_axis_is_orthogonal_to_the_lying_tube_axis():
    axis = _solve_axis(_entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA)))["vec"]
    quat, why = _grasp_quat(axis)
    assert why == "yaw_orthogonal_to_axis"
    finger = oracle_runtime._tool_axes(quat)[oracle_runtime.FINGER_AXIS_IDX]
    assert sum(finger[i] * axis[i] for i in range(3)) == pytest.approx(0.0, abs=1e-9)


def test_grasp_quat_no_longer_follows_the_4_3_degree_tilt_noise():
    """同一根横躺管、只差 ±4.3° 自转:新长轴给同一个抓取腕姿,旧长轴差 180°。"""
    new = [_grasp_quat(_solve_axis(
        _entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA), _quat_about_x(sign * 4.3)))["vec"])[0]
        for sign in (+1, -1)]
    assert oracle_runtime._qang(new[0], new[1]) < 1e-6

    legacy = [_grasp_quat(_legacy_local_z(_quat_about_x(sign * 4.3)))[0]
              for sign in (+1, -1)]
    assert oracle_runtime._qang(legacy[0], legacy[1]) > 30.0, \
        "对照组:旧长轴下这两个姿态的抓取四元数确实差几十度"


# ==========================================================================
# 靶子 2:区带沿真实长轴,横躺时取段中点(= 赤道高度)。
# ==========================================================================
def test_lying_cylinder_band_is_taken_at_the_equator_not_above_it():
    ent = _entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA))
    out = _solve_pose(ent, "upper_body")

    assert out["xyz"][2] == pytest.approx(_CENTER[2], abs=1e-12), \
        "横躺圆柱必须在赤道(质心高度)夹"
    # 旧实现沿世界 z 取 s=0.80,会高出赤道约 1 cm——半径才 16.8 mm。
    lo, hi = ent["aabb"]["min"], ent["aabb"]["max"]
    legacy_z = lo[2] + 0.80 * (hi[2] - lo[2])
    assert legacy_z - out["xyz"][2] > 0.009


def test_lying_cylinder_band_reports_end_ambiguous_instead_of_guessing():
    out = _solve_pose(_entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA)), "upper_body")
    assert out["end_ambiguous"] is True
    assert out["region_status"] == "band"
    # 没有可靠的"哪端算 upper"信号 → 取段中点,不猜。
    assert out["xyz"] == pytest.approx(list(_CENTER), abs=1e-12)


def test_lying_cylinder_bands_do_not_differ_when_the_end_is_ambiguous():
    """bottom / upper_body 在横躺时都退到段中点——不猜端序就是不猜。"""
    ent = _entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA))
    assert _solve_pose(ent, "bottom")["xyz"] == pytest.approx(
        _solve_pose(ent, "top")["xyz"], abs=1e-12)


# ==========================================================================
# 向后兼容:物体立着(长轴 ≈ 世界 z)时与旧实现一致。
# ==========================================================================
_STANDING = (_TUBE_DIA, _TUBE_DIA, _TUBE_LEN)


def test_standing_tube_axis_matches_the_legacy_local_z_formula():
    for quat in ([1.0, 0.0, 0.0, 0.0], [math.cos(0.3), 0.0, 0.0, math.sin(0.3)]):
        out = _solve_axis(_entity(_STANDING, quat))
        assert out["vec"] == pytest.approx(_legacy_local_z(quat), abs=1e-12)


@pytest.mark.parametrize("region", sorted(binding._REGION_BAND_CENTER))
def test_standing_tube_band_matches_the_legacy_world_z_formula(region):
    ent = _entity(_STANDING)
    lo, hi = ent["aabb"]["min"], ent["aabb"]["max"]
    legacy = [ent["pos"][0], ent["pos"][1],
              lo[2] + binding._REGION_BAND_CENTER[region] * (hi[2] - lo[2])]

    out = _solve_pose(ent, region)

    assert out["xyz"] == pytest.approx(legacy, abs=1e-12)
    assert out["end_ambiguous"] is False, "立着时上下端有可靠含义,不是歧义"


def test_standing_tube_band_still_orders_bottom_below_top():
    ent = _entity(_STANDING)
    assert (_solve_pose(ent, "bottom")["xyz"][2]
            < _solve_pose(ent, "middle")["xyz"][2]
            < _solve_pose(ent, "top")["xyz"][2])


def test_standing_tube_axis_sign_does_not_flip_the_band_order():
    """资产上下颠倒(局部 +z 朝下)时长轴翻成 +z,region 端序仍由世界 +z 定。"""
    upside_down = _entity(_STANDING, [0.0, 1.0, 0.0, 0.0])   # 绕世界 x 转 180°
    assert (_solve_pose(upside_down, "top")["xyz"][2]
            > _solve_pose(upside_down, "bottom")["xyz"][2])


# ==========================================================================
# 近立方 / 近方形:主方向不可辨 → 拒绝。
# ==========================================================================
def test_near_cube_axis_is_refused():
    with pytest.raises(binding.UnsolvedHole) as error:
        _solve_axis(_entity((0.100, 0.095, 0.090)))
    assert error.value.reason == "axis_ambiguous_extents"


def test_near_cube_region_band_is_refused_too():
    """区带也建立在长轴上;长轴不可辨时 region 没有定义 → 一起拒绝,不退回世界 z。"""
    with pytest.raises(binding.UnsolvedHole) as error:
        _solve_pose(_entity((0.100, 0.095, 0.090)), "upper_body")
    assert error.value.reason == "axis_ambiguous_extents"


def test_dominance_threshold_has_teeth():
    """次/主 = 0.78 收,0.82 拒——阈值不是摆设(边界两侧各留一点浮点余量)。"""
    assert _solve_axis(_entity((0.100, 0.078, 0.078)))["vec"] == pytest.approx(
        [1.0, 0.0, 0.0], abs=1e-12)
    with pytest.raises(binding.UnsolvedHole):
        _solve_axis(_entity((0.100, 0.082, 0.078)))


def test_axis_without_aabb_is_unobserved_not_a_crash():
    ent = {"pos": list(_CENTER), "quat": [1.0, 0.0, 0.0, 0.0]}
    with pytest.raises(binding.UnsolvedHole) as error:
        _solve_axis(ent)
    assert error.value.reason == "axis_unobserved"
