"""8/6 第二集 episode 实测出的四项 oracle 运行时修复。

四项都来自 5090 `~/dgl-stack/evidence/{ep2,taskb}/` 的实测,口径是**第 3 档
「privileged Oracle 调试」**——钉的是特权调试路径的行为,不构成任何成功率。

Behavioral contract:
  1. **按目标选臂 + 不可达独立失败类**:stage 开始时按目标实体的 y 符号选臂
     (+y 左 → arm0,−y 右 → arm1,死区内保持当前臂);持物期间不换臂,`release`
     后才可重选。抓取翻转兜底后 xy 残差仍 > `UNREACHABLE_XY_MM` → 记
     `unreachable_target` 且**不闭爪**,与「夹空了」分开。
  2. **张/闭爪双常数**:`grasp_at`/`approach` 用 `CLAW_TIP_DZ_OPEN`,
     `transport`/`align` 用 `CLAW_TIP_DZ_CLOSED`,两者差 21.9 mm。
  3. **`lower_until` 细步**:步长 5 mm(20 mm 单步实测第 1 步就打出 656 N)。
  4. **MP 熔断**:首次 HTTP 400 后同一 episode 不再调规划;非 400 的失败不熔断。

风格对齐 tests/test_lift_closed_loop.py:纯逻辑、离线、不触 sim/网络/LLM。
用真 OracleRuntime 走真代码路径,只把会发 HTTP / 会真等待的底层动作换成桩。
"""

import time
import urllib.error

import pytest

from demo_graph_lab.execution import oracle_runtime
from demo_graph_lab.execution.oracle_runtime import (
    ALIGN_DZ, ARM_LEFT, ARM_RIGHT, ARM_SELECT_DEADZONE_Y_M,
    CLAW_TIP_DZ_CLOSED, CLAW_TIP_DZ_OPEN, GRIP_CLOSE_TUBE, LOWER_MAX_STEPS,
    LOWER_STEP, PREGRASP_DZ, UNREACHABLE_XY_MM, OracleRuntime,
)
from demo_graph_lab.execution.pipeline import PipelineError

# 8/6 ep2 的 2×2 选臂矩阵实测值(m / mm)。
EP2_TUBE_LEFT_Y = 0.258
EP2_TUBE_RIGHT_Y = -0.365
EP2_ERR_ARM0_LEFT_MM = 4.9        # 同侧,够得着
EP2_ERR_ARM1_RIGHT_MM = 9.0       # 同侧,够得着
EP2_ERR_CROSS_BODY_MM = 31.0      # 跨身体,够不着(实测区间 25–69 mm)


def _entity(x=0.45, y=0.0, z=0.80, half=0.02):
    return {"pos": [x, y, z], "quat": [1.0, 0.0, 0.0, 0.0],
            "aabb": {"min": [x - half, y - half, z - 0.08],
                     "max": [x + half, y + half, z + 0.08]}}


def _find(rt, op):
    return [c for c in rt.calls if c["op"] == op]


# ==========================================================================
# 1. 按目标选臂
# ==========================================================================
def _arm_rt(entities, stage_objects, arm_id=1):
    """真 OracleRuntime + 注入实体缓存;只桩掉会发 HTTP 的归位动作。"""
    graph = {"stages": [{"index": 0, "name": "grasp", "holes": [],
                         "stage_objects": dict(stage_objects)}]}
    rt = OracleRuntime(graph, arm_id=arm_id)
    rt._ents_cache = (time.time() + 1e6, dict(entities))
    rt.parked = []
    rt._park_idle_arm = lambda: rt.parked.append(rt.arm_id)
    return rt


def _both_tubes():
    return {"tube_left": _entity(y=EP2_TUBE_LEFT_Y),
            "tube_right": _entity(y=EP2_TUBE_RIGHT_Y)}


