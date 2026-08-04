"""lift/lower_until 的控制回路不得使用 oracle 停止判据。

GT 防火墙要求特权量不得进入 lift/lower_until 的停止判据。

本文件验证 **方法路径看不见特权位移**:
  - lift 的 attach 判据只吃非特权代理(get_xquat 位移 + get_ee_extforce 残余负载);
     构造「只有 _entities(特权实体态)有位移、get_xquat 无位移」的假 rt,
     断言 lift **不因特权位移判成 attached='likely'**(方法路径盲于特权量,不得靠它成功)。
  - lower_until 在假接触力信号下正确以 contact_force 停(非特权)。
  - stop_kind=predicate(需特权谓词)→ UNSUPPORTED 记账 + 保守停止,**方法路径零 probes()**。

风格对齐 tests/test_contract_params.py:纯逻辑、离线、不触 sim/网络/LLM。
用真 OracleRuntime 走代码路径,只把碰 sim 的底层动作桩掉并记录。
"""

import time

from demo_graph_lab.execution.oracle_runtime import LIFT_LOAD_FORCE_N, OracleRuntime


# --------------------------------------------------------------------------
# 离线 OracleRuntime:
#   - _entities 注入「特权实体态」缓存(gate/evaluator 侧合法数据源);
#   - 方法原语(lift/lower_until)碰 sim 的底层动作全部桩掉;
#   - get_xquat 位移与 get_ee_extforce 力值由参数控制(非特权信号)。
# 关键:_cur_xquat(get_xquat)与 _entities(特权态)**解耦** ——
# 这样才能构造「特权位移有、非特权位移无」的对照,考验方法路径是否偷看特权量。
# --------------------------------------------------------------------------
def _entity(x=0.4, y=0.1, z=0.8, half=0.06):
    return {"pos": [x, y, z], "quat": [1.0, 0.0, 0.0, 0.0],
            "aabb": {"min": [x - half, y - half, z - 0.08],
                     "max": [x + half, y + half, z + 0.08]}}


def _rt(entities=None, force=0.0, ee_z_trajectory=None, probes=None):
    """entities        : 注入的特权实体态(_entities 缓存)。
    force           : get_ee_extforce 返回的标量力(N),lift/lower_until 都读它。
    ee_z_trajectory : _cur_xquat 每次调用返回的 z 序列(非特权 EEF 高度);
                      None → 恒定 z(无位移)。用列表模拟抬升/下探的真实 EEF 轨迹。
    probes          : probes() 返回值(仅用于探测方法路径是否偷调它;应全程为空触碰)。
    """
    g = {"stages": [{"index": 0, "name": "lift", "holes": [], "stage_objects": {}}]}
    rt = OracleRuntime(g)
    ents = entities if entities is not None else {}
    rt._ents_cache = (time.time() + 1e6, ents)

    # 非特权 EEF 高度轨迹:每次 _cur_xquat 取下一个 z(耗尽后停在末值)。
    seq = list(ee_z_trajectory) if ee_z_trajectory is not None else None
    state = {"i": 0}

    def _cur_xquat():
        if seq is None:
            z = 0.9
        else:
            z = seq[min(state["i"], len(seq) - 1)]
            state["i"] += 1
        return [0.4, 0.1, z], [0.0, 1.0, 0.0, 0.0]
    rt._cur_xquat = _cur_xquat

    rt._ctrl = lambda *a, **kw: rt._log("ctrl", fn=a[0] if a else kw.get("fn")) or None
    rt._wait_settle = lambda *a, **kw: "still"
    rt._verify_moved = lambda *a, **kw: (True, 0.0, 0.0)
    rt._park_idle_arm = lambda: None

    # probes 探针:一旦方法路径调用即置真。
    rt._probes_touched = {"v": False}
    _p = probes or []

    def _spy_probes():
        rt._probes_touched["v"] = True
        return _p
    rt.probes = _spy_probes

    class _Pipe:
        def call(self, action, fn, kw):
            if fn == "get_ee_extforce":
                return [float(force), 0.0, 0.0]
            if fn == "get_xquat":
                p, q = _cur_xquat()
                return p + q
            return {"ok": True}
    rt.pipe = _Pipe()
    return rt


def _find(rt, op):
    return [c for c in rt.calls if c["op"] == op]


# ==========================================================================
# 只有 _entities 有位移、get_xquat 无位移的假 runtime。
#    → 方法路径看不见特权位移,lift 不得判成 attached='likely'。
# ==========================================================================
def test_lift_blind_to_privileged_displacement():
    """特权实体态显示物体被抬高很多(_entities 位移大),但非特权 EEF 高度(get_xquat)
    纹丝不动、且无残余负载 → lift 的 attach 判据必须判不出成功('likely')。
    这证明方法路径**没有偷看特权位移**来宣称抓取成功。"""
    # 特权态:物体从 z=0.8 抬到 z=1.0(位移 0.2m,若偷看会误判 attached)。
    ents = {"tube": _entity(z=0.8)}
    rt = _rt(entities=ents, force=0.0, ee_z_trajectory=[0.9, 0.9, 0.9, 0.9, 0.9])
    # 抬升过程中把特权态改成「物体升高」,模拟 evaluator 侧看得到位移。
    rt._ents_cache = (time.time() + 1e6, {"tube": _entity(z=1.0)})
    rt.lift("tube")
    done = _find(rt, "lift_done")
    assert done, "lift 应记 lift_done"
    rec = done[0]
    # 非特权信号:EEF 没上移(z 恒 0.9)+ 无残余负载 → 判不出附着。
    assert rec["attached"] != "likely", \
        f"方法路径不得靠特权位移宣称抓住,实得 attached={rec['attached']!r}"
    # EEF 未上移 → attached=None(UNKNOWN,与 predicates 三值同语义),不 fail-open。
    assert rec["attached"] is None, f"EEF 无位移应记 UNKNOWN(None),实得 {rec['attached']!r}"
    assert rec["reason"] == "ee_did_not_rise"
    # 记账里绝不出现「读了物体位姿」的痕迹:ee_dz 是非特权 EEF 量,不是 obj 位移。
    assert "obj_dz" not in rec, "去特权后不得再记特权 obj_dz"


