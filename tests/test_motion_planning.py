"""OracleRuntime 运动路径单测:_move 的规划接线、关节回读来源、抓取 IK 分支。

Behavioral contract:
  - `_move` **默认走 MP 路径**:调 reasoning:motion_planning_stereo 规划,再逐航点 ctrl:qpos_move
     执行;全程不出现手写伺服的 ctrl:xquat_move。
  - 规划失败(PlanFailed)时**退回手写伺服**,并显式记账 `mp_fallback`(degraded=true)——
     退化路径不得静默使用。
  - MP 路径按 cartesian_goal 下发,目标位姿 = 调用者给的 xyz + quat(quat=None 时取竖直姿态)。
  - `_arm_qpos` 的关节真值**只来自 pipeline `info:get_qpos`**;不从 `/state` 的
     `robot_qpos` 按固定步长切片猜索引,形状不对就抛错(fail-closed),不返回错值。
  - 抓取到位后 xy 残差超过 `GRASP_XY_RETRY_MM` 时,绕**工具**接近轴翻转 180° 重试一次
     (平行夹爪两指对称 → 另一个 IK 分支),两支的实测残差都进账本。

离线:复用 tests/fixtures/mp_fixture_joint_goal.json 的真实响应,不触 sim/网络/LLM。
"""

import json
from pathlib import Path

import pytest

from demo_graph_lab.execution import oracle_runtime, robot_api
from demo_graph_lab.execution.pipeline import wire_value

_FIX = Path(__file__).resolve().parent / "fixtures" / "mp_fixture_joint_goal.json"


class MovePipe:
    """PipelineClient 替身:记录全部调用;reasoning 可回放 fixture 或抛错(测退化路径)。

    get_xquat 返回的位姿会随 qpos_move / xquat_move 推进到 `_reach`,以便 `_move` 的
    到位判据能收敛(否则伺服分支会跑满迭代预算,拖慢单测)。
    get_qpos 同理**一发即到位**(qpos_move 后立刻回读为目标航点),否则 robot_api._wait_qpos
    会对 92 个航点各空等一个 8 s 超时窗。
    """

    def __init__(self, mp_result, xquat, *, plan_raises=False, reach=None):
        self._mp_result = mp_result
        self._xquat = list(xquat)
        self.plan_raises = plan_raises
        self._reach = list(reach) if reach else None
        self._qpos = {0: [0.0] * 7, 1: [0.0] * 7}
        self.calls = []

    def call(self, action, name, kwargs):
        self.calls.append((action, name, dict(kwargs)))
        if action == "info" and name == "get_qpos":
            return list(self._qpos[int(kwargs["arm_id"])])
        if action == "info" and name == "get_xquat":
            return list(self._xquat)
        if action == "info" and name == "get_ee_extforce":
            return [0.0, 0.0, 0.0]
        if action == "reasoning" and name == "motion_planning_stereo":
            if self.plan_raises:
                raise RuntimeError("backend refused (simulated)")
            return self._mp_result
        if action == "ctrl" and name in ("qpos_move", "xquat_move", "delta_move"):
            if name == "qpos_move":          # 关节侧一发即到位,让 _wait_qpos 立刻收敛
                self._qpos[int(kwargs["arm_id"])] = list(kwargs["qpos"])
            if self._reach is not None:      # 笛卡尔侧一发即到位,让到位判据收敛
                self._xquat = list(self._reach)
            return True
        raise AssertionError(f"unexpected call {action}:{name}")

    def names(self, action=None):
        return [n for a, n, _ in self.calls if action is None or a == action]


# 8/6 v4 单世界栈实测的 /state 布局:robot_qpos 长 29,右臂真实下标不等距。
V4_QPOS_LEN = 29
V4_RIGHT_ARM_SLOTS = (1, 3, 6, 9, 11, 13, 15)
V4_RIGHT_ARM_Q = [0.10, -0.20, 0.30, -0.40, 0.50, -0.60, 0.70]
# 旧实现的交错切片 [1::2][:7] 会命中 5/7 两个**非本臂**槽位;实测取出的 j6 是这个值,
# 它落在该关节自身限位之外——关节不可能越过自己的限位,这正是切错的判别依据。
V4_J6_OUT_OF_LIMIT = -2.1813
V4_J6_LIMIT = (-1.308, 1.570)


