"""[phase1 · binding] 洞求解:按 hole["type"] 派发 5 个求解器,参照物从**本阶段约束 args** 取。

设计依据:docs/TODO.md §1.2 C-3、docs/EXECUTION.md §2.5 #1a/#1b、docs/PROPOSAL.md v4 §2.1。

三条纪律(违反即失败):
  1. **派发以 `hole["type"]` 为准**(vocab.HOLE_TYPES 五类),不以 hole 名字子串匹配。
     旧 kwadapter.solve():295-321 用 `hole_name.lower()` 子串匹配,把 `coin_pose`/`retract_pose`/
     `push_direction` 误派进 runtime_condition 兜底;本模块按 type 派发,三者各归其位。
  2. **参照物从约束 args 取**,不从 stage_objects 猜:
     `region_grasp.obj`+`.region` → 抓取洞的参照物与偏好区带;
     `center_align.obj_b` / `inside.obj_b` → 放置/点洞的目标参照物;
     `approach_direction.cone` → 方向洞的锥。
     没有可用约束时才回退到 stage_objects(manipulated/target),并在句柄里标注 ref_source。
  3. **`solver_hint` 只用于选求解器,禁止据它建任务分支**(本模块甚至不读 solver_hint——
     type 已是权威;保留形参只为将来 pose_se3 内部多求解器时选择,绝不产生 per-task 逻辑)。

**region → 抓取点几何走「归一化几何」(D-03)**:物体 AABB 的竖直归一化高度带,全任务同一套规则,
不含任何任务名/物体名/场景度量魔数(消灭旧 `top - 0.03` 反面教材)。本模块只在**单洞求解**时用
区带中心生成一个代表位姿;候选集的偏好排序(rank_by_region)是 P0-03 harness.regions 的职责,
本模块不实现、不越界。

非 world frame 的洞先过 `to_world()` 纯函数(当前支持 world / object 两类 frame);frame 缺省视为 world。
"""

from __future__ import annotations

from . import vocab


class UnsolvedHole(Exception):
    """洞无法绑定时抛出。归因字段固定 layer='L2_bind'(见 docs/TODO.md §1.2 C-2)。

    触发点:①`_hole_index` 查不到 hole_name(kwadapter 侧,不回退当前阶段猜);
            ②hole 缺 type 或 type 不在 vocab.HOLE_TYPES;
            ③某求解器拿不到必需的参照实体。
    """

    layer = "L2_bind"

    def __init__(self, message: str, *, hole=None, reason=None):
        super().__init__(message)
        self.hole = hole
        self.reason = reason


# --------------------------------------------------------------------------
# region → 归一化竖直区带中心 s∈[0,1](s 越大越靠物体上部)。全任务同一套规则。
# 这不是 P0-03 的偏好排序函数(那作用在候选集上、由 harness.regions 提供);这里只是
# 「单洞求解」时给一个代表性抓取高度。rim/handle 无法只凭 AABB 竖直带定位 → UNCHECKABLE,
# 求解器退回物体质心高度并在句柄标注(不许用区间硬凑,D-03 同规)。
# --------------------------------------------------------------------------
_REGION_BAND_CENTER = {
    "bottom": 0.15,
    "middle": 0.50,
    "upper_body": 0.80,
    "top": 0.95,
}
_REGION_UNCHECKABLE = {"rim", "handle"}   # 几何特征检测,v1 不做


# ---------- 坐标变换纯函数 ----------
def to_world(vec3, frame, ref_entity=None):
    """把 frame 坐标系下的一个 3D 点/向量搬到 world。纯函数,无副作用。

    支持:
      - None / "world"      → 原样返回(已在 world)。
      - "object" / "<obj>"  → 平移到 ref_entity 的世界质心(ref_entity['pos']);
                              无 ref_entity 时无法变换 → 视为 world(调用方已在世界系取值)。
    仅平移,不做旋转:M1a 的洞值本身即以 world 轴向量表达(物体皆近似轴对齐,见 kwadapter
    verify 里的 oracle 简化);完整 SO(3) 变换留待 P1 感知链接入 T_base_cam 后补齐。
    """
    if frame in (None, "", "world"):
        return list(vec3)
    if ref_entity is not None and "pos" in ref_entity:
        p = ref_entity["pos"]
        return [vec3[0] + p[0], vec3[1] + p[1], vec3[2] + p[2]]
    return list(vec3)


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


