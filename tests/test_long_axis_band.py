"""长轴推断与 region 区带必须跟着物体的真实长轴走。

8/6 ep1 两次复现的实测:管子是**横躺**的(AABB 的 z 跨度就是直径 33.6 mm),但
``solve_axis_3d`` 拿物体**局部 +z** 当长轴——横躺资产的局部 +z 仍近竖直(偏离
4.3°),于是

  - ``_grasp_quat`` 的 yaw 跟着那 4.3° 倾斜的方位角抖,两次 attempt 的抓取四元数
    能差几十度;
  - ``upper_body``(s=0.80)沿 AABB 的**世界 z** 取点,抓取点高出赤道约 1 cm,
    而半径才 16.8 mm——光滑圆柱在赤道以上夹必滑出。

本文件钉死修复后的语义:长轴从 AABB 最长边推断并经实体四元数变换到世界系;区带
沿真实长轴参数化;横躺时哪一端算 upper 没有可靠信号,取段中点并记 ``end_ambiguous``;
物体立着时行为与旧实现一致(向后兼容用旧公式逐点对照钉住);近立方拒绝。

纯逻辑、离线、不触 sim/网络/LLM。
"""

import math

import pytest

from demo_graph_lab.execution import oracle_runtime
from demo_graph_lab.execution.oracle_runtime import OracleRuntime
from demo_graph_lab.selection import binding


# ep1 实测尺寸:管长 110 mm、直径 33.6 mm。
_TUBE_LEN = 0.110
_TUBE_DIA = 0.0336
_CENTER = (0.4, 0.1, 0.8)


def _entity(extents, quat=(1.0, 0.0, 0.0, 0.0), center=_CENTER):
    """按世界系 AABB 边长构造实体(pos 取 AABB 中心,与仿真资产一致)。"""
    half = [item / 2.0 for item in extents]
    return {"pos": list(center), "quat": list(quat),
            "aabb": {"min": [center[i] - half[i] for i in range(3)],
                     "max": [center[i] + half[i] for i in range(3)]}}


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


def _legacy_local_z(quat):
    """旧实现:无条件取物体局部 +z 作长轴。"""
    w, x, y, z = quat
    return [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]


# ==========================================================================
# 横躺圆柱:长轴 = 世界 x / y,不是局部 +z。
# ==========================================================================
def test_lying_cylinder_long_axis_is_world_x():
    out = _solve_axis(_entity((_TUBE_LEN, _TUBE_DIA, _TUBE_DIA)))
    assert out["vec"] == pytest.approx([1.0, 0.0, 0.0], abs=1e-12)
    assert out["axis_source"] == "aabb_longest_edge"


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
