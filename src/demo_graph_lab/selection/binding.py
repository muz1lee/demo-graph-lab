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

from dataclasses import dataclass
import math
from typing import Iterable, Mapping

from ..graph import vocab
from ..perception.observations import ObservationPacket
from .candidates import CandidateBundle, CheckStatus


_GEOMETRIC_HOLE_LENGTHS = {
    "pose_se3": 7,
    "axis_3d": 3,
    "point_3d": 3,
}
_CANDIDATE_VALUE_FIELDS = {"value", "frame", "calibration_ref", "object_id"}


@dataclass(frozen=True)
class BindingValidation:
    """Fail-closed result for one candidate's typed-hole values."""

    status: CheckStatus
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError("binding validation must include at least one reason")


def _numeric_vector(value, length: int) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == length
        and all(
            not isinstance(item, bool)
            and isinstance(item, (int, float))
            and math.isfinite(item)
            for item in value
        )
    )


def _unit_vector(value, *, tolerance: float = 1e-3) -> bool:
    return math.isclose(
        math.sqrt(sum(float(item) ** 2 for item in value)),
        1.0,
        rel_tol=0.0,
        abs_tol=tolerance,
    )


def _stage_object_ids(stage: Mapping) -> set[str]:
    stage_objects = stage.get("stage_objects") or {}
    return {
        value
        for value in stage_objects.values()
        if isinstance(value, str) and value
    }


def _validate_geometry_value(
    hole: Mapping,
    raw_value,
    stage_object_ids: set[str],
    observation: ObservationPacket,
) -> tuple[CheckStatus, tuple[str, ...]]:
    name = str(hole.get("name", ""))
    hole_type = hole.get("type")
    expected_length = _GEOMETRIC_HOLE_LENGTHS[hole_type]
    reasons: list[str] = []
    status = CheckStatus.PASS

    if not isinstance(raw_value, Mapping) or set(raw_value) != _CANDIDATE_VALUE_FIELDS:
        return CheckStatus.FAIL, (f"{name}:invalid_value_envelope",)

    vector = raw_value.get("value")
    if not _numeric_vector(vector, expected_length):
        reasons.append(f"{name}:invalid_{hole_type}_shape")
        status = CheckStatus.FAIL
    elif hole_type == "pose_se3" and not _unit_vector(vector[3:7]):
        reasons.append(f"{name}:quaternion_not_unit_xyzw")
        status = CheckStatus.FAIL
    elif hole_type == "axis_3d" and not _unit_vector(vector):
        reasons.append(f"{name}:axis_not_unit")
        status = CheckStatus.FAIL

    frame = raw_value.get("frame")
    requested_frame = hole.get("frame")
    if not isinstance(frame, str) or not frame:
        reasons.append(f"{name}:missing_frame")
        status = CheckStatus.FAIL
    elif not isinstance(requested_frame, str) or not requested_frame:
        reasons.append(f"{name}:hole_frame_not_declared")
        status = CheckStatus.FAIL
    elif frame != requested_frame:
        reasons.append(f"{name}:frame_mismatch:{frame}!={requested_frame}")
        status = CheckStatus.FAIL
    elif frame != observation.frame:
        reasons.append(
            f"{name}:observation_frame_mismatch:{frame}!={observation.frame}"
        )
        status = CheckStatus.FAIL

    calibration_ref = raw_value.get("calibration_ref")
    if not isinstance(calibration_ref, str) or not calibration_ref:
        reasons.append(f"{name}:missing_calibration_ref")
        status = CheckStatus.FAIL
    elif calibration_ref != observation.calibration_ref:
        reasons.append(f"{name}:calibration_mismatch")
        status = CheckStatus.FAIL

    object_id = raw_value.get("object_id")
    if not isinstance(object_id, str) or not object_id:
        reasons.append(f"{name}:missing_object_id")
        status = CheckStatus.FAIL
    elif object_id not in stage_object_ids:
        reasons.append(f"{name}:object_not_in_stage:{object_id}")
        status = CheckStatus.FAIL
    else:
        anchor = hole.get("anchor")
        if anchor is None:
            if len(stage_object_ids) > 1:
                reasons.append(f"{name}:hole_object_anchor_ambiguous")
                if status is CheckStatus.PASS:
                    status = CheckStatus.UNKNOWN
        elif not isinstance(anchor, Mapping):
            reasons.append(f"{name}:hole_object_anchor_invalid")
            status = CheckStatus.FAIL
        else:
            anchor_object_id = anchor.get("object_id")
            if not isinstance(anchor_object_id, str) or not anchor_object_id:
                reasons.append(f"{name}:hole_object_anchor_invalid")
                status = CheckStatus.FAIL
            elif anchor_object_id not in stage_object_ids:
                reasons.append(
                    f"{name}:hole_object_anchor_not_in_stage:{anchor_object_id}")
                status = CheckStatus.FAIL
            elif object_id != anchor_object_id:
                reasons.append(
                    f"{name}:object_anchor_mismatch:"
                    f"{object_id}!={anchor_object_id}")
                status = CheckStatus.FAIL
        observed = next(
            (item for item in observation.objects if item.object_id == object_id),
            None,
        )
        if observed is None:
            reasons.append(f"{name}:object_not_observed:{object_id}")
            if status is CheckStatus.PASS:
                status = CheckStatus.UNKNOWN
        elif isinstance(frame, str) and observed.frame != frame:
            reasons.append(
                f"{name}:object_frame_mismatch:{observed.frame}!={frame}"
            )
            status = CheckStatus.FAIL

    return status, tuple(reasons or (f"{name}:valid",))