class StillEval:
    """EvalClient 替身。`_wait_settle` 的关节回读已改走 pipeline `info:get_qpos`
    (见 test_arm_qpos_* ),这里只是让 rt.eval 有个不发 HTTP 的占位对象。"""

    def state(self):
        return {"robot_qpos": [0.0] * V4_QPOS_LEN, "entities": {}, "probes": []}


def _rt(pipe):
    """真 OracleRuntime,但 pipe/eval 换成替身;graph 为空(_move 不读图)。"""
    rt = oracle_runtime.OracleRuntime.__new__(oracle_runtime.OracleRuntime)
    rt.graph, rt.arm_id = {"stages": []}, 1
    rt.pipe = pipe
    rt.eval = StillEval()
    rt.registry = []
    rt.calls = []
    rt._hole_index = {}
    rt._current_stage = None
    return rt


def _mp_result():
    return wire_value(json.loads(_FIX.read_text())["response_raw"]["result"])


# 目标位姿:xyz + 竖直朝下四元数(TDX0)。起始位姿离目标很远,保证不会「一开始就到位」。
TARGET_XYZ = [0.45, -0.15, 0.88]
START_XQUAT = [0.10, -0.10, 1.10] + list(oracle_runtime.TDX0)
REACHED_XQUAT = TARGET_XYZ + list(oracle_runtime.TDX0)


def test_move_prefers_motion_planning_path():
    """默认走 MP;下发 cartesian_goal;执行用 qpos_move;不碰 xquat_move。"""
    pipe = MovePipe(_mp_result(), START_XQUAT, reach=REACHED_XQUAT)
    rt = _rt(pipe)

    ok = rt._move(TARGET_XYZ, quat=oracle_runtime.TDX0)

    assert ok is True
    assert "motion_planning_stereo" in pipe.names("reasoning")
    assert "qpos_move" in pipe.names("ctrl")
    assert "xquat_move" not in pipe.names("ctrl")      # 手写伺服未被触发

    plan_kwargs = next(kw for a, n, kw in pipe.calls if n == "motion_planning_stereo")
    assert "mp.planning_mode=cartesian_goal" in plan_kwargs["text"]
    assert plan_kwargs["data"][:3] == pytest.approx(TARGET_XYZ)     # 目标位置进 data
    assert plan_kwargs["data"][3:7] == pytest.approx(list(oracle_runtime.TDX0))
    assert plan_kwargs["arm_id"] == 1

    ops = [c["op"] for c in rt.calls]
    assert "move_mp" in ops and "mp_fallback" not in ops


def test_move_quat_none_uses_vertical_pose_as_goal():
    """quat=None 时目标姿态取「离当前腕姿最近的竖直姿态」,而非留空(cartesian_goal 必须给 7 元)。"""
    pipe = MovePipe(_mp_result(), START_XQUAT, reach=REACHED_XQUAT)
    rt = _rt(pipe)

    rt._move(TARGET_XYZ)

    plan_kwargs = next(kw for a, n, kw in pipe.calls if n == "motion_planning_stereo")
    assert len(plan_kwargs["data"]) == 7
    expected_q = oracle_runtime._topdown_like(START_XQUAT[3:7])
    assert plan_kwargs["data"][3:7] == pytest.approx(expected_q)


def test_move_falls_back_to_servo_and_books_mp_fallback():
    """规划失败 → 退回手写伺服(xquat_move),且记 mp_fallback(degraded=true),不静默。"""
    pipe = MovePipe(_mp_result(), START_XQUAT, plan_raises=True, reach=REACHED_XQUAT)
    rt = _rt(pipe)

    ok = rt._move(TARGET_XYZ, quat=oracle_runtime.TDX0)

    assert ok is True                                   # 伺服在替身下能到位
    assert "xquat_move" in pipe.names("ctrl")           # 退化路径确被使用
    assert "qpos_move" not in pipe.names("ctrl")        # MP 执行未发生

    fb = [c for c in rt.calls if c["op"] == "mp_fallback"]
    assert len(fb) == 1
    assert fb[0]["degraded"] is True
    assert fb[0]["reason"] == "backend"                 # PlanFailed 的归因字段


