"""契约参数必须被消费,或显式记为 UNSUPPORTED。

每条断言都检查 rt.calls(调用账本),防止参数被静默丢弃:
参数要么改变行为(align.axis → 不同末端腕姿;lower_until.stop_condition → 只启用某类判据),
要么被记进 unsupported_param(param/value/reason 三字段可断言)。

approach.cone 必须参与 regions.rank_by_cone 排序并进入账本。

风格对齐 tests/test_solve_dispatch.py:纯逻辑、离线、不触 sim/网络/LLM。
用真 OracleRuntime 走真代码路径,只把碰 sim 的底层动作(_move/_ctrl/_wait_settle/
_cur_xquat/probes/pipe)桩掉并记录——参数消费逻辑本身全部真跑。
"""

import pytest

from demo_graph_lab.execution.oracle_runtime import OracleRuntime
from demo_graph_lab.selection import regions


# --------------------------------------------------------------------------
# 离线 OracleRuntime:注入 oracle 实体缓存,桩掉全部碰 sim 的底层动作。
# align/transport/lower_until 的参数消费逻辑(_consume_obj/_axis_vec/_align_quat/
# _stop_kind)全部真跑;只有会发 HTTP 的 _move/_ctrl/... 被替换成记录桩。
# --------------------------------------------------------------------------
def _entity(x=0.4, y=0.1, z=0.8, half=0.06):
    return {"pos": [x, y, z], "quat": [1.0, 0.0, 0.0, 0.0],
            "aabb": {"min": [x - half, y - half, z - 0.08],
                     "max": [x + half, y + half, z + 0.08]}}


def _offline_rt(entities=None, probes=None, force=None):
    g = {"stages": [{"index": 0, "name": "insert", "holes": [], "stage_objects": {}}]}
    rt = OracleRuntime(g)
    ents = entities if entities is not None else {}
    # 注入实体缓存(_entities 的短 TTL 缓存),让 _ent/_resolve 离线可解析。
    import time
    rt._ents_cache = (time.time() + 1e6, ents)
    # 记录桩:_move 记目标位姿,不发 HTTP。
    rt.moves = []
    rt._move = lambda xyz, quat=None, **kw: rt.moves.append(
        {"xyz": list(xyz), "quat": None if quat is None else list(quat)}) or True
    rt._step_to = lambda *a, **kw: True
    rt._cur_xquat = lambda: ([0.4, 0.1, 0.9], [0.0, 1.0, 0.0, 0.0])
    rt._wait_settle = lambda *a, **kw: "still"
    rt._verify_moved = lambda *a, **kw: (True, 0.0, 0.0)
    rt._park_idle_arm = lambda: None
    rt.probes = lambda: probes or []
    # pipe.call 桩:info/get_ee_extforce 返回可控力值,其余返回 ok。
    force_val = [0.0] if force is None else force

    class _Pipe:
        def call(self, action, fn, kw):
            if fn == "get_ee_extforce":
                return force_val
            return {"ok": True}
    rt.pipe = _Pipe()
    return rt


def _ops(rt):
    return [c["op"] for c in rt.calls]


def _find(rt, op):
    return [c for c in rt.calls if c["op"] == op]


# ==========================================================================
# align.axis:不同 axis → 不同末端腕姿。
# ==========================================================================
def _axis_handle(vec):
    """binding.solve_axis_3d 的句柄形态。"""
    return {"kind": "axis", "hole": "long_axis", "vec": list(vec)}


def test_align_axis_different_axis_gives_different_endpose():
    """align.axis 必须有「不同 axis → 不同末端行为」的断言:
    两个水平投影不同的对齐轴 → 目标腕姿 quat 不同。"""
    rt_x = _offline_rt({"tube": _entity(), "rack": _entity()})
    rt_x.align("tube", "rack", axis=_axis_handle([1.0, 0.0, 0.0]))
    q_x = rt_x.moves[-1]["quat"]

    rt_y = _offline_rt({"tube": _entity(), "rack": _entity()})
    rt_y.align("tube", "rack", axis=_axis_handle([0.0, 1.0, 0.0]))
    q_y = rt_y.moves[-1]["quat"]

    assert q_x is not None and q_y is not None, "带水平分量的 axis 应产出目标腕姿"
    assert q_x != q_y, f"不同 axis 应给不同末端腕姿,实得相同 {q_x}"
    # 且都记了 align_axis(参数被消费的账本证据)。
    assert _find(rt_x, "align_axis") and _find(rt_y, "align_axis")


