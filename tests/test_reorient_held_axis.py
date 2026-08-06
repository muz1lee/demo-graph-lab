"""`reorient_held_axis` 的桩 pipeline 行为测与非特权纪律。

这条原语的契约由 backend 模型在 2026-08-06 的受控提案实验里提出、人类评审修订后
admit(出处见 `docs/DEVLOG.md` 同日条目),所以它的判据要比照 `lift` 被同样钉住:
正常收敛、未持有拒、无进展停、近平行短路,四条路径各一个用例,外加一条「方法路径
不碰特权量」。

风格对齐 tests/test_contract_params.py 与 tests/test_gates_no_privilege.py:纯逻辑、
离线、不触 sim/网络/LLM。用真 OracleRuntime 走真代码路径,只把发 HTTP 的底层动作
换成桩——旋转数学、闭环判据和持物证据本身全部真跑。
"""

import math

import pytest

from demo_graph_lab.execution import oracle_runtime
from demo_graph_lab.execution.oracle_runtime import (
    GRIP_ANGLE_TOL_DEG,
    GRIP_CLOSE,
    GRIP_OPEN,
    LIFT_LOAD_FORCE_N,
    REORIENT_MAX_STEPS,
    REORIENT_TOL_DEG,
    SERVO_PATIENCE,
    OracleRuntime,
    _qang,
    _qslerp,
    _tool_axes,
)

# 「夹住带」正中间的角度:两端各留 GRIP_ANGLE_TOL_DEG 之后仍稳稳在带内。
HELD_ANGLE = (GRIP_CLOSE + GRIP_OPEN) / 2.0
HELD_FORCE = LIFT_LOAD_FORCE_N * 2.0
# 上游控制器单条指令的交付率(8/6 v4 实测约 74%,见 `lift` 的注释)。默认按它建模,
# 这样闭环用例考的是真实的欠冲情形,而不是理想执行。
DELIVERY = 0.74


def _axis(vec):
    """binding.solve_axis_3d 的句柄形态。"""
    return {"kind": "axis", "hole": "axis", "vec": list(vec)}


def _rt(*, grip_angle=HELD_ANGLE, force=HELD_FORCE, gripping=True,
        delivery=DELIVERY, quat=(0.0, 1.0, 0.0, 0.0)):
    """离线 OracleRuntime。

    grip_angle / force : 非特权持物证据的两个信号;``None`` 表示回读不可用。
    gripping           : ``is_gripping_sth`` 的回读(只应进账本,不应参与判定)。
    delivery           : 每条 ``xquat_move`` 实际交付的比例(1.0=理想,0.0=完全不动),
                         腕姿按 slerp 朝指令值走这么多。
    quat               : 初始腕姿(xyzw);默认 Ry(180),工具 +z 朝下。
    """
    graph = {"stages": [{"index": 0, "name": "reorient",
                         "holes": [], "stage_objects": {}}]}
    rt = OracleRuntime(graph)
    state = {"p": [0.4, 0.1, 0.9], "q": list(quat)}

    rt._cur_xquat = lambda: (list(state["p"]), list(state["q"]))
    rt._wait_settle = lambda *a, **kw: "still"
    rt._park_idle_arm = lambda: None

    def _ctrl(fn, arm_id=None, **kw):
        rt._log("ctrl", fn=fn, **{k: v for k, v in kw.items()
                                  if k != "target_quat"})
        if fn == "xquat_move":
            state["p"] = list(kw["target_xyz"])
            state["q"] = _qslerp(state["q"], list(kw["target_quat"]), delivery)
        return {"ok": True}
    rt._ctrl = _ctrl

    # 特权探针:方法路径一旦触碰即置真。
    rt._touched = {"probes": False, "entities": False}

    def _spy_probes():
        rt._touched["probes"] = True
        return []
    rt.probes = _spy_probes

    def _spy_entities(*a, **kw):
        rt._touched["entities"] = True
        return {}
    rt._entities = _spy_entities

    class _Pipe:
        def call(self, action, fn, kw):
            if fn == "get_ee_extforce":
                if force is None:
                    raise RuntimeError("force channel down")
                return [float(force), 0.0, 0.0]
            if fn == "get_sensor_info":
                if grip_angle is None:
                    raise RuntimeError("gripper angle channel down")
                return [0.0] * 7 + [float(grip_angle)]
            if fn == "is_gripping_sth":
                return gripping
            if fn == "get_xquat":
                return state["p"] + state["q"]
            return {"ok": True}
    rt.pipe = _Pipe()
    return rt