def test_servo_path_still_available_directly():
    """_move_servo 是独立退化路径,且只走 xquat_move。"""
    pipe = MovePipe(_mp_result(), START_XQUAT, reach=REACHED_XQUAT)
    rt = _rt(pipe)

    rt._move_servo(TARGET_XYZ, quat=oracle_runtime.TDX0)

    assert "xquat_move" in pipe.names("ctrl")
    assert "motion_planning_stereo" not in pipe.names("reasoning")


# ---------------------------------------------------------------------------
# approach 的 cone 参数形状归一。
# ---------------------------------------------------------------------------
def test_cone_name_normalizes_constraint_args_dict():
    """编译出的 policy 传的是 `approach_direction` 约束的**整个 args**
    ({"cone": "top_down", "target": ...}),而 regions.cone_angle_deg 以锥名求目标倾角。
    _cone_name 负责形状归一;不归一则 TypeError: unhashable type: 'dict'。"""
    rt = _rt(MovePipe(_mp_result(), START_XQUAT))
    assert rt._cone_name({"cone": "top_down", "target": "tube_left"}) == "top_down"
    assert rt._cone_name("side") == "side"                 # 已是锥名则原样
    assert rt._cone_name(None) is None
    assert rt._cone_name({"target": "rack"}) is None        # 取不出锥名 → None(调用方记账)
    assert rt._cone_name(["top_down"]) is None


def test_cone_dict_is_accepted_by_regions_ranking():
    """归一后的锥名必须能被 regions.rank_by_cone 直接消费(闭环校验封闭词表)。"""
    from demo_graph_lab.graph import vocab
    from demo_graph_lab.selection import regions
    rt = _rt(MovePipe(_mp_result(), START_XQUAT))
    name = rt._cone_name({"cone": "top_down", "target": "tube_left"})
    assert name in vocab.APPROACH_CONES
    ranked = regions.rank_by_cone(rt._APPROACH_DIR_CANDIDATES, name)
    assert ranked[0]["id"] == "down"        # top_down 锥的 top-1 是竖直下探


# ---------------------------------------------------------------------------
# _arm_qpos:关节真值只来自 pipeline get_qpos,不从 /state 的 robot_qpos 猜索引。
# ---------------------------------------------------------------------------
class TrapEval:
    """EvalClient 替身:按 8/6 v4 实测布局摆右臂关节值,并在旧交错切片会命中的
    非本臂槽位(5、7)埋越限位的值。任何仍从 /state 切片的实现都会取到错值。"""

    def __init__(self):
        self.qpos = [0.0] * V4_QPOS_LEN
        for slot, v in zip(V4_RIGHT_ARM_SLOTS, V4_RIGHT_ARM_Q):
            self.qpos[slot] = v
        self.qpos[5] = V4_J6_OUT_OF_LIMIT
        self.qpos[7] = V4_J6_OUT_OF_LIMIT

    def state(self):
        return {"robot_qpos": list(self.qpos), "entities": {}, "probes": []}


def _qpos_rt(pipe):
    rt = _rt(pipe)
    rt.eval = TrapEval()
    return rt


def test_arm_qpos_reads_pipeline_get_qpos_not_state_slice():
    """真值来自 info:get_qpos;同一瞬间 /state 的交错切片给的是越限位的错值。"""
    pipe = MovePipe(_mp_result(), START_XQUAT)
    pipe._qpos[1] = list(V4_RIGHT_ARM_Q)
    rt = _qpos_rt(pipe)

    q = rt._arm_qpos()

    assert q == pytest.approx(V4_RIGHT_ARM_Q)
    assert "get_qpos" in pipe.names("info")
    # 旧路径的反证:交错切片取出的值越过了关节自身限位,物理上不可能。
    interleaved = rt.eval.state()["robot_qpos"][1::2][:7]
    assert interleaved != pytest.approx(V4_RIGHT_ARM_Q)
    assert min(interleaved) < V4_J6_LIMIT[0]
    assert min(q) > V4_J6_LIMIT[0] and max(q) < V4_J6_LIMIT[1]