def test_left_target_selects_the_left_arm():
    """目标在左(y=+0.258)→ 选 arm0,即便命令行默认给的是 arm1。

    这正是 ep2 的失败现场:`--arm 1` + 左侧目标 = 31 mm 够不着。
    """
    rt = _arm_rt(_both_tubes(), {"manipulated": "tube_left"}, arm_id=ARM_RIGHT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_LEFT
    rec = _find(rt, "arm_select")[0]
    assert rec["switched"] is True and rec["prev_arm"] == ARM_RIGHT
    assert rec["reason"] == "target_y_sign"
    assert rec["y"] == pytest.approx(EP2_TUBE_LEFT_Y)


def test_right_target_selects_the_right_arm():
    """目标在右(y=−0.365)→ 选 arm1。与上一条构成 2×2 矩阵的对角。"""
    rt = _arm_rt(_both_tubes(), {"manipulated": "tube_right"}, arm_id=ARM_LEFT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_RIGHT
    assert _find(rt, "arm_select")[0]["switched"] is True


def test_arm_switch_parks_the_arm_that_just_became_idle():
    """换臂后用现有 `_park_idle_arm` 把刚空出来的那条臂归位(按新 arm_id 取闲臂)。"""
    rt = _arm_rt(_both_tubes(), {"manipulated": "tube_left"}, arm_id=ARM_RIGHT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.parked == [ARM_LEFT], "归位必须发生在 arm_id 赋值之后,闲臂才是旧的那条"
    assert oracle_runtime.IDLE_ARM[rt.arm_id] == ARM_RIGHT


def test_no_switch_means_no_extra_park():
    """已经是对的臂 → 不换、不多发一次归位指令。"""
    rt = _arm_rt(_both_tubes(), {"manipulated": "tube_right"}, arm_id=ARM_RIGHT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_RIGHT
    assert _find(rt, "arm_select")[0]["switched"] is False
    assert rt.parked == []


def test_holding_an_object_pins_the_arm():
    """持物期间不换臂——换臂等于把手上的东西丢在半空。"""
    rt = _arm_rt(_both_tubes(), {"manipulated": "tube_left"}, arm_id=ARM_RIGHT)
    rt._holding = True
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_RIGHT, "持物期间必须保持当前臂"
    assert _find(rt, "arm_select")[0]["reason"] == "holding_object"
    assert rt.parked == []


def test_release_allows_reselecting_the_arm():
    """`release` 之后才恢复重选;同一个 stage 声明前后行为相反。"""
    rt = _arm_rt(_both_tubes(), {"manipulated": "tube_left"}, arm_id=ARM_RIGHT)
    rt._holding = True
    rt._ctrl = lambda *a, **kw: None
    rt._wait_grip = lambda *a, **kw: 0.0

    rt.release()
    assert rt._holding is False

    rt.begin_stage(rt.graph["stages"][0])
    assert rt.arm_id == ARM_LEFT


def test_deadzone_keeps_the_current_arm():
    """|y| < 死区 → 左右分不开,保持当前臂,不猜。"""
    y = ARM_SELECT_DEADZONE_Y_M / 2.0
    rt = _arm_rt({"tube_mid": _entity(y=y)}, {"manipulated": "tube_mid"},
                 arm_id=ARM_RIGHT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_RIGHT
    rec = _find(rt, "arm_select")[0]
    assert rec["switched"] is False and rec["reason"] == "deadzone_keep_current"


def test_just_outside_the_deadzone_still_switches():
    """死区**外**一点点仍按符号选臂(死区有边界,不是「小于 0.3 m 都不换」)。"""
    y = ARM_SELECT_DEADZONE_Y_M * 1.01
    rt = _arm_rt({"tube_mid": _entity(y=y)}, {"manipulated": "tube_mid"},
                 arm_id=ARM_RIGHT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_LEFT


def test_unresolvable_target_keeps_the_current_arm():
    """目标解析不到 → 保持当前臂并记原因,不 fail-open 成「随便挑一只」。"""
    rt = _arm_rt({}, {"manipulated": "ghost_obj_zzz"}, arm_id=ARM_RIGHT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_RIGHT
    assert _find(rt, "arm_select")[0]["reason"].startswith("unresolved:")


def test_stage_without_objects_keeps_the_current_arm():
    """没有 stage_objects 的阶段不读实体表(runner 对每个 stage 都会调 begin_stage)。"""
    rt = _arm_rt({}, {}, arm_id=ARM_RIGHT)
    rt._entities = lambda *a, **kw: pytest.fail("无 stage_objects 时不该读实体表")
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_RIGHT
    assert _find(rt, "arm_select")[0]["reason"] == "no_stage_object"


def test_target_object_is_used_when_no_manipulated_object():
    """只声明了参照物时用它选臂(而不是放弃选臂)。"""
    rt = _arm_rt({"rack": _entity(y=EP2_TUBE_LEFT_Y)}, {"target": "rack"},
                 arm_id=ARM_RIGHT)
    rt.begin_stage(rt.graph["stages"][0])

    assert rt.arm_id == ARM_LEFT
    assert _find(rt, "arm_select")[0]["obj"] == "rack"


# ==========================================================================
# 1b. 不可达独立失败类
# ==========================================================================
GRASP_TARGET = {"xyz": [0.45, -0.15, 0.80]}
GRASP_AXIS = {"kind": "axis", "hole": "long_axis", "vec": [1.0, 0.0, 0.0]}


def _grasp_rt(err_mm, arm_id=1):
    """真 OracleRuntime,只桩掉会发 HTTP 的动作。

    `_move` 把「到位后的实测落点」设成沿 +x 偏离目标 `err_mm` 毫米,于是
    `_xy_err_mm`/`_retry_flipped_branch`/不可达判据全部真跑。两个 IK 分支给
    同一个残差,对应「跨身体够不着」——翻转救不回来。
    """
    rt = OracleRuntime({"stages": []}, arm_id=arm_id)
    rt.moves, rt.grips = [], []
    state = {"pos": [0.0, 0.0, 0.0], "quat": list(oracle_runtime.TDX0)}

    def _move(xyz, quat=None, **kw):
        rt.moves.append({"xyz": [float(v) for v in xyz],
                         "quat": None if quat is None else [float(v) for v in quat]})
        if quat is not None:
            state["quat"] = [float(v) for v in quat]
        state["pos"] = [xyz[0] + err_mm / 1000.0, xyz[1], xyz[2]]
        return True

    def _ctrl(fn, arm_id=None, **kw):
        if fn == "set_gripper":
            rt.grips.append(kw.get("angle"))
        return True

    rt._move = _move
    rt._ctrl = _ctrl
    rt._cur_xquat = lambda: (list(state["pos"]), list(state["quat"]))
    rt._wait_grip = lambda *a, **kw: 0.0
    rt._grip_angle = lambda: 0.0
    rt._is_gripping = lambda: True
    return rt


def test_unreachable_target_is_its_own_failure_and_does_not_close_the_claw():
    """跨身体 31 mm → 记 `unreachable_target`,且**不闭爪**。

    ep2 的旧行为是照样在空中闭爪,最后由 lift 以 `attached=empty` 结案,于是
    「够不到」和「夹了滑掉」共用同一个 reason。
    """
    rt = _grasp_rt(EP2_ERR_CROSS_BODY_MM)
    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)

    rec = _find(rt, "grasp_failed")
    assert len(rec) == 1, "够不到必须是一条独立的失败记录"
    assert rec[0]["reason"] == "unreachable_target"
    assert rec[0]["closed"] is False
    assert rec[0]["xy_err_mm"] == pytest.approx(EP2_ERR_CROSS_BODY_MM, abs=0.05)
    assert GRIP_CLOSE_TUBE not in rt.grips, "够不到时不得闭爪"
    assert not _find(rt, "grasp_close")


def test_unreachable_target_does_not_enter_the_holding_state():
    """没夹上就不是持物态——否则后续阶段会被错误地钉在这条够不到的臂上。"""
    rt = _grasp_rt(EP2_ERR_CROSS_BODY_MM)
    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)
    assert rt._holding is False


@pytest.mark.parametrize("err_mm", [EP2_ERR_ARM0_LEFT_MM, EP2_ERR_ARM1_RIGHT_MM])
def test_reachable_residual_still_closes_the_claw(err_mm):
    """同侧实测残差(4.9 / 9.0 mm)在阈值内 → 照常闭爪、进入持物态。"""
    rt = _grasp_rt(err_mm)
    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)

    assert not _find(rt, "grasp_failed")
    assert GRIP_CLOSE_TUBE in rt.grips
    assert _find(rt, "grasp_close")
    assert rt._holding is True


def test_unreachable_threshold_has_teeth_on_both_sides():
    """阈值上下两侧行为相反(阈值不是摆设):14.9 mm 闭爪,15.1 mm 不闭。"""
    below = _grasp_rt(UNREACHABLE_XY_MM - 0.1)
    below.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)
    above = _grasp_rt(UNREACHABLE_XY_MM + 0.1)
    above.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)

    assert GRIP_CLOSE_TUBE in below.grips and not _find(below, "grasp_failed")
    assert GRIP_CLOSE_TUBE not in above.grips and _find(above, "grasp_failed")


def test_unreachable_threshold_sits_between_the_measured_clusters():
    """阈值必须落在两簇实测之间:够得着 ≤9.0 mm,够不着 ≥25 mm。"""
    assert EP2_ERR_ARM1_RIGHT_MM < UNREACHABLE_XY_MM < 25.0


def test_unreachable_check_happens_after_the_flip_fallback():
    """判不可达之前必须先给翻转分支一次机会(顺序错了会把可救的抓取判死)。"""
    rt = _grasp_rt(EP2_ERR_CROSS_BODY_MM)
    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)

    ops = [c["op"] for c in rt.calls]
    assert ops.index("grasp_branch") < ops.index("grasp_failed")


# ==========================================================================
# 2. 张/闭爪双常数
# ==========================================================================
# 张/闭爪指尖差:−3.5 → +18.35 mm,即 21.85 mm(实测报数按 21.9 mm 记)。
CLAW_TIP_TRAVEL_MM = 21.85


def test_open_and_closed_claw_constants_match_the_measurement():
    """张爪 −3.5 mm / 闭爪 +18.35 mm,开合一次指尖垂直行程 21.85 mm。"""
    assert CLAW_TIP_DZ_OPEN == pytest.approx(-0.0035)
    assert CLAW_TIP_DZ_CLOSED == pytest.approx(0.01835)
    assert (CLAW_TIP_DZ_CLOSED - CLAW_TIP_DZ_OPEN) * 1000.0 == pytest.approx(
        CLAW_TIP_TRAVEL_MM, abs=0.01)


def _consumer_rt(entities):
    rt = OracleRuntime({"stages": []})
    rt._ents_cache = (time.time() + 1e6, dict(entities))
    rt.moves = []
    rt._move = lambda xyz, quat=None, **kw: rt.moves.append(
        {"xyz": [float(v) for v in xyz]}) or True
    rt._step_to = lambda *a, **kw: True
    rt._park_idle_arm = lambda: None
    rt._cur_xquat = lambda: ([0.45, 0.0, 0.9], list(oracle_runtime.TDX0))
    return rt


def test_grasp_at_positions_with_the_open_claw_constant():
    """`grasp_at` 张着爪下探,EEF 高度 = 爪尖目标 + 张爪偏移。"""
    rt = _grasp_rt(0.0)
    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)
    z0 = GRASP_TARGET["xyz"][2]
    assert rt.moves[-1]["xyz"][2] == pytest.approx(z0 + CLAW_TIP_DZ_OPEN)


