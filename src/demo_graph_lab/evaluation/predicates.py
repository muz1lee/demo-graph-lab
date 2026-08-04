"""[runtime · predicates] 将封闭词表中的 10 种约束映射为三值几何检验。

未覆盖谓词、异常和不可观测都不能静默当作成功。本模块把「检查不了」和
「检查通过」在类型上分开:

    PASS    = 谓词成立(margin ≥ 0,数值是到边界的余量)
    FAIL    = 谓词违反(margin < 0,数值是越界的幅度)
    UNKNOWN = 检查不了(缺参照实体 / 缺输入 / 本 runtime 无法几何判)

`UNKNOWN` 永远不等于 PASS，也不等于 FAIL。调用方必须记录 UNKNOWN，
但不得静默放行或静默否决。`vacuous_pass`（入口即为真的成立不带新证据）
由 gate 侧处理，本模块不涉及。

可几何检验(8):axis_vertical / axis_parallel / center_align / above / inside /
              region_grasp / approach_direction / clearance
本 runtime 不可检验(2，显式 UNCHECKABLE_IN_RUNTIME 并计入报告):
  carry  —— 需夹爪-物体附着这个跨阶段状态量，几何快照读不出。
  order  —— 需跨阶段执行序，单帧实体快照无此信息。

本模块只吃 `entities` 快照(`{name: {"pos","quat","aabb"}}`，与 binding/regions 同一套)，
不持有 runtime、不发网络、运行期零 LLM。`region_grasp` 复用 `binding` 的归一化
竖直带；`approach_direction` 复用 `regions.cone_angle_deg`。阈值是任务无关的几何容差，
不是场景坐标。
"""

from __future__ import annotations

import math

from ..graph import vocab
from ..selection import binding, regions

# ---- 三值 ----
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

# 本 runtime 结构上无法检验的约束（几何快照读不出的跨阶段状态）。
UNCHECKABLE_IN_RUNTIME = {"carry", "order"}

# ---- 几何容差(非场景坐标,是判定的角度/距离容差常量)----
_ANGLE_TOL_DEG = 20.0        # 轴竖直/平行的容差
_ALIGN_TOL_M = 0.05          # center_align 的 xy 容差
_INSIDE_PAD_M = 0.02         # inside 的 AABB 外扩容差
_CONE_TOL_DEG = 25.0         # approach 方向落在 cone 内的容差


class Predicate:
    """三值判定结果。

    status : PASS / FAIL / UNKNOWN。
    margin : 有符号余量(status!=UNKNOWN 时有意义):
             ≥0 = 满足且到边界的余量;<0 = 违反且越界幅度。UNKNOWN 时为 None。
    detail : 人读诊断串(记账用,不参与判定)。
    reason : UNKNOWN 的机器可读原因(如 "uncheckable_in_runtime" / "ref_unresolved")。
    name   : 约束名。
    """

    __slots__ = ("name", "status", "margin", "detail", "reason")

    def __init__(self, name, status, margin=None, detail="", reason=None):
        self.name = name
        self.status = status
        self.margin = margin
        self.detail = detail
        self.reason = reason

    @property
    def ok(self):
        """PASS→True,FAIL→False,UNKNOWN→None(三值,调用方须区分 None 与 False)。"""
        if self.status == PASS:
            return True
        if self.status == FAIL:
            return False
        return None

    def __repr__(self):
        m = "None" if self.margin is None else f"{self.margin:+.4f}"
        return f"<{self.name} {self.status} margin={m} {self.detail}>"


def _unknown(name, reason, detail=""):
    return Predicate(name, UNKNOWN, margin=None, detail=detail or reason, reason=reason)


def _from_margin(name, margin, detail=""):
    """margin ≥ 0 → PASS,< 0 → FAIL。margin 的符号即判定,大小即余量/越界幅度。"""
    return Predicate(name, PASS if margin >= 0 else FAIL, margin=margin, detail=detail)


# ---------- 实体/参数取值(纯,容 None) ----------
def _args(c):
    return (c or {}).get("args", {}) or {}


def _obj_base(v):
    """把 "<obj>.<attr>" / "<obj>" 归一到实体名 base(第一个 '.' 前)。"""
    return str(v).split(".")[0] if v is not None else None


def _ent(entities, name):
    if entities is None or name is None:
        return None
    return entities.get(name)


def _quat_z_axis(q):
    """物体局部 +z 在世界系的方向(wxyz)。"""
    w, x, y, z = q
    return [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]


def _angle_deg(a, b):
    na = math.sqrt(sum(v * v for v in a)) or 1e-9
    nb = math.sqrt(sum(v * v for v in b)) or 1e-9
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))
    return math.degrees(math.acos(dot))