def validate_candidate_bindings(
    candidate: CandidateBundle,
    stage: Mapping,
    observation: ObservationPacket,
    *,
    required_holes: Iterable[str] | None = None,
) -> BindingValidation:
    """Validate candidate values before any physical checker is allowed to run.

    Geometry uses one closed representation::

        {"value": [...], "frame": "...", "calibration_ref": "...",
         "object_id": "..."}

    Pose values are ``[x, y, z, qx, qy, qz, qw]``.  V1 does not perform
    implicit frame aliases or transforms.  Scalar and runtime-condition holes
    must come from a separate trusted resolver, never from a candidate provider.
    In a multi-object stage, ``hole.anchor.object_id`` selects the object whose
    candidate geometry may fill that hole.
    """

    if candidate.observation_id != observation.observation_id:
        return BindingValidation(
            CheckStatus.FAIL,
            ("candidate_observation_mismatch",),
        )

    holes = {
        hole.get("name"): hole
        for hole in stage.get("holes", [])
        if isinstance(hole, Mapping) and isinstance(hole.get("name"), str)
    }
    required = (
        tuple(
            name
            for name, hole in holes.items()
            if hole.get("type") in _GEOMETRIC_HOLE_LENGTHS
        )
        if required_holes is None
        else tuple(required_holes)
    )
    reasons: list[str] = []
    statuses: list[CheckStatus] = []

    for name in required:
        hole = holes.get(name)
        if hole is None:
            reasons.append(f"unknown_required_hole:{name}")
            statuses.append(CheckStatus.FAIL)
        elif name not in candidate.hole_values:
            if hole.get("type") in {"scalar", "runtime_condition"}:
                reasons.append(f"{name}:trusted_runtime_source_unavailable")
                statuses.append(CheckStatus.UNKNOWN)
            else:
                reasons.append(f"{name}:missing_required_value")
                statuses.append(CheckStatus.FAIL)

    stage_objects = _stage_object_ids(stage)
    for name, raw_value in candidate.hole_values.items():
        hole = holes.get(name)
        if hole is None:
            reasons.append(f"unknown_candidate_hole:{name}")
            statuses.append(CheckStatus.FAIL)
            continue
        hole_type = hole.get("type")
        if hole_type in {"scalar", "runtime_condition"}:
            reasons.append(f"{name}:candidate_source_forbidden:{hole_type}")
            statuses.append(CheckStatus.FAIL)
            continue
        if hole_type not in _GEOMETRIC_HOLE_LENGTHS:
            reasons.append(f"{name}:unknown_hole_type:{hole_type}")
            statuses.append(CheckStatus.FAIL)
            continue
        value_status, value_reasons = _validate_geometry_value(
            hole,
            raw_value,
            stage_objects,
            observation,
        )
        statuses.append(value_status)
        reasons.extend(value_reasons)

    if CheckStatus.FAIL in statuses:
        status = CheckStatus.FAIL
    elif CheckStatus.UNKNOWN in statuses:
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    if not reasons:
        reasons.append("candidate_bindings_valid")
    return BindingValidation(status, tuple(reasons))


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
    # 竖直归一化区带 → 世界 z。region 缺省(非抓取语义)取质心高度;
    # 词表外 region 不静默退化成质心(与 regions.region_preference 同规)。
    if region in _REGION_BAND_CENTER:
        s = _REGION_BAND_CENTER[region]
        z = lo[2] + s * (hi[2] - lo[2])
        region_status = "band"
    elif region in _REGION_UNCHECKABLE:
        z, region_status = ent["pos"][2], "uncheckable"
    elif region is None:
        z, region_status = ent["pos"][2], "centroid"
    else:
        raise ValueError(
            f"未知 region {region!r};合法(vocab.GRASP_REGIONS):{vocab.GRASP_REGIONS}")

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