def test_approach_offsets_with_the_open_claw_constant():
    """`approach` 是预抓取,爪子还张着 → 用张爪常数。"""
    rt = _consumer_rt({"tube": _entity()})
    rt.approach("tube")
    assert rt.moves[-1]["xyz"][2] == pytest.approx(0.80 + PREGRASP_DZ + CLAW_TIP_DZ_OPEN)


def test_transport_offsets_with_the_closed_claw_constant():
    """`transport` 夹着物体移动,爪子闭合 → 用闭爪常数。"""
    rt = _consumer_rt({"coin": _entity(), "slot": _entity(x=0.6)})
    rt.transport("coin", "slot")
    assert rt.moves[-1]["xyz"][2] == pytest.approx(0.80 + PREGRASP_DZ + CLAW_TIP_DZ_CLOSED)


def test_align_offsets_with_the_closed_claw_constant():
    """`align` 同样是夹着物体 → 闭爪常数。这条正是任务 B 里 −21.9 mm 的那一项。"""
    rt = _consumer_rt({"tube": _entity(), "rack": _entity(x=0.6)})
    rt.align("tube", "rack", axis=None)
    assert rt.moves[-1]["xyz"][2] == pytest.approx(0.80 + ALIGN_DZ + CLAW_TIP_DZ_CLOSED)


