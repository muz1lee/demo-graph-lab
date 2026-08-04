"""Tests for robot_api.plan_joint_path and execute_path.

用录制的 motion_planning_stereo joint_goal live/head 请求与响应 fixture
(`tests/fixtures/mp_fixture_joint_goal.json`)驱动,离线不触 sim/网络/LLM:
  - FakePipe 按 fixture 的 response_raw.result 经同款 wire_value 还原为 tuple,复现 pipe.call。
  - 断言 plan_joint_path:mp.* 控制 token 装配正确、扁平轨迹 reshape 成 N×7、末航点=q_goal。
  - 断言 execute_path:逐航点 qpos_move + get_qpos 收敛核对,ctrl 假 ok 不被当到位。
  - 断言契约防御:非 7 整数倍扁平轨迹 → PlanFailed;后端异常 → PlanFailed。

风格对齐 tests/test_solve_dispatch.py:纯逻辑、离线。
"""

import ast
import json
from pathlib import Path

import pytest

from demo_graph_lab.execution import robot_api
from demo_graph_lab.execution.pipeline import wire_value

_FIX = Path(__file__).resolve().parent / "fixtures" / "mp_fixture_joint_goal.json"


def _load_fixture():
    return json.loads(_FIX.read_text())


class FakePipe:
    """离线 PipelineClient 替身:info 走桩状态,reasoning 回放 fixture,ctrl 记录并可注入收敛。

    - get_qpos: 返回 _qpos[arm_id](执行时可被 qpos_move 推进,模拟收敛)。
    - get_xquat: 返回固定 7 元位姿(名义 data 占位)。
    - reasoning motion_planning_stereo: 回放 fixture 的 wire_value(result) 元组。
    - ctrl qpos_move: 记录调用;若 converge_on_send,则把 _qpos[arm] 设为目标(模拟到位)。
    """

    def __init__(self, *, mp_result, qpos0, qpos1, xquat, converge_on_send=True):
        self._mp_result = mp_result
        self._qpos = {0: list(qpos0), 1: list(qpos1)}
        self._xquat = list(xquat)
        self.converge_on_send = converge_on_send
        self.calls = []

    def call(self, action, name, kwargs):
        self.calls.append((action, name, dict(kwargs)))
        if action == "info" and name == "get_qpos":
            return list(self._qpos[int(kwargs["arm_id"])])
        if action == "info" and name == "get_xquat":
            return list(self._xquat)
        if action == "reasoning" and name == "motion_planning_stereo":
            return self._mp_result
        if action == "ctrl" and name == "qpos_move":
            if self.converge_on_send:
                self._qpos[int(kwargs["arm_id"])] = list(kwargs["qpos"])
            return True
        raise AssertionError(f"unexpected call {action}:{name}")


class FakeRT:
    def __init__(self, pipe, arm_id=1):
        self.pipe = pipe
        self.arm_id = arm_id
        self.calls = []

    def _log(self, op, **kw):
        self.calls.append({"op": op, **kw})


def _fake_from_fixture(**over):
    fx = _load_fixture()
    kw = fx["request"]["kwargs"]
    mp_result = wire_value(fx["response_raw"]["result"])  # → (['head'], [644 floats])
    defaults = dict(
        mp_result=mp_result,
        qpos0=kw["q_other_arm"],   # left arm nominal
        qpos1=kw["q_current"],     # right arm current
        xquat=kw["data"],
    )
    defaults.update(over)
    return FakePipe(**defaults)


# ---------------------------------------------------------------------------
# plan_joint_path
# ---------------------------------------------------------------------------
def test_fixture_result_is_tuple_not_mapping():
    """契约锚点:motion_planning_stereo 返回 tuple(text_out, flat),不是 Mapping。
    这正是 robot_api 必须走 pipe.call 而非 pipe.reasoning 的原因。"""
    mp = wire_value(_load_fixture()["response_raw"]["result"])
    assert isinstance(mp, tuple) and len(mp) == 2
    assert isinstance(mp[0], list)                     # text_out
    assert len(mp[1]) % robot_api.JOINTS_PER_WAYPOINT == 0