def _find(rt, op):
    return [c for c in rt.calls if c["op"] == op]


def _moves(rt):
    return [c for c in rt.calls if c["op"] == "ctrl" and c["fn"] == "xquat_move"]


def _finger_axis(rt):
    """当前腕姿的工具开合轴(世界系单位向量)——被夹物长轴随它一起转。"""
    return _tool_axes(rt._cur_xquat()[1])[oracle_runtime.FINGER_AXIS_IDX]


def _angle_deg(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(dot)))))


# ==========================================================================
# 正常路径:闭环收敛,且长轴真的转到了目标方向。
# ==========================================================================
def test_reorient_converges_and_actually_parallelises_the_axis():
    """把「当前长轴」转到「目标方向」:收敛退出,残差 ≤ 容差。

    断言不止看账本上的 converged 字段,还独立算一遍工具开合轴与目标方向的夹角——
    被夹物随腕部刚性转动,这个夹角就是后置条件 `object_axis ∥ target_direction`
    在非特权侧唯一量得到的东西。
    """
    rt = _rt()
    obj_axis = _finger_axis(rt)              # 物体当前长轴 = 开合轴方向
    target = [0.0, 0.0, 1.0]
    before = _angle_deg(obj_axis, target)
    assert before > REORIENT_TOL_DEG, "用例前提:初始必须没对齐,否则考不到闭环"

    rt.reorient_held_axis("tube", _axis(obj_axis), _axis(target))

    done = _find(rt, "reorient_done")
    assert len(done) == 1
    rec = done[0]
    assert rec["reason"] == "reached"
    assert rec["converged"] is True
    assert rec["rot_gap_deg"] <= REORIENT_TOL_DEG
    assert rec["iters"] >= 1, "74% 交付率下一步到不了位,必须是闭环多步"
    assert _find(rt, "reorient_step"), "每步都要进账本"
    # 独立复核后置条件:长轴与目标方向确实平行了。
    assert _angle_deg(_finger_axis(rt), target) <= REORIENT_TOL_DEG


def test_reorient_does_not_translate_the_wrist():
    """契约写明「不平移抓取点」:每条指令的 target_xyz 都等于当轮回读位置。"""
    rt = _rt()
    start = rt._cur_xquat()[0]
    rt.reorient_held_axis("tube", _axis(_finger_axis(rt)), _axis([0.0, 0.0, 1.0]))

    moves = _moves(rt)
    assert moves, "用例前提:确实发了旋转指令"
    for move in moves:
        assert move["target_xyz"] == [round(c, 4) for c in start]
    assert rt._cur_xquat()[0] == [round(c, 4) for c in start]
    # 携物旋转全程保持夹持:每条指令都带闭合夹爪位。
    assert all(move["gpos"] == oracle_runtime.GRIP_CLOSE_TUBE for move in moves)


def test_reorient_takes_the_short_way_round_for_an_undirected_axis():
    """长轴是无向直线:目标方向取反时应走短程(≈0°),不为对齐同一条线转 180°。"""
    rt = _rt()
    obj_axis = _finger_axis(rt)
    rt.reorient_held_axis("tube", _axis(obj_axis),
                          _axis([-c for c in obj_axis]))

    rec = _find(rt, "reorient_done")[0]
    assert rec["flipped_target"] is True
    assert rec["reason"] == "already_aligned"
    assert not _moves(rt), "反向平行等于已经平行,不该发任何指令"