def test_closed_claw_consumers_sit_one_travel_above_the_open_claw_ones():
    """同一个目标、同一个标称抬升(``PREGRASP_DZ``)下,闭爪消费点(`transport`)
    比张爪消费点(`approach`)高出的正是实测的指尖行程——此前两处共用张爪值,
    差的就是任务 B 里那 −21.9 mm。"""
    rt_closed = _consumer_rt({"tube": _entity(), "rack": _entity(x=0.6)})
    rt_closed.transport("tube", "rack")
    rt_open = _consumer_rt({"rack": _entity(x=0.6)})
    rt_open.approach("rack")

    delta_mm = (rt_closed.moves[-1]["xyz"][2] - rt_open.moves[-1]["xyz"][2]) * 1000.0
    assert delta_mm == pytest.approx(CLAW_TIP_TRAVEL_MM, abs=0.01)


# ==========================================================================
# 3. lower_until 细步
# ==========================================================================
class _LowerPipe:
    """只记录 ctrl 调用的桩;外力恒定,由用例决定是否触发接触判据。"""

    def __init__(self, force=0.0):
        self.force = force
        self.deltas = []

    def call(self, action, name, kwargs):
        if name == "delta_move":
            self.deltas.append(kwargs["delta_xyz"])
        if name == "get_ee_extforce":
            return [self.force, 0.0, 0.0]
        if name == "get_xquat":
            return [0.45, -0.15, 0.80, 0.0, 1.0, 0.0, 0.0]
        return True


