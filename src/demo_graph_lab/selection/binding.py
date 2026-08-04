"""Resolve typed holes from the current stage and its constraints.

Dispatch is based on ``hole["type"]``, never on words in the hole name.  Object
references come from constraint arguments and only fall back to ``stage_objects``
when no relevant constraint exists.  Region labels use task-independent normalized
geometry; ranking a real candidate set belongs in :mod:`selection.regions`.

The Oracle reads world-frame geometry from simulator state, so its numeric
handles are returned in the world frame regardless of the graph's requested
representation.  The requested frame is kept for diagnostics.
"""

from __future__ import annotations

from ..graph import vocab


class UnsolvedHole(Exception):
    """洞无法绑定时抛出。归因字段固定为 ``binding``。

    触发点:①当前 stage 查不到 hole_name(不回退猜测);
            ②hole 缺 type 或 type 不在 vocab.HOLE_TYPES;
            ③某求解器拿不到必需的参照实体。
    """

    layer = "binding"

    def __init__(self, message: str, *, hole=None, reason=None):
        super().__init__(message)
        self.hole = hole
        self.reason = reason


# region → 归一化竖直区带中心 s∈[0,1](s 越大越靠物体上部)。全任务同一套规则。
# 这里给单个洞一个代表高度；候选集排序由 selection.regions 负责。
# rim/handle 无法从 AABB 定位，返回质心并标为 uncheckable。
_REGION_BAND_CENTER = {
    "bottom": 0.15,
    "middle": 0.50,
    "upper_body": 0.80,
    "top": 0.95,
}
_REGION_UNCHECKABLE = {"rim", "handle"}   # 当前无几何特征检测器


# ---------- 约束检索(参照物从约束 args 取) ----------
def _constraint(constraints, name):
    for c in constraints or []:
        if c.get("name") == name:
            return c
    return None


def _args(c):
    return (c or {}).get("args", {}) or {}


def _aabb_bounds(entity):
    """返回 (lo, hi) 两个三元组;两种 aabb 形态都读(dict{min,max} 或 [min,max])。"""
    bx = entity.get("aabb")
    if isinstance(bx, dict):
        return bx["min"], bx["max"]
    return bx[0], bx[1]


# 5 个求解器。签名统一 (hole, stage, constraints, rt) → 句柄 dict。
# 每个句柄带 kind + hole + ref_source,便于 gate/评测侧审计参照物来源。

def _resolve_ref(rt, name):
    """借 OracleRuntime._ent 解析实体。rt 为 None 或解析失败 → None。"""
    if rt is None or name is None:
        return None
    try:
        return rt._ent(name)
    except Exception:
        return None


def solve_pose_se3(hole, stage, constraints, rt):
    """pose_se3:抓取/放置位姿。参照物与偏好区带从 region_grasp 约束取;无则回退 stage_objects。

    `frame` 为 robot_base/base/ee，或阶段没有物体锚点时，返回不含数值的
    机器人系描述子，交给下游控制器解释。
    """
    so = (stage or {}).get("stage_objects") or {}
    rg = _constraint(constraints, "region_grasp")
    ca = _constraint(constraints, "center_align")
    inside = _constraint(constraints, "inside")

    _ROBOT_FRAMES = {"robot_base", "base", "robot", "ee", "end_effector"}
    anchor_free = (rg is None and ca is None and inside is None
                   and so.get("manipulated") is None and so.get("target") is None)
    if str(hole.get("frame", "")).lower() in _ROBOT_FRAMES or anchor_free:
        return {"kind": "pose", "hole": hole.get("name"), "xyz": None, "quat": None,
                "frame": hole.get("frame") or "robot_base",
                "ref": None, "ref_source": "robot_frame"}

    if rg is not None:
        obj = _args(rg).get("obj") or so.get("manipulated")
        region = _args(rg).get("region")
        ref_source = "region_grasp"
    elif ca is not None:
        obj = _args(ca).get("obj_b") or so.get("target")
        region, ref_source = None, "center_align"
    elif inside is not None:
        obj = _args(inside).get("obj_b") or so.get("target")
        region, ref_source = None, "inside"
    else:
        obj = so.get("manipulated") or so.get("target")
        region, ref_source = None, "stage_objects"

    ent = _resolve_ref(rt, obj)
    if ent is None:
        raise UnsolvedHole(f"pose_se3 hole {hole.get('name')!r}: 参照物 {obj!r} 无法解析",
                           hole=hole, reason="ref_unresolved")

    lo, hi = _aabb_bounds(ent)
    x, y = ent["pos"][0], ent["pos"][1]
    # 竖直归一化区带 → 世界 z。region 缺省(非抓取语义)取质心高度。
    if region in _REGION_BAND_CENTER:
        s = _REGION_BAND_CENTER[region]
        z = lo[2] + s * (hi[2] - lo[2])
        region_status = "band"
    elif region in _REGION_UNCHECKABLE:
        z, region_status = ent["pos"][2], "uncheckable"
    else:
        z, region_status = ent["pos"][2], "centroid"

    xyz = [x, y, z]
    return {"kind": "pose", "hole": hole.get("name"), "xyz": xyz, "quat": None,
            "frame": "world", "requested_frame": hole.get("frame"),
            "ref": obj, "ref_source": ref_source, "region": region,
            "region_status": region_status}