# ==========================================================================
# 8 个可几何检验的谓词。签名统一 (constraint, entities, **ctx) → Predicate。
# ctx 里可带 grasp_point(region_grasp 用)、approach_dir(approach_direction 用)。
# ==========================================================================
def pred_axis_vertical(c, entities, **ctx):
    a = _args(c)
    obj = _obj_base(a.get("axis") or next(iter(a.values()), None))
    ent = _ent(entities, obj)
    if ent is None or "quat" not in ent:
        return _unknown("axis_vertical", "ref_unresolved", f"obj={obj}")
    ang = _angle_deg(_quat_z_axis(ent["quat"]), [0, 0, 1])
    ang = min(ang, 180 - ang)                       # 轴无向:取到竖直的最小夹角
    return _from_margin("axis_vertical", _ANGLE_TOL_DEG - ang, f"angle={ang:.1f}")


def pred_axis_parallel(c, entities, **ctx):
    a = _args(c)
    va = _obj_base(a.get("axis_a"))
    vb = _obj_base(a.get("axis_b"))
    ea, eb = _ent(entities, va), _ent(entities, vb)
    if ea is None or "quat" not in ea:
        return _unknown("axis_parallel", "ref_unresolved", f"axis_a={va}")
    if eb is None or "quat" not in eb:
        return _unknown("axis_parallel", "ref_unresolved", f"axis_b={vb}")
    axa = _quat_z_axis(ea["quat"])
    axb = _quat_z_axis(eb["quat"])
    ang = _angle_deg(axa, axb)
    ang = min(ang, 180 - ang)
    return _from_margin("axis_parallel", _ANGLE_TOL_DEG - ang, f"angle={ang:.1f}")


def pred_center_align(c, entities, **ctx):
    a = _args(c)
    ea, eb = _ent(entities, _obj_base(a.get("obj_a"))), _ent(entities, _obj_base(a.get("obj_b")))
    if ea is None or eb is None:
        return _unknown("center_align", "ref_unresolved")
    d = math.dist(ea["pos"][:2], eb["pos"][:2])
    return _from_margin("center_align", _ALIGN_TOL_M - d, f"xy_dist={d:.3f}")


def pred_above(c, entities, **ctx):
    a = _args(c)
    ea, eb = _ent(entities, _obj_base(a.get("obj_a"))), _ent(entities, _obj_base(a.get("obj_b")))
    if ea is None or eb is None:
        return _unknown("above", "ref_unresolved")
    dz = ea["pos"][2] - eb["pos"][2]                # >0 = a 在 b 之上,幅度即高差
    return _from_margin("above", dz, f"dz={dz:.3f}")


def pred_inside(c, entities, **ctx):
    a = _args(c)
    ea, eb = _ent(entities, _obj_base(a.get("obj_a"))), _ent(entities, _obj_base(a.get("obj_b")))
    if ea is None or eb is None:
        return _unknown("inside", "ref_unresolved")
    try:
        lo, hi = binding._aabb_bounds(eb)
    except Exception:
        return _unknown("inside", "no_aabb", f"obj_b={_obj_base(a.get('obj_b'))}")
    p = ea["pos"]
    # margin = 到最近侧壁(含 pad)的带符号距离在 xy 两轴取最小:全在框内为正,越界为负。
    mx = min((p[0] - (lo[0] - _INSIDE_PAD_M)), ((hi[0] + _INSIDE_PAD_M) - p[0]))
    my = min((p[1] - (lo[1] - _INSIDE_PAD_M)), ((hi[1] + _INSIDE_PAD_M) - p[1]))
    margin = min(mx, my)
    return _from_margin("inside", margin, f"xy_margin={margin:.3f}")


def pred_clearance(c, entities, **ctx):
    """两物体 AABB 之间的最小间隙 ≥ 0(不相交即成立)。margin = 间隙(负=重叠深度)。

    完整无碰需三角网格,本 runtime 只有 AABB → 这是保守近似:AABB 不相交则一定无碰,
    AABB 相交只说明**包围盒**重叠(可能仍无碰),故 FAIL 偏严。显式记 detail,不夸大为精确。
    """
    a = _args(c)
    ea, eb = _ent(entities, _obj_base(a.get("obj_a"))), _ent(entities, _obj_base(a.get("obj_b")))
    if ea is None or eb is None:
        return _unknown("clearance", "ref_unresolved")
    try:
        alo, ahi = binding._aabb_bounds(ea)
        blo, bhi = binding._aabb_bounds(eb)
    except Exception:
        return _unknown("clearance", "no_aabb")
    # 每轴间隙:正=分离距离,负=重叠量。三轴取最大(只要一轴分离即整体分离 → 间隙=该正值)。
    gaps = [max(blo[i] - ahi[i], alo[i] - bhi[i]) for i in range(3)]
    gap = max(gaps)
    return _from_margin("clearance", gap, f"aabb_gap={gap:.3f}(box_approx)")