def test_plan_reshapes_flat_to_n_by_7():
    fx = _load_fixture()
    kw = fx["request"]["kwargs"]
    pipe = _fake_from_fixture()
    rt = FakeRT(pipe, arm_id=1)
    plan = robot_api.plan_joint_path(rt, 1, kw["q_goal"], q_current=kw["q_current"],
                                    q_other_arm=kw["q_other_arm"], data=kw["data"])
    assert plan.n_waypoints == fx["parsed"]["n_waypoints"]        # 92
    assert all(len(wp) == 7 for wp in plan.waypoints)
    # 扁平总数守恒
    assert plan.n_waypoints * 7 == fx["parsed"]["flat_len"]       # 644
    assert plan.planning_mode == "joint_goal"


def test_plan_control_tokens_assembled():
    """mp.* token 装配正确:version/intent/planning_mode/scene_input/scene_camera。"""
    kw = _load_fixture()["request"]["kwargs"]
    pipe = _fake_from_fixture()
    rt = FakeRT(pipe, arm_id=1)
    robot_api.plan_joint_path(rt, 1, kw["q_goal"], q_current=kw["q_current"],
                             q_other_arm=kw["q_other_arm"], data=kw["data"])
    reasoning_calls = [c for c in pipe.calls if c[0] == "reasoning"]
    assert len(reasoning_calls) == 1
    text = reasoning_calls[0][2]["text"]
    assert "mp.version=1" in text
    assert "mp.intent=plan" in text
    assert "mp.planning_mode=joint_goal" in text
    assert "mp.scene_input=live" in text
    assert "mp.scene_camera=head" in text
    # data 必是 7 元(_parse_arm_data 契约)
    assert len(reasoning_calls[0][2]["data"]) == 7
    assert reasoning_calls[0][2]["q_goal"] == [float(v) for v in kw["q_goal"]]


def test_plan_last_waypoint_matches_goal():
    """末航点应收敛到 q_goal(规划到位)——判据「末点到目标」的离线复核。"""
    kw = _load_fixture()["request"]["kwargs"]
    pipe = _fake_from_fixture()
    rt = FakeRT(pipe, arm_id=1)
    plan = robot_api.plan_joint_path(rt, 1, kw["q_goal"], q_current=kw["q_current"],
                                    q_other_arm=kw["q_other_arm"], data=kw["data"])
    last = plan.waypoints[-1]
    maxdev = max(abs(a - b) for a, b in zip(last, kw["q_goal"]))
    assert maxdev < 0.01   # rad;fixture 实测末航点与 q_goal 差 <0.001


def test_plan_reads_qpos_when_not_given():
    """q_current/q_other_arm 未传时应从 info:get_qpos 读。"""
    kw = _load_fixture()["request"]["kwargs"]
    pipe = _fake_from_fixture()
    rt = FakeRT(pipe, arm_id=1)
    robot_api.plan_joint_path(rt, 1, kw["q_goal"], data=kw["data"])
    getq = [c for c in pipe.calls if c[1] == "get_qpos"]
    # 至少读了当前臂与另一臂
    arms = {c[2]["arm_id"] for c in getq}
    assert arms == {0, 1}


def test_plan_bad_trajectory_len_raises():
    """扁平轨迹非 7 整数倍 → PlanFailed(契约防御,不静默)。"""
    pipe = _fake_from_fixture(mp_result=(["head"], [0.0] * 10))  # 10 不是 7 的倍数
    rt = FakeRT(pipe, arm_id=1)
    with pytest.raises(robot_api.PlanFailed) as ei:
        robot_api.plan_joint_path(rt, 1, [0.0] * 7, q_current=[0.0] * 7,
                                 q_other_arm=[0.0] * 7, data=[0.0] * 7)
    assert ei.value.reason == "bad_trajectory_len"


def test_plan_backend_error_raises_planfailed():
    """后端异常会包装为可归因的 PlanFailed。"""
    class Boom(FakePipe):
        def call(self, action, name, kwargs):
            if action == "reasoning":
                raise RuntimeError("motion_planning_stereo: no_tsdf_cache")
            return super().call(action, name, kwargs)
    fx = _load_fixture()["request"]["kwargs"]
    pipe = Boom(mp_result=None, qpos0=fx["q_other_arm"], qpos1=fx["q_current"], xquat=fx["data"])
    rt = FakeRT(pipe, arm_id=1)
    with pytest.raises(robot_api.PlanFailed) as ei:
        robot_api.plan_joint_path(rt, 1, fx["q_goal"], q_current=fx["q_current"],
                                 q_other_arm=fx["q_other_arm"], data=fx["data"])
    assert ei.value.reason == "backend"
    assert ei.value.layer == "planning"