# ==========================================================================
# 拒绝路径 ①:轴句柄缺失/退化。
# ==========================================================================
@pytest.mark.parametrize(("object_axis", "target_direction", "param"), [
    (None, _axis([0.0, 0.0, 1.0]), "object_axis"),
    (_axis([1.0, 0.0, 0.0]), None, "target_direction"),
    (_axis([0.0, 0.0, 0.0]), _axis([0.0, 0.0, 1.0]), "object_axis"),
    (_axis([1.0, 0.0, 0.0]), "insertion", "target_direction"),
])
def test_reorient_refuses_a_missing_or_degenerate_axis(
    object_axis, target_direction, param,
):
    rt = _rt()
    rt.reorient_held_axis("tube", object_axis, target_direction)

    unsupported = _find(rt, "unsupported_param")
    assert unsupported, "取不到轴向量必须记 UNSUPPORTED,不能静默"
    assert unsupported[0]["param"] == f"reorient_held_axis.{param}"
    assert unsupported[0]["reason"] == "no_axis_vec"
    refused = _find(rt, "reorient_refused")
    assert refused and refused[0]["reason"] == f"no_axis_vec:{param}"
    assert not _moves(rt)
    assert not _find(rt, "reorient_done")


# ==========================================================================
# 拒绝路径 ②:非特权持物证据。
# ==========================================================================
@pytest.mark.parametrize(("kwargs", "evidence"), [
    # 爪子全闭到位 = 手上什么都没夹住。
    ({"grip_angle": GRIP_CLOSE}, "gripper_not_in_holding_band"),
    # 爪子还在全开位。
    ({"grip_angle": GRIP_OPEN}, "gripper_not_in_holding_band"),
    # 带内但空载:夹住了空气。
    ({"force": LIFT_LOAD_FORCE_N / 10.0}, "no_sustained_load"),
])
def test_reorient_refuses_when_the_object_is_not_held(kwargs, evidence):
    rt = _rt(**kwargs)
    rt.reorient_held_axis("tube", _axis([1.0, 0.0, 0.0]), _axis([0.0, 0.0, 1.0]))

    refused = _find(rt, "reorient_refused")
    assert refused, "未持有必须拒"
    assert refused[0]["reason"] == "not_holding"
    assert refused[0]["evidence"] == evidence
    assert not _moves(rt)
    assert not _find(rt, "reorient_done")


@pytest.mark.parametrize("kwargs", [{"grip_angle": None}, {"force": None}])
def test_reorient_refuses_when_hold_evidence_is_unreadable(kwargs):
    """读不到证据 ≠ 证据成立:一样拒,不 fail-open。"""
    rt = _rt(**kwargs)
    rt.reorient_held_axis("tube", _axis([1.0, 0.0, 0.0]), _axis([0.0, 0.0, 1.0]))

    refused = _find(rt, "reorient_refused")
    assert refused and refused[0]["reason"] == "hold_unreadable"
    assert refused[0]["evidence"] == "grip_or_force_unreadable"
    assert not _moves(rt)


def test_is_gripping_alone_cannot_authorise_the_rotation():
    """`is_gripping` 只是停转信号:它为真、但角度带外时仍然拒;
    它为假、而带内+有载时照样放行。"""
    blocked = _rt(grip_angle=GRIP_CLOSE, gripping=True)
    blocked.reorient_held_axis("tube", _axis([1.0, 0.0, 0.0]),
                               _axis([0.0, 0.0, 1.0]))
    assert _find(blocked, "reorient_refused")

    allowed = _rt(gripping=False)
    axis = _finger_axis(allowed)
    allowed.reorient_held_axis("tube", _axis(axis), _axis([0.0, 0.0, 1.0]))
    assert not _find(allowed, "reorient_refused")
    assert _find(allowed, "reorient_done")[0]["gripping"] is False