def test_align_axis_changes_behavior_vs_no_axis():
    """有 axis(水平)时锁定腕姿,与 axis=None(退回竖直姿态,quat=None)行为不同。"""
    rt_none = _offline_rt({"tube": _entity(), "rack": _entity()})
    rt_none.align("tube", "rack", axis=None)
    assert rt_none.moves[-1]["quat"] is None, "无 axis 应使用 _move(quat=None) 的竖直姿态"
    assert not _find(rt_none, "align_axis")
    # axis=None 不是「读到用不上」,是压根没传 → 不记 UNSUPPORTED。
    assert not _find(rt_none, "unsupported_param")

    rt_ax = _offline_rt({"tube": _entity(), "rack": _entity()})
    rt_ax.align("tube", "rack", axis=_axis_handle([1.0, 0.0, 0.0]))
    assert rt_ax.moves[-1]["quat"] is not None, "有水平 axis 应锁定腕姿"


def test_align_axis_vertical_is_unsupported_for_yaw():
    """近竖直的 axis 定不出 yaw 自由度 → UNSUPPORTED 记账,退回竖直姿态(quat=None)。"""
    rt = _offline_rt({"tube": _entity(), "rack": _entity()})
    rt.align("tube", "rack", axis=_axis_handle([0.0, 0.0, 1.0]))
    assert rt.moves[-1]["quat"] is None
    us = _find(rt, "unsupported_param")
    assert us, "竖直 axis 应记 UNSUPPORTED"
    rec = us[0]
    assert rec["param"] == "align.axis"
    assert rec["reason"] == "axis_vertical_yaw_unconstrained"


# ==========================================================================
# align.obj / transport.obj:参照物按参数解析并记账;解析不到 → UNSUPPORTED。
# ==========================================================================
def test_align_obj_resolved_is_logged():
    rt = _offline_rt({"tube": _entity(x=0.2), "rack": _entity(x=0.5)})
    rt.align("tube", "rack", axis=None)
    got = _find(rt, "obj_resolved")
    assert got, "align.obj 解析成功应记 obj_resolved(不再静默忽略)"
    assert got[0]["prim"] == "align" and got[0]["obj"] == "tube"


def test_transport_obj_resolved_is_logged():
    rt = _offline_rt({"coin": _entity(x=0.3), "slot": _entity(x=0.6)})
    rt.transport("coin", "slot")
    got = _find(rt, "obj_resolved")
    assert got and got[0]["prim"] == "transport" and got[0]["obj"] == "coin"


def test_transport_obj_unresolvable_is_unsupported():
    """obj 解析不到(实体表里没有、且无同义词命中)→ UNSUPPORTED 记账,不静默。"""
    rt = _offline_rt({})  # 空实体表 → _resolve 抛 KeyError
    rt.transport("nonexistent_widget_xyz", {"xyz": [0.5, 0.1, 0.8]})
    us = [c for c in _find(rt, "unsupported_param") if c["param"] == "transport.obj"]
    assert us, "解析不到的 transport.obj 应记 UNSUPPORTED"
    assert us[0]["reason"].startswith("unresolved:")


def test_align_obj_unresolvable_is_unsupported():
    rt = _offline_rt({})
    # target 用字面 xyz,避免 target 解析先抛;只考验 obj 解析。
    rt.align("ghost_obj_zzz", {"xyz": [0.5, 0.1, 0.8]}, axis=None)
    us = [c for c in _find(rt, "unsupported_param") if c["param"] == "align.obj"]
    assert us, "解析不到的 align.obj 应记 UNSUPPORTED"


# ==========================================================================
# lower_until.stop_condition:消费参数选择停止判据类别。
#    contact/plateau 是非特权判据;predicate 没有非特权实现,
#    因此记为 UNSUPPORTED 并退回 contact/plateau。更深入的防火墙断言
#    见 tests/test_gates_no_privilege.py。
# ==========================================================================
def _passing_probes():
    return [{"label": "root_in_bbox", "passed": True},
            {"label": "axis_aligned", "passed": True}]