def solve_axis_3d(hole, stage, constraints, rt):
    """axis_3d:方向向量。参照物取自 axis_* / approach_direction 约束;从物体局部 +z 派生。"""
    so = (stage or {}).get("stage_objects") or {}
    av = _constraint(constraints, "axis_vertical")
    ap = _constraint(constraints, "axis_parallel")
    if av is not None:
        obj = str(_args(av).get("axis", "")).split(".")[0] or so.get("manipulated")
        ref_source = "axis_vertical"
    elif ap is not None:
        obj = str(_args(ap).get("axis_a", "")).split(".")[0] or so.get("manipulated")
        ref_source = "axis_parallel"
    else:
        obj = so.get("manipulated") or so.get("target")
        ref_source = "stage_objects"

    ent = _resolve_ref(rt, obj)
    if ent is None or "quat" not in ent:
        raise UnsolvedHole(
            f"axis_3d hole {hole.get('name')!r}: 参照物 {obj!r} 的姿态无法解析",
            hole=hole,
            reason="axis_unobserved",
        )
    w, x, y, z = ent["quat"]                           # /state 物体四元数 wxyz
    vec = [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]
    return {"kind": "axis", "hole": hole.get("name"), "vec": vec,
            "frame": "world", "requested_frame": hole.get("frame"),
            "ref": obj, "ref_source": ref_source}


def solve_point_3d(hole, stage, constraints, rt):
    """point_3d:放置/插入点。参照物优先 center_align.obj_b / inside.obj_b,再回退 target。"""
    so = (stage or {}).get("stage_objects") or {}
    ca = _constraint(constraints, "center_align")
    inside = _constraint(constraints, "inside")
    above = _constraint(constraints, "above")
    if ca is not None:
        obj = _args(ca).get("obj_b") or so.get("target")
        ref_source = "center_align"
    elif inside is not None:
        obj = _args(inside).get("obj_b") or so.get("target")
        ref_source = "inside"
    elif above is not None:
        obj = _args(above).get("obj_b") or so.get("target")
        ref_source = "above"
    else:
        obj = so.get("target") or so.get("manipulated")
        ref_source = "stage_objects"

    ent = _resolve_ref(rt, obj)
    if ent is None:
        raise UnsolvedHole(f"point_3d hole {hole.get('name')!r}: 参照物 {obj!r} 无法解析",
                           hole=hole, reason="ref_unresolved")
    lo, hi = _aabb_bounds(ent)
    xyz = [ent["pos"][0], ent["pos"][1], hi[2]]
    return {"kind": "point", "hole": hole.get("name"), "xyz": xyz,
            "frame": "world", "requested_frame": hole.get("frame"),
            "ref": obj, "ref_source": ref_source}


def solve_scalar(hole, stage, constraints, rt):
    """scalar:Oracle 无独立标量源，返回描述子并交由下游控制器决定。"""
    return {"kind": "scalar", "hole": hole.get("name"), "value": None,
            "ref_source": "deferred_to_controller"}


def solve_runtime_condition(hole, stage, constraints, rt):
    """runtime_condition:阶段内停止/成立条件(如 lower_until 的 stop_condition)。
    返回描述子,由 lower_until/verify 在运行期消费;参照物取 manipulated+target。"""
    so = (stage or {}).get("stage_objects") or {}
    return {"kind": "condition", "hole": hole.get("name"),
            "purpose": hole.get("purpose"),
            "manip": so.get("manipulated"), "target": so.get("target"),
            "ref_source": "stage_objects"}


_SOLVERS = {
    "pose_se3": solve_pose_se3,
    "axis_3d": solve_axis_3d,
    "point_3d": solve_point_3d,
    "scalar": solve_scalar,
    "runtime_condition": solve_runtime_condition,
}


def solve_hole(hole, stage, constraints, rt):
    """入口:按 hole["type"] 派发。未知或缺失 type 时抛出 UnsolvedHole。

    hole        : {"name","type","solver_hint"?,"frame"?}
    stage       : 该洞所在阶段 dict(供 stage_objects 回退)
    constraints : 本阶段 constraints 列表(参照物的第一来源)
    rt          : OracleRuntime(用其 _ent 解析 oracle 实体);离线单测可传桩对象
    """
    htype = (hole or {}).get("type")
    solver = _SOLVERS.get(htype)
    if solver is None:
        raise UnsolvedHole(
            f"hole {hole.get('name')!r}: 未知 hole type {htype!r}"
            f"(合法:{vocab.HOLE_TYPES})",
            hole=hole, reason="unknown_type")
    return solver(hole, stage, constraints or [], rt)