def pred_region_grasp(c, entities, *, grasp_point=None, **ctx):
    """抓取点是否落在物体归一化竖直带的偏好侧。**复用** binding 的区带规则 + regions 的偏好函数。

    需要 grasp_point(世界系抓取点 [x,y,z]);gate/verify 快照里没有抓取点时 → UNKNOWN
    （这是「输入缺失」，不是「不可检查」：本谓词已有完整几何实现）。
    rim/handle 走 binding/regions 的 UNCHECKABLE 集（当前无几何特征检测器）→ UNKNOWN。
    """
    a = _args(c)
    obj = _obj_base(a.get("obj"))
    region = a.get("region")
    ent = _ent(entities, obj)
    if ent is None:
        return _unknown("region_grasp", "ref_unresolved", f"obj={obj}")
    if grasp_point is None:
        return _unknown("region_grasp", "no_grasp_point", f"region={region}")
    if region in regions._REGION_UNCHECKABLE:       # rim/handle,与 binding 同集
        return _unknown("region_grasp", "region_uncheckable", f"region={region}")
    try:
        lo, hi = binding._aabb_bounds(ent)
    except Exception:
        return _unknown("region_grasp", "no_aabb", f"obj={obj}")
    span = hi[2] - lo[2]
    if span == 0:
        return _unknown("region_grasp", "degenerate_extent", f"obj={obj}")
    s = (grasp_point[2] - lo[2]) / span             # 归一化竖直坐标 s∈[0,1](全边长)
    try:
        pref = regions.region_preference(region)     # 未知 region → ValueError
    except ValueError:
        return _unknown("region_grasp", "unknown_region", f"region={region}")
    if pref is regions.UNCHECKABLE:
        return _unknown("region_grasp", "region_uncheckable", f"region={region}")
    score = pref(max(0.0, min(1.0, s)))              # 偏好分∈[0,1](越大越贴合)
    # margin = 偏好分 − 0.5:分高于中位判成立,幅度即偏好强度。任务无关、纯几何。
    margin = score - 0.5
    return _from_margin("region_grasp", margin, f"s={s:.2f} score={score:.2f} region={region}")


def pred_approach_direction(c, entities, *, approach_dir=None, **ctx):
    """approach 方向与 cone 目标倾角的误差 ≤ 容差。**复用** regions.cone_angle_deg。

    需要 approach_dir(该次接近的方向向量);快照里没有时 → UNKNOWN(输入缺失,非不可检查)。
    """
    a = _args(c)
    cone = a.get("cone")
    if approach_dir is None:
        return _unknown("approach_direction", "no_approach_dir", f"cone={cone}")
    try:
        ang = regions.cone_angle_deg(approach_dir, cone)   # 未知 cone → ValueError
    except ValueError:
        return _unknown("approach_direction", "unknown_cone", f"cone={cone}")
    if ang is None:
        return _unknown("approach_direction", "zero_vector", f"cone={cone}")
    return _from_margin("approach_direction", _CONE_TOL_DEG - ang, f"angle={ang:.1f} cone={cone}")


_PREDICATES = {
    "axis_vertical": pred_axis_vertical,
    "axis_parallel": pred_axis_parallel,
    "center_align": pred_center_align,
    "above": pred_above,
    "inside": pred_inside,
    "clearance": pred_clearance,
    "region_grasp": pred_region_grasp,
    "approach_direction": pred_approach_direction,
}


def check(constraint, entities, **ctx) -> Predicate:
    """入口:按约束名派发三值谓词。

    constraint : {"name","args",...}(词表约束)。
    entities   : {name: {"pos","quat","aabb"}} 实体快照（Oracle/evaluator 侧特权量）。
    ctx        : 谓词专用输入,如 grasp_point=[x,y,z]、approach_dir=[x,y,z]。

    未覆盖名的处置(不 fail-open):
      • carry/order → UNKNOWN(reason="uncheckable_in_runtime") 并计入报告。
      • 词表外的名 → UNKNOWN(reason="not_in_vocab");词表内但无谓词实现的 → 同上。
    异常绝不吞成 PASS:任何谓词内部异常 → UNKNOWN(reason="predicate_error"),记账待查。
    """
    name = (constraint or {}).get("name")
    if name in UNCHECKABLE_IN_RUNTIME:
        return _unknown(name, "uncheckable_in_runtime")
    fn = _PREDICATES.get(name)
    if fn is None:
        reason = "not_in_vocab" if name not in vocab.CONSTRAINT_VOCAB else "no_predicate_impl"
        return _unknown(name or "?", reason)
    try:
        return fn(constraint, entities, **ctx)
    except Exception as e:                            # 检查不了 = UNKNOWN,绝不 fail-open 成 PASS
        return _unknown(name, "predicate_error", f"{type(e).__name__}:{e}")


def coverage() -> dict:
    """10 词表约束 × 覆盖态,供报告/门禁用。checkable / uncheckable_in_runtime 两类。"""
    out = {}
    for name in vocab.CONSTRAINT_VOCAB:
        if name in _PREDICATES:
            out[name] = "checkable"
        elif name in UNCHECKABLE_IN_RUNTIME:
            out[name] = "uncheckable_in_runtime"
        else:
            out[name] = "no_predicate_impl"
    return out