def test_arm_qpos_reads_the_requested_arm():
    """arm_id 显式传入时读那条臂(_park_idle_arm 靠这条读闲臂)。"""
    pipe = MovePipe(_mp_result(), START_XQUAT)
    pipe._qpos[0] = [0.9] * 7
    pipe._qpos[1] = list(V4_RIGHT_ARM_Q)
    rt = _qpos_rt(pipe)

    assert rt._arm_qpos(0) == pytest.approx([0.9] * 7)
    assert rt._arm_qpos(1) == pytest.approx(V4_RIGHT_ARM_Q)
    assert [kw["arm_id"] for a, n, kw in pipe.calls if n == "get_qpos"] == [0, 1]


class BadLenPipe:
    """get_qpos 返回整条 robot_qpos(29 个数)的坏底座:形状不对必须抛错,不能截前 7 个用。"""

    def call(self, action, name, kwargs):
        if action == "info" and name == "get_qpos":
            return [0.0] * V4_QPOS_LEN
        raise AssertionError(f"unexpected call {action}:{name}")

    def names(self, action=None):
        return []


def test_arm_qpos_fails_closed_on_unknown_layout():
    """读不到规定的 7 元组 → 抛错;`_wait_settle` 因此退化成 timeout,而不是用错值判静止。"""
    rt = _qpos_rt(BadLenPipe())

    with pytest.raises(ValueError):
        rt._arm_qpos()
    assert rt._wait_settle(timeout_s=0.5) == "timeout"


# ---------------------------------------------------------------------------
# 抓取的 IK 分支翻转兜底(平行夹爪 180° 对称)。
# ---------------------------------------------------------------------------
def test_flip_about_approach_is_a_tool_frame_180_about_the_approach_axis():
    """翻转 = 绕**工具**接近轴 180°:接近轴不变、指轴同线反向、翻两次回到原姿态。"""
    q = oracle_runtime._tdx(37.0)
    f = oracle_runtime._flip_about_approach(q)
    ax_q, ax_f = oracle_runtime._tool_axes(q), oracle_runtime._tool_axes(f)
    a, g = oracle_runtime.APPROACH_AXIS_IDX, oracle_runtime.FINGER_AXIS_IDX

    assert ax_f[a] == pytest.approx(ax_q[a], abs=1e-9)                  # 接近轴不变
    assert ax_f[g] == pytest.approx([-v for v in ax_q[g]], abs=1e-9)    # 指轴同一条直线、反向
    assert oracle_runtime._qang(q, f) == pytest.approx(180.0, abs=1e-6)
    # 翻两次回到原姿态(四元数可差一个整体符号)。
    ff = oracle_runtime._flip_about_approach(f)
    assert abs(sum(x * y for x, y in zip(ff, q))) == pytest.approx(1.0, abs=1e-9)
    # 与 _tdx 的 yaw+180 是同一个姿态,即 _qmul(TDX0, Rz) 那套工具系约定。
    assert f == pytest.approx(oracle_runtime._tdx(37.0 + 180.0), abs=1e-9)


def test_flip_is_not_a_world_frame_rotation():
    """腕姿不是正下方时,工具系右乘与世界系左乘结果不同——乘序错了会被这条抓住。"""
    tilted = oracle_runtime._qnorm(oracle_runtime._qmul(
        oracle_runtime._qaxis([1.0, 0.0, 0.0], 25.0), oracle_runtime._tdx(37.0)))
    tool = oracle_runtime._flip_about_approach(tilted)
    world = oracle_runtime._qnorm(oracle_runtime._qmul(
        oracle_runtime._qaxis([0.0, 0.0, 1.0], 180.0), tilted))
    assert oracle_runtime._qang(tool, world) > 1.0


GRASP_TARGET = {"xyz": [0.45, -0.15, 0.80]}
GRASP_AXIS = {"kind": "axis", "hole": "long_axis", "vec": [1.0, 0.0, 0.0]}


