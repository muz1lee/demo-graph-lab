"""OracleRuntime 运动路径单测:_move 的规划接线、关节回读来源、抓取 IK 分支。

Behavioral contract:
  - `_move` **默认走 MP 路径**:调 reasoning:motion_planning_stereo 规划,再逐航点 ctrl:qpos_move
     执行;全程不出现手写伺服的 ctrl:xquat_move。
  - 规划失败(PlanFailed)时**退回手写伺服**,并显式记账 `mp_fallback`(degraded=true)——
     退化路径不得静默使用。
  - MP 路径按 cartesian_goal 下发,目标位姿 = 调用者给的 xyz + quat(quat=None 时取竖直姿态)。
  - `_arm_qpos` 的关节真值**只来自 pipeline `info:get_qpos`**;不从 `/state` 的
     `robot_qpos` 按固定步长切片猜索引,形状不对就抛错(fail-closed),不返回错值。

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
