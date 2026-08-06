"""lift 的语义是「抬到目标高度」,不是「发够步数」。

背景(8/6 v4 实测):上游控制器每条 `delta_move` 只交付约 **74%** 的指令量,空载
与带载相同(负载无关),且渐近停住。固定步数的开环必然欠冲,所以 `lift` 改成读
EEF 高度算剩余量的闭环。本文件用一个「按比例交付」的桩 pipeline 把这个上游行为
建模出来,验证闭环确实几何收敛、到上限未收敛时如实记账。

风格对齐 tests/test_gates_no_privilege.py:纯逻辑、离线、不触 sim/网络/LLM。
用真 OracleRuntime 走真代码路径(`_ctrl`/`_verify_moved`/记账逻辑全部真跑),
只把会真等待的 `_wait_settle` 桩掉,并把整个"机器人"换成 `_Plant`。
"""

import time

import pytest

from demo_graph_lab.execution import oracle_runtime
from demo_graph_lab.execution.oracle_runtime import (
    LIFT_DZ, LIFT_MAX_ITERS, LIFT_TOL_M, OracleRuntime,
)

# lift 允许调用的 pipeline 函数白名单——全部是非特权 info/ctrl。
# 闭环化不得引入任何特权读取(实体位姿、/state 物体态等)。
NONPRIV_FNS = {"delta_move", "get_xquat", "get_ee_extforce",
               "is_gripping_sth", "get_sensor_info"}


@pytest.fixture(autouse=True)
def _no_creep_wait(monkeypatch):
    """离线测试里把每轮的爬升吸收等待置零(真实值 1.5 s,见 LIFT_CREEP_S)。"""
    monkeypatch.setattr(oracle_runtime, "LIFT_CREEP_S", 0.0)


class _Plant:
    """桩 pipeline = 一台「每条指令只交付 delivery 比例」的机器人。

    这正是 v4 上游控制器的实测行为:发 dz 只走 delivery*dz,走完就停住不再动。
    交付率与负载无关,所以这里也不区分空载/带载。
    """

    def __init__(self, delivery=0.74, z0=0.80, force=0.0):
        self.delivery, self.z, self.force = delivery, z0, force
        self.z0 = z0
        self.cmds = []          # 收到的每条 delta_move 的 dz
        self.fns = []           # 收到的全部函数名(用于非特权白名单断言)

    def call(self, action, fn, kw):
        self.fns.append(fn)
        if fn == "delta_move":
            dz = kw["delta_xyz"][2]
            self.cmds.append(dz)
            self.z += dz * self.delivery
            return {"ok": True}
        if fn == "get_xquat":
            return [0.4, 0.1, self.z, 0.0, 1.0, 0.0, 0.0]
        if fn == "get_ee_extforce":
            return [self.force, 0.0, 0.0]
        if fn == "is_gripping_sth":
            return True
        if fn == "get_sensor_info":
            return [0.0] * 8
        return {"ok": True}


def _rt(plant):
    g = {"stages": [{"index": 0, "name": "lift", "holes": [], "stage_objects": {}}]}
    rt = OracleRuntime(g)
    rt._ents_cache = (time.time() + 1e6, {})     # 特权缓存留空:闭环不该碰它
    rt.pipe = plant
    rt._wait_settle = lambda *a, **kw: "still"   # 唯一的桩:避免真等待
    return rt


def _find(rt, op):
    return [c for c in rt.calls if c["op"] == op]


def test_lift_converges_geometrically_under_74_percent_delivery():
    """每条指令只交付 74% → 剩余量按 0.26^n 几何收敛,应在 8 轮内达容差。"""
    plant = _Plant(delivery=0.74)
    rt = _rt(plant)
    rt.lift("tube")
    rec = _find(rt, "lift_done")[0]
    assert rec["converged"] is True, f"74% 交付率下闭环应收敛,实得 {rec}"
    assert rec["iters"] <= 8, f"应在 8 轮内收敛,实得 {rec['iters']}"
    assert abs(rec["ee_dz"] - LIFT_DZ) <= LIFT_TOL_M, \
        f"收敛后实得高度应落在容差内,实得 ee_dz={rec['ee_dz']}"
    # 首条指令按全部剩余量下发(开环的固定 0.02 步长已不存在)。
    assert abs(plant.cmds[0] - LIFT_DZ) < 1e-6, f"首条指令应是全剩余量,实得 {plant.cmds[0]}"
    # 剩余量逐轮至少缩到上一轮的 0.30 倍(理论 0.26)。
    steps = _find(rt, "lift_step")
    assert len(steps) == rec["iters"], "每轮迭代都要进账本"
    rems = [s["remaining_dz"] for s in steps]
    prev = LIFT_DZ
    for r in rems:
        assert abs(r) <= 0.30 * prev, f"剩余量未几何收敛:{rems}"
        prev = abs(r)


def test_lift_iteration_log_carries_command_and_achieved():
    """每轮账本要能复查「发了多少 / 实得多少 / 还差多少」。"""
    rt = _rt(_Plant(delivery=0.74))
    rt.lift("tube")
    s = _find(rt, "lift_step")[0]
    assert s["i"] == 1
    assert abs(s["cmd_dz"] - LIFT_DZ) < 1e-6
    assert abs(s["achieved_dz"] - 0.74 * LIFT_DZ) < 1e-3, \
        f"首轮实得应约为指令的 74%,实得 {s['achieved_dz']}"
    assert abs(s["remaining_dz"] - (LIFT_DZ - s["achieved_dz"])) < 1e-6


def test_lift_full_delivery_converges_immediately():
    """交付率 100%(理想控制器)→ 1-2 轮即收敛,闭环不额外浪费指令。"""
    rt = _rt(_Plant(delivery=1.0))
    rt.lift("tube")
    rec = _find(rt, "lift_done")[0]
    assert rec["converged"] is True
    assert rec["iters"] <= 2, f"理想交付下应 1-2 轮收敛,实得 {rec['iters']}"


def test_lift_budget_exhausted_reports_honestly():
    """交付率极低 → 到迭代上限仍未达高度:如实记 converged=False + 实得高度,
    既不抛异常也不把「发完了」当成「抬到了」。"""
    plant = _Plant(delivery=0.02)
    rt = _rt(plant)
    rt.lift("tube")
    rec = _find(rt, "lift_done")[0]
    assert rec["converged"] is False, "未达目标高度不得记成收敛"
    assert rec["iters"] == LIFT_MAX_ITERS, f"应用满迭代预算,实得 {rec['iters']}"
    assert rec["target_dz"] == LIFT_DZ
    assert rec["ee_dz"] < LIFT_DZ - LIFT_TOL_M, "实得高度应如实低于目标"
    # 账本按 4 位小数记,允许 0.1 mm 的记账取整。
    assert abs(rec["ee_dz"] - (plant.z - plant.z0)) < 1e-4, "记的必须是回读实得量"


def test_lift_closed_loop_uses_only_nonprivileged_calls():
    """闭环化不得引入特权读取:全程只走 get_xquat / 外力 / 夹持回读 / delta_move。"""
    plant = _Plant(delivery=0.74, force=10.0)
    rt = _rt(plant)
    rt.lift("tube")
    unexpected = set(plant.fns) - NONPRIV_FNS
    assert not unexpected, f"lift 闭环调用了非白名单接口:{unexpected}"