def test_plan_cartesian_goal_mode():
    """cartesian_goal:目标位姿从 q_goal_or_pose(7 元 pose)取,不传 q_goal。"""
    kw = _load_fixture()["request"]["kwargs"]
    pipe = _fake_from_fixture()
    rt = FakeRT(pipe, arm_id=1)
    pose = kw["data"]  # [pos3, quat4]
    plan = robot_api.plan_joint_path(rt, 1, pose, planning_mode="cartesian_goal",
                                    q_current=kw["q_current"], q_other_arm=kw["q_other_arm"])
    assert plan.planning_mode == "cartesian_goal"
    rc = [c for c in pipe.calls if c[0] == "reasoning"][0][2]
    assert "mp.planning_mode=cartesian_goal" in rc["text"]
    assert "q_goal" not in rc              # cartesian 不带 q_goal
    assert rc["data"] == [float(v) for v in pose]


# ---------------------------------------------------------------------------
# execute_path
# ---------------------------------------------------------------------------
def test_execute_streams_waypoints_and_converges_endpoint():
    """流式执行连续下发中间点,只确认终点收敛。

    契约:点数被抽稀到 ≤ EXEC_MAX_WAYPOINTS;**终点必须原样下发**
    (抽稀不许丢终点,否则精度就退化了);reached 由终点收敛决定。
    """
    kw = _load_fixture()["request"]["kwargs"]
    pipe = _fake_from_fixture(converge_on_send=True)
    rt = FakeRT(pipe, arm_id=1)
    plan = robot_api.plan_joint_path(rt, 1, kw["q_goal"], q_current=kw["q_current"],
                                    q_other_arm=kw["q_other_arm"], data=kw["data"])
    res = robot_api.execute_path(rt, plan)
    qmoves = [c for c in pipe.calls if c[1] == "qpos_move"]
    assert all(len(c[2]["qpos"]) == 7 for c in qmoves)
    assert 0 < len(qmoves) <= robot_api.EXEC_MAX_WAYPOINTS
    assert len(qmoves) < plan.n_waypoints            # 该 fixture 有 92 点,必被抽稀
    # 终点原样送到:精度不因抽稀而退化
    assert qmoves[-1][2]["qpos"] == pytest.approx(plan.waypoints[-1])
    assert res.reached is True
    assert res.n_sent == len(qmoves)
    assert res.n_converged == 1                      # 只终点被确认


def test_execute_keeps_endpoint_when_downsampling():
    """抽稀必须保留终点,即使航点数不是抽稀步长的整数倍。"""
    pts = [[float(i)] * 7 for i in range(45)]
    kept = robot_api._downsample(pts, 20)
    assert len(kept) <= 20
    assert kept[0] == pts[0] and kept[-1] == pts[-1]


def test_execute_ctrl_ok_not_treated_as_reached():
    """ctrl 假 ok(fire-and-forget)不被当到位:关节不动时 execute 判 reached=False。"""
    kw = _load_fixture()["request"]["kwargs"]
    # converge_on_send=False:qpos_move 回 True 但关节不动 → get_qpos 永远差得远
    pipe = _fake_from_fixture(converge_on_send=False)
    rt = FakeRT(pipe, arm_id=1)
    plan = robot_api.plan_joint_path(rt, 1, kw["q_goal"], q_current=kw["q_current"],
                                    q_other_arm=kw["q_other_arm"], data=kw["data"])
    # 用末航点(与起始 q_current 有实质差异,maxdev≈0.1rad>tol):关节不动 → 判不到位。
    # (前几个航点恰在起点邻域,不足以区分。)settle_timeout 短以免测试等太久。
    res = robot_api.execute_path(rt, [plan.waypoints[-1]], arm=1, settle_timeout_s=0.4)
    assert res.reached is False
    assert res.n_converged == 0