# ==========================================================================
# 5 个求解器。签名统一 (hole, stage, constraints, rt) → 句柄 dict。
# 每个句柄带 kind + hole + ref_source,便于 gate/评测侧审计参照物来源。
# ==========================================================================

def _resolve_ref(rt, name):
    """借 KWRuntime._ent 解析实体(oracle 态)。rt 为 None 或解析失败 → None。"""
    if rt is None or name is None:
        return None
    try:
        return rt._ent(name)
    except Exception:
        return None


def solve_pose_se3(hole, stage, constraints, rt):
    """pose_se3:抓取/放置位姿。参照物与偏好区带从 region_grasp 约束取;无则回退 stage_objects。

    机器人系位姿洞(home/retract 类):按结构字段派发,不看名字——
    `frame` 为机器人锚定(robot_base/base/ee)或全阶段无任何锚定
    (零约束参照 + stage_objects 双 None)时,该洞不描述世界系几何,
    返回机器人系描述子交下游 ctrl(go_home 等)消费。旧 kwadapter 把
    这类洞吞进 condition 兜底;抛 UnsolvedHole 则会在 retreat 阶段崩,
    两者都不对。语料实例:deposit_coin v0.2 的 left/right_arm_home_pose
    (frame=robot_base, stage_objects 全 None)。"""
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

    xyz = to_world([x, y, z], hole.get("frame"), ent)
    return {"kind": "pose", "hole": hole.get("name"), "xyz": xyz, "quat": None,
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
    if ent is not None and "quat" in ent:
        w, x, y, z = ent["quat"]                       # /state 物体四元数 wxyz
        vec = [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]
    else:
        vec = [0.0, 0.0, 1.0]                           # 无参照 → 世界竖直缺省
    vec = to_world(vec, hole.get("frame"), ent)
    return {"kind": "axis", "hole": hole.get("name"), "vec": vec,
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
    xyz = to_world([ent["pos"][0], ent["pos"][1], hi[2]], hole.get("frame"), ent)
    return {"kind": "point", "hole": hole.get("name"), "xyz": xyz,
            "ref": obj, "ref_source": ref_source}


def solve_scalar(hole, stage, constraints, rt):
    """scalar:深度/间隙/容差等标量。M1a 无感知标量源 → 交由下游控制原语按几何决定,
    这里只返回一个不含场景度量魔数的描述子(不再是旧代码里的硬常量 value=0.05)。"""
    return {"kind": "scalar", "hole": hole.get("name"), "value": None,
            "ref_source": "deferred_to_controller"}


def solve_runtime_condition(hole, stage, constraints, rt):
    """runtime_condition:阶段内停止/成立条件(如 lower_until 的 stop_condition)。
    返回描述子,由 lower_until/verify 在运行期消费;参照物取 manipulated+target。"""
    so = (stage or {}).get("stage_objects") or {}
    return {"kind": "condition", "hole": hole.get("name"),
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
    """入口:按 hole["type"] 派发。未知/缺失 type → UnsolvedHole(L2_bind)。

    hole        : {"name","type","solver_hint"?,"frame"?}
    stage       : 该洞所在阶段 dict(供 stage_objects 回退)
    constraints : 本阶段 constraints 列表(参照物的第一来源)
    rt          : KWRuntime(用其 _ent 解析 oracle 实体);离线单测可传桩对象
    """
    htype = (hole or {}).get("type")
    solver = _SOLVERS.get(htype)
    if solver is None:
        raise UnsolvedHole(
            f"hole {hole.get('name')!r}: 未知 hole type {htype!r}"
            f"(合法:{vocab.HOLE_TYPES})",
            hole=hole, reason="unknown_type")
    return solver(hole, stage, constraints or [], rt)