def _lower_rt(force=0.0):
    rt = OracleRuntime({"stages": []})
    rt.pipe = _LowerPipe(force=force)
    rt._wait_settle = lambda *a, **kw: "still"
    rt._verify_moved = lambda *a, **kw: (True, 0.0, 0.0)
    return rt


def test_lower_until_steps_are_five_millimetres():
    """步长 5 mm:1.5 mm 间隙下 20 mm 单步的第 1 步实测 656 N(判据阈 20 N)。"""
    assert LOWER_STEP == pytest.approx(0.005)

    rt = _lower_rt(force=0.0)
    rt.lower_until({"kind": "condition", "purpose": "lower_stop",
                    "stop_kind": "contact"})

    assert len(rt.pipe.deltas) == LOWER_MAX_STEPS
    for delta in rt.pipe.deltas:
        assert delta == [0, 0, -LOWER_STEP]
    assert _find(rt, "lower_until_done")[0]["reason"] == "budget"


def test_lower_until_still_stops_on_contact_force_at_the_finer_step():
    """细步不改停止判据:高力值仍然首步即停(其余判据本轮未动)。"""
    rt = _lower_rt(force=57.0)
    rt.lower_until({"kind": "condition", "purpose": "lower_stop",
                    "stop_kind": "contact"})

    done = _find(rt, "lower_until_done")[0]
    assert done["reason"] == "contact_force" and done["steps"] == 1
    assert len(rt.pipe.deltas) == 1


# ==========================================================================
# 4. MP 熔断
# ==========================================================================
# 用真的 urllib HTTPError 造消息,把熔断判别串钉在 urllib 的实际格式上,
# 而不是手打一个字符串(手打的话改了格式测试也不会红)。
_HTTP_400 = urllib.error.HTTPError("http://127.0.0.1:8000/run", 400,
                                   "Bad Request", None, None)
_HTTP_503 = urllib.error.HTTPError("http://127.0.0.1:8000/run", 503,
                                   "Service Unavailable", None, None)


def _transport_error(http_error):
    return PipelineError(
        f"reasoning:motion_planning_stereo transport failed: {http_error}")