def _grasp_rt():
    """真 OracleRuntime,只桩掉会发 HTTP 的动作。`_move` 记录被命令的腕姿,并把
    「到位后的实测落点」按分支设成沿 +x 偏离目标 `_xy_err_of_branch(quat)` 毫米。"""
    rt = oracle_runtime.OracleRuntime.__new__(oracle_runtime.OracleRuntime)
    rt.arm_id, rt.calls, rt.moves = 1, [], []
    rt._xy_err_of_branch = lambda quat: 0.0          # 由用例覆盖
    state = {"pos": [0.0, 0.0, 0.0], "quat": list(oracle_runtime.TDX0)}

    def _move(xyz, quat=None, **kw):
        rt.moves.append({"xyz": [float(v) for v in xyz],
                         "quat": None if quat is None else [float(v) for v in quat]})
        if quat is not None:
            state["quat"] = [float(v) for v in quat]
        state["pos"] = [xyz[0] + rt._xy_err_of_branch(quat) / 1000.0, xyz[1], xyz[2]]
        return True

    rt._move = _move
    rt._cur_xquat = lambda: (list(state["pos"]), list(state["quat"]))
    rt._ctrl = lambda *a, **kw: None
    rt._wait_grip = lambda *a, **kw: 0.0
    rt._grip_angle = lambda: 0.0
    rt._is_gripping = lambda: True
    return rt


def _branch_err(default_q, flipped_q, err_default, err_flipped):
    def f(quat):
        if quat is None:
            return 0.0                                # 预抓取那次不锁腕姿,不参与分支判定
        if oracle_runtime._qang(quat, flipped_q) < 1.0:
            return err_flipped
        if oracle_runtime._qang(quat, default_q) < 1.0:
            return err_default
        raise AssertionError("下探用了既不是默认也不是翻转的腕姿")
    return f


def _branch_log(rt):
    return [c for c in rt.calls if c["op"] == "grasp_branch"]


def test_grasp_retries_flipped_branch_when_xy_error_exceeds_threshold():
    """8/6 v4 实测数字:默认分支 15.3 mm(超 8 mm 阈值)→ 翻转重试得 3.6 mm,停在翻转支。"""
    rt = _grasp_rt()
    dq, _ = rt._grasp_quat(GRASP_AXIS)
    fq = oracle_runtime._flip_about_approach(dq)
    rt._xy_err_of_branch = _branch_err(dq, fq, 15.3, 3.6)

    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)

    rec = _branch_log(rt)
    assert len(rec) == 1
    assert rec[0]["branch"] == "flipped" and rec[0]["retried"] is True
    assert rec[0]["default_xy_err_mm"] == pytest.approx(15.3, abs=0.05)
    assert rec[0]["flipped_xy_err_mm"] == pytest.approx(3.6, abs=0.05)
    assert rec[0]["xy_err_mm"] == pytest.approx(3.6, abs=0.05)
    assert rt.moves[-1]["quat"] == pytest.approx(fq)      # 闭爪前停在翻转支的腕姿上


def test_grasp_keeps_default_branch_when_xy_error_is_small():
    """残差在阈值内 → 不翻转、不重试,只下探一次。"""
    rt = _grasp_rt()
    dq, _ = rt._grasp_quat(GRASP_AXIS)
    fq = oracle_runtime._flip_about_approach(dq)
    rt._xy_err_of_branch = _branch_err(dq, fq, 3.6, 15.3)

    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)

    rec = _branch_log(rt)
    assert len(rec) == 1
    assert rec[0]["branch"] == "default" and rec[0]["retried"] is False
    assert "flipped_xy_err_mm" not in rec[0]
    assert len(rt.moves) == 2                            # 预抓取 + 一次下探,没有第二次
    assert rt.moves[-1]["quat"] == pytest.approx(dq)


def test_grasp_restores_default_branch_when_flip_is_worse():
    """翻转后更差 → 退回默认分支再闭爪,账本如实记两支残差与 restored。"""
    rt = _grasp_rt()
    dq, _ = rt._grasp_quat(GRASP_AXIS)
    fq = oracle_runtime._flip_about_approach(dq)
    rt._xy_err_of_branch = _branch_err(dq, fq, 12.0, 40.0)

    rt.grasp_at(GRASP_TARGET, axis=GRASP_AXIS)

    rec = _branch_log(rt)
    assert len(rec) == 1
    assert rec[0]["branch"] == "default" and rec[0]["retried"] is True
    assert rec[0]["restored"] is True
    assert rec[0]["default_xy_err_mm"] == pytest.approx(12.0, abs=0.05)
    assert rec[0]["flipped_xy_err_mm"] == pytest.approx(40.0, abs=0.05)
    assert rt.moves[-1]["quat"] == pytest.approx(dq)      # 不在更差的构型上闭爪