def test_lift_attached_from_nonprivileged_load():
    """真非特权证据齐(EEF 确有上移 + 残余负载 ≥ 阈值)→ attached='likely'。
    证明判据不是恒 UNKNOWN,而是被非特权信号驱动。"""
    # EEF 从 0.80 一路升到 0.92(上移 0.12m,超过进展容差);力值给足残余负载。
    traj = [0.80] + [0.80 + 0.02 * i for i in range(1, 8)]
    rt = _rt(entities={"tube": _entity()}, force=LIFT_LOAD_FORCE_N + 5.0,
             ee_z_trajectory=traj)
    rt.lift("tube")
    rec = _find(rt, "lift_done")[0]
    assert rec["attached"] == "likely", f"非特权证据齐应判 likely,实得 {rec}"
    assert rec["reason"] == "ee_rose_and_loaded"


def test_lift_empty_when_ee_rose_but_no_load():
    """EEF 确有上移但无残余负载 → attached='empty'(抬了个空),FAIL 侧证据,不 fail-open。"""
    traj = [0.80] + [0.80 + 0.02 * i for i in range(1, 8)]
    rt = _rt(entities={"tube": _entity()}, force=0.5, ee_z_trajectory=traj)
    rt.lift("tube")
    rec = _find(rt, "lift_done")[0]
    assert rec["attached"] == "empty"
    assert rec["reason"] == "ee_rose_no_load"


def test_lift_never_calls_privileged_probes():
    """lift 的控制回路全程不得触碰 privileged probes()。"""
    rt = _rt(entities={"tube": _entity()}, force=10.0)
    rt.lift("tube")
    assert not rt._probes_touched["v"], "lift 方法路径不得调用 probes()"


# ==========================================================================
# lower_until 在非特权接触力信号下正确停。
# ==========================================================================
def test_lower_until_stops_on_fake_contact_force():
    """给高力值(> CONTACT_FORCE_N)→ 应以非特权 contact_force 立即停,不走满预算。"""
    rt = _rt(force=57.0)
    rt.lower_until({"kind": "condition", "purpose": "lower_stop",
                    "stop_kind": "contact"})
    done = _find(rt, "lower_until_done")
    assert done and done[0]["reason"] == "contact_force"
    assert done[0]["steps"] == 1, "首步即触发接触力应立刻停"


def test_lower_until_contact_never_calls_probes():
    """lower_until 走非特权判据时,方法路径不得触碰 probes()。"""
    rt = _rt(force=57.0)
    rt.lower_until({"kind": "condition", "purpose": "lower_stop",
                    "stop_kind": "contact"})
    assert not rt._probes_touched["v"], "lower_until contact 判据不得调 probes()"


# ==========================================================================
# predicate 类(需特权谓词)→ UNSUPPORTED 记账 + 保守停止,零 probes()。
# ==========================================================================
def test_lower_until_predicate_kind_unsupported_no_probes():
    """stop_kind=predicate 没有可用的非特权实现时:
    - 记 unsupported_param(param=lower_until.stop_kind);
    - 退回 contact/plateau(此处高力值 → contact_force 停);
    - **绝不调 probes()**(即便 probes 已满足也不看)。"""
    rt = _rt(force=57.0, probes=[{"label": "root_in_bbox", "passed": True},
                                 {"label": "axis_aligned", "passed": True}])
    rt.lower_until({"kind": "condition", "purpose": "lower_stop",
                    "stop_kind": "predicate"})
    us = [c for c in _find(rt, "unsupported_param")
          if c["param"] == "lower_until.stop_kind"]
    assert us, "predicate 类去特权后应记 UNSUPPORTED"
    assert us[0]["reason"].startswith("privileged_predicate_no_nonpriv_impl")
    done = _find(rt, "lower_until_done")[0]
    assert done["reason"] == "contact_force", "应退回非特权 contact 判据"
    assert done["reason"] != "predicates"
    assert not rt._probes_touched["v"], "非特权方法路径不得调用 probes()"


def test_lower_until_predicate_only_no_contact_goes_to_budget():
    """路由到 predicate 且无接触信号(力低、EEF 恒 z 使 plateau 也可能触发):
    关键是**不因特权谓词停**——无论如何 reason 不得是 'predicates'。"""
    rt = _rt(force=0.5, ee_z_trajectory=[0.9, 0.7, 0.5, 0.3, 0.1],
             probes=[{"label": "root_in_bbox", "passed": True},
                     {"label": "axis_aligned", "passed": True}])
    rt.lower_until({"kind": "condition", "purpose": "lower_stop",
                    "stop_kind": "predicate"})
    done = _find(rt, "lower_until_done")[0]
    assert done["reason"] != "predicates", "去特权后不得以特权谓词停"
    assert not rt._probes_touched["v"]