def test_lower_until_routes_to_contact_kind():
    """stop_kind=contact → 只启用接触力判据。给高力值,应以 contact_force 停。"""
    rt = _offline_rt(probes=_passing_probes(), force=[57.0])
    rt.lower_until({"kind": "condition", "stop_kind": "contact"})
    assert _find(rt, "lower_stop_route")[0]["stop_kind"] == "contact"
    done = _find(rt, "lower_until_done")
    assert done and done[0]["reason"] == "contact_force"


def test_lower_until_contact_kind_ignores_predicates():
    """路由到 contact 时,即便谓词已满足也不因谓词停(证明只启用了 contact 一类)。
    力值低于阈值 + 无高度停 → 走到预算,而不是被 predicates 提前停。"""
    rt = _offline_rt(probes=_passing_probes(), force=[0.5])
    # _cur_xquat 恒定 z → plateau 也可能触发;这里把 plateau 也排除(只留 contact)。
    rt.lower_until({"kind": "condition", "stop_kind": "contact"})
    done = _find(rt, "lower_until_done")[0]
    assert done["reason"] != "predicates", "路由到 contact 不应因谓词停"


def test_lower_until_predicate_kind_is_unsupported_and_falls_back():
    """predicate 停止需要特权谓词,无非特权实现时必须记账并退回。

    控制路径不得调用 probes();高力值应由非特权 contact_force 判据停止。
    """
    called = {"probes": False}

    rt = _offline_rt(probes=_passing_probes(), force=[57.0])
    # 显式探针:一旦方法路径还调 probes() 立即置真,断言其未被触碰。
    _orig = rt.probes
    def _spy():
        called["probes"] = True
        return _orig()
    rt.probes = _spy

    rt.lower_until({"kind": "condition", "stop_kind": "predicate"})
    assert _find(rt, "lower_stop_route")[0]["stop_kind"] == "predicate"
    us = [c for c in _find(rt, "unsupported_param")
          if c["param"] == "lower_until.stop_kind"]
    assert us, "predicate 类去特权后应记 UNSUPPORTED"
    assert us[0]["reason"].startswith("privileged_predicate_no_nonpriv_impl")
    done = _find(rt, "lower_until_done")[0]
    assert done["reason"] != "predicates", "去特权后不得以特权谓词停"
    assert done["reason"] == "contact_force", "应退回非特权 contact 判据停"
    assert not called["probes"], "方法路径控制回路不得调用 probes()"


def test_lower_until_no_stop_kind_is_unsupported_and_uses_safe_fallback():
    """缺少显式 stop_kind 时必须记为 UNSUPPORTED,并启用全部默认判据。"""
    rt = _offline_rt(probes=_passing_probes(), force=[57.0])
    rt.lower_until({"kind": "condition", "hole": "seated_condition"})
    us = [c for c in _find(rt, "unsupported_param")
          if c["param"] == "lower_until.stop_condition"]
    assert us, "无 stop_kind 应记 UNSUPPORTED"
    assert "keep_all_criteria" in us[0]["reason"]
    # 三判据全开:高力值应以 contact_force 停止。
    assert _find(rt, "lower_until_done")[0]["reason"] == "contact_force"


def test_lower_until_string_condition_is_unsupported():
    """裸字符串条件(如 policy 里的 `seated`)无 stop_kind → UNSUPPORTED,不静默。"""
    rt = _offline_rt(probes=[], force=[0.0])
    rt.lower_until("seated")
    us = [c for c in _find(rt, "unsupported_param")
          if c["param"] == "lower_until.stop_condition"]
    assert us


# ==========================================================================
# approach.cone:参与 regions.rank_by_cone 排序并进入账本。
# ==========================================================================
def test_approach_cone_is_consumed():
    """cone 参与排序,并记入 approach_cone。"""
    rt = _offline_rt({"bowl": _entity()})
    rt.approach("bowl", cone="side")
    got = _find(rt, "approach_cone")
    assert got, "approach.cone 应参与排序并被记账"
    assert got[0]["cone"] == "side"


def test_approach_cone_ranking_is_direction_aware():
    """rank_by_cone 对不同 cone 给出不同 top-1 方向。"""
    cands = list(OracleRuntime._APPROACH_DIR_CANDIDATES)
    top_down = regions.rank_by_cone(cands, "top_down")[0]["id"]
    side = regions.rank_by_cone(cands, "side")[0]["id"]
    assert top_down != side, "不同 cone 应偏好不同 approach 方向"