class _MPPipe:
    """规划恒失败的桩;伺服退化路径一发即到位,避免跑满迭代预算。"""

    def __init__(self, error):
        self.error = error
        self.names = []
        self.xquat = [0.10, -0.10, 1.10] + list(oracle_runtime.TDX0)

    def call(self, action, name, kwargs):
        self.names.append(name)
        if name == "motion_planning_stereo":
            raise self.error
        if name == "get_qpos":
            return [0.0] * 7
        if name == "get_xquat":
            return list(self.xquat)
        if name == "get_ee_extforce":
            return [0.0, 0.0, 0.0]
        if name == "xquat_move":
            self.xquat = list(kwargs["target_xyz"]) + list(kwargs["target_quat"])
        return True


TARGET_A = [0.45, -0.15, 0.88]
TARGET_B = [0.30, 0.20, 0.95]


def _mp_rt(pipe):
    rt = OracleRuntime({"stages": []}, arm_id=1)
    rt.pipe = pipe
    rt._wait_settle = lambda *a, **kw: "still"
    return rt


def test_mp_is_disabled_after_the_first_http_400():
    """首次 400 之后同一 episode 不再调规划;熔断只记一次。

    ep2 实测:隔离总线上没有 motion_planning_stereo 后端,每次白等 400+20 s,
    单集烧掉 200 s(29% wall),100% 失败后全部走 degraded。
    """
    pipe = _MPPipe(_transport_error(_HTTP_400))
    rt = _mp_rt(pipe)

    rt._move(TARGET_A, quat=oracle_runtime.TDX0)
    rt._move(TARGET_B, quat=oracle_runtime.TDX0)

    assert pipe.names.count("motion_planning_stereo") == 1, "400 之后不得再调规划"
    assert len(_find(rt, "mp_disabled_after_400")) == 1
    fallbacks = _find(rt, "mp_fallback")
    assert len(fallbacks) == 2 and all(f["degraded"] is True for f in fallbacks)
    assert fallbacks[1]["reason"] == "mp_disabled_after_400"
    assert "xquat_move" in pipe.names, "熔断后仍要走 degraded 伺服,不是不动"


def test_non_400_failures_do_not_trip_the_breaker():
    """非 400(可能是瞬时的)保留原逐次 fallback 语义,不熔断。"""
    pipe = _MPPipe(_transport_error(_HTTP_503))
    rt = _mp_rt(pipe)

    rt._move(TARGET_A, quat=oracle_runtime.TDX0)
    rt._move(TARGET_B, quat=oracle_runtime.TDX0)

    assert pipe.names.count("motion_planning_stereo") == 2, "非 400 失败仍逐次尝试"
    assert not _find(rt, "mp_disabled_after_400")
    assert [f["reason"] for f in _find(rt, "mp_fallback")] == ["backend", "backend"]


def test_breaker_marker_matches_the_real_urllib_message():
    """判别串对着 urllib 的真实 `HTTPError.__str__`,不是手打的近似。"""
    assert oracle_runtime._is_mp_unavailable(_transport_error(_HTTP_400))
    assert not oracle_runtime._is_mp_unavailable(_transport_error(_HTTP_503))
    assert not oracle_runtime._is_mp_unavailable(
        RuntimeError("target_xyz=[0.400, 0.0, 0.8]"))     # 坐标里的 400 不算


def test_breaker_is_per_runtime_instance():
    """熔断闸的作用域是「同一个 episode」= 同一个 runtime 实例,不是进程全局。"""
    pipe_a = _MPPipe(_transport_error(_HTTP_400))
    rt_a = _mp_rt(pipe_a)
    rt_a._move(TARGET_A, quat=oracle_runtime.TDX0)
    assert rt_a._mp_disabled is True

    pipe_b = _MPPipe(_transport_error(_HTTP_400))
    rt_b = _mp_rt(pipe_b)
    assert rt_b._mp_disabled is False
    rt_b._move(TARGET_A, quat=oracle_runtime.TDX0)
    assert pipe_b.names.count("motion_planning_stereo") == 1