def test_hold_band_edges_are_the_readout_tolerance():
    """夹住带的两端各收一个回读容差;边界值本身在带外,带内一点点即放行。"""
    edge = _rt(grip_angle=GRIP_CLOSE + GRIP_ANGLE_TOL_DEG)
    edge.reorient_held_axis("tube", _axis([1.0, 0.0, 0.0]), _axis([0.0, 0.0, 1.0]))
    assert _find(edge, "reorient_refused")

    inside = _rt(grip_angle=GRIP_CLOSE + GRIP_ANGLE_TOL_DEG + 0.1)
    inside.reorient_held_axis("tube", _axis(_finger_axis(inside)),
                              _axis([0.0, 0.0, 1.0]))
    assert not _find(inside, "reorient_refused")


# ==========================================================================
# 拒绝路径 ③:旋转不可达 —— 无进展就停,不硬转。
# ==========================================================================
def test_reorient_stops_when_the_rotation_makes_no_progress():
    """指令发出去但腕姿纹丝不动(ep2/ep3 的 18° 残差是这类现实):
    连续 SERVO_PATIENCE 轮无进展即停,如实记 converged=False,不走满预算。"""
    rt = _rt(delivery=0.0)
    rt.reorient_held_axis("tube", _axis(_finger_axis(rt)), _axis([0.0, 0.0, 1.0]))

    rec = _find(rt, "reorient_done")[0]
    assert rec["reason"] == "no_rotation_progress"
    assert rec["converged"] is False
    assert rec["iters"] == SERVO_PATIENCE, "无进展应尽早停,不耗满预算"
    assert rec["iters"] < REORIENT_MAX_STEPS
    assert rec["rot_gap_deg"] > REORIENT_TOL_DEG, "残差要如实记,不能报成 0"


def test_reorient_partial_delivery_is_reported_not_rounded_up():
    """转得动、但转不完(每轮只交付一点点):走满预算后如实记 budget + 未收敛。"""
    rt = _rt(delivery=0.02)
    rt.reorient_held_axis("tube", _axis(_finger_axis(rt)), _axis([0.0, 0.0, 1.0]))

    rec = _find(rt, "reorient_done")[0]
    assert rec["reason"] == "budget"
    assert rec["converged"] is False
    assert rec["iters"] == REORIENT_MAX_STEPS


# ==========================================================================
# 短路路径:两轴已近平行 → 恒等旋转。
# ==========================================================================
def test_reorient_already_aligned_is_a_success_without_any_command():
    rt = _rt()
    axis = _finger_axis(rt)
    q_before = rt._cur_xquat()[1]

    rt.reorient_held_axis("tube", _axis(axis), _axis(axis))

    rec = _find(rt, "reorient_done")[0]
    assert rec["reason"] == "already_aligned"
    assert rec["converged"] is True
    assert rec["iters"] == 0
    assert rec["flipped_target"] is False
    assert not _moves(rt)
    assert _qang(rt._cur_xquat()[1], q_before) == pytest.approx(0.0, abs=1e-6)


# ==========================================================================
# 非特权纪律:方法路径不得越白名单。
# ==========================================================================
def test_reorient_never_touches_privileged_state():
    """整条路径只读 get_xquat / get_ee_extforce / get_sensor_info / is_gripping_sth,
    不碰 probes() 也不碰特权实体态。"""
    rt = _rt()
    rt.reorient_held_axis("tube", _axis(_finger_axis(rt)), _axis([0.0, 0.0, 1.0]))

    assert not rt._touched["probes"], "reorient 不得调 probes()"
    assert not rt._touched["entities"], "reorient 不得读特权实体态"


def test_reorient_only_calls_whitelisted_pipeline_reads():
    allowed_reads = {"get_xquat", "get_ee_extforce", "get_sensor_info",
                     "is_gripping_sth"}
    seen = []

    rt = _rt()

    class _Recording:
        def __init__(self, inner):
            self._inner = inner

        def call(self, action, fn, kw):
            seen.append((action, fn))
            return self._inner.call(action, fn, kw)
    rt.pipe = _Recording(rt.pipe)

    rt.reorient_held_axis("tube", _axis(_finger_axis(rt)), _axis([0.0, 0.0, 1.0]))

    assert seen, "用例前提:确实读了非特权信号"
    for action, fn in seen:
        assert action == "info" and fn in allowed_reads, (action, fn)
