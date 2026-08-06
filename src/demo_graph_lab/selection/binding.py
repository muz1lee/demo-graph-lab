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


# 长轴判据:**局部**次长边 / 最长边 超过此比值 → 近立方/近方形,主方向不可辨,拒绝。
# 口径与 perception.operators.fit_principal_axis 的 PCA 判据一致(次/主 > 0.8 即歧义)。
# 判的必须是反求出来的局部边长,不是世界 AABB 跨度:世界跨度会被姿态撑大,同一根管子
# 换个 yaw 比值就变(8/6 ep3 实测 tube1 的世界跨度比值 0.648,照样过闸)。
_AXIS_DOMINANCE_MAX_RATIO = 0.8
# |R| 的高斯消元主元小于此值即视为奇异(如绕竖直轴恰 45°:两行相同,世界 AABB
# 对这两条局部边长完全无信息)。数值退化就拒绝,不猜。
_ROT_SINGULAR_EPS = 1e-6
# 反求出的局部边长允许的负向浮点噪声;超过就说明这组 (AABB, quat) 自身不自洽。
_EXTENT_NEG_TOL_M = 1e-9
# 长轴与世界 z 的方向余弦 ≥ 此值才算「立着」;此时 region 的上下端才有可靠含义
# (0.9 ≈ 与竖直夹角 25.8°)。横躺资产的局部 +z 只偏离竖直 4.3°,靠它区分不了。
_AXIS_VERTICAL_DOT = 0.9


def _local_axis_in_world(quat, index):
    """物体四元数(``/state`` 的 wxyz)旋转矩阵的第 ``index`` 列 = 局部轴的世界方向。

    ``index == 2`` 就是旧实现里直接内联的那条局部 +z 表达式,逐项相同。
    """
    w, x, y, z = quat
    if index == 0:
        return [1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)]
    if index == 1:
        return [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)]
    return [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]


def _solve3(matrix, rhs):
    """3×3 线性方程组 ``matrix · x = rhs``,列主元高斯消元。奇异 → ``None``。

    只有 3 阶,纯 Python 展开即可,不引入 numpy 依赖。入参不被修改。
    """
    rows = [[float(v) for v in matrix[i]] + [float(rhs[i])] for i in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < _ROT_SINGULAR_EPS:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(3):
            if r == col:
                continue
            factor = rows[r][col] / rows[col][col]
            for c in range(col, 4):
                rows[r][c] -= factor * rows[col][c]
    return [rows[i][3] / rows[i][i] for i in range(3)]


def _local_extents(ent):
    """从世界 AABB 跨度**反求**物体的局部三边长。

    返回 ``(extents, columns, None)``(``extents[k]`` = 局部第 k 轴的边长 m,
    ``columns[k]`` = 该局部轴在世界系的方向)或 ``(None, None, reason)``。

    依据:一个局部边长为 ``e`` 的长方体按旋转矩阵 ``R`` 摆放后,它的世界轴对齐
    包围盒在第 j 轴上的跨度是三条局部边在该轴上投影的绝对值之和::

        S_j = Σ_k |R[j][k]| · e_k

    也就是 ``|R| · e = S``。``|R|`` 和 ``S`` 都是已知量,解这一个 3×3 线性系统就
    把 ``e`` 反求出来了——纯几何,不需要任何物体形状先验。

    这是 8/6 ep3 定位的那个 bug 的正解:旧实现把 ``S`` 的**世界**边序号直接当成
    **局部**轴序号喂给 ``_local_axis_in_world``,两个索引空间根本不是一回事。只有
    姿态轴对齐时它们才碰巧相等;三根同资产同姿态、只差 yaw 的平躺管子会被判出三种
    不同的长轴(实测 86.0° / 8.7° / 拒绝),而正解是三根都一样。

    三处拒绝,都不给静默出口:
      - 快照读不出 AABB → ``no_aabb``;
      - 四元数退化(某条旋转列长度为零)→ ``axis_unobserved``;
      - ``|R|`` 奇异(绕竖直轴恰 45° 这类:世界 AABB 对两条局部边长无信息)或解出
        负边长(这组 AABB 与 quat 不自洽)→ ``axis_extents_unrecoverable``。
    """
    try:
        lo, hi = _aabb_bounds(ent)
        spans = [float(hi[i]) - float(lo[i]) for i in range(3)]
    except Exception:
        return None, None, "no_aabb"
    try:
        columns = [_local_axis_in_world(ent["quat"], k) for k in range(3)]
    except Exception:
        return None, None, "axis_unobserved"
    norms = [math.sqrt(sum(v * v for v in col)) for col in columns]
    if min(norms) < 1e-9:
        return None, None, "axis_unobserved"
    # |R|:第 j 行第 k 列 = 局部 k 轴世界方向的第 j 个分量的绝对值。
    abs_r = [[abs(columns[k][j]) for k in range(3)] for j in range(3)]
    extents = _solve3(abs_r, spans)
    if extents is None or min(extents) < -_EXTENT_NEG_TOL_M:
        return None, None, "axis_extents_unrecoverable"
    extents = [max(0.0, v) for v in extents]
    return extents, [[v / norms[k] for v in columns[k]] for k in range(3)], None


def long_axis_world(ent):
    """实体的**真实长轴**:``(世界系单位向量, 沿该轴的长度 m, None)``
    或 ``(None, None, reason)``。

    局部边长由 ``_local_extents`` 从世界 AABB 反求;长轴 = 局部边长最大的那根轴,
    世界方向就是 ``R`` 的对应列。歧义闸判的是**局部**次/主边长比(见
    ``_AXIS_DOMINANCE_MAX_RATIO``),不是世界跨度比。

    `evaluation.predicates._long_axis_world` 与 `binding._long_axis` 都薄封装本函数
    (前者转三值 reason,后者转 `UnsolvedHole`),口径只有这一份实现。
    """
    extents, columns, reason = _local_extents(ent)
    if reason is not None:
        return None, None, reason
    order = sorted(range(3), key=lambda k: extents[k], reverse=True)
    longest, second = extents[order[0]], extents[order[1]]
    if longest <= 0.0 or second / longest > _AXIS_DOMINANCE_MAX_RATIO:
        return None, None, "axis_ambiguous_extents"
    return columns[order[0]], longest, None


_LONG_AXIS_REFUSAL_MSG = {
    "no_aabb": "快照里没有可读的 AABB,长轴无从谈起",
    "axis_unobserved": "实体四元数退化,长轴方向读不出来",
    "axis_extents_unrecoverable": (
        "世界 AABB 反求不出自洽的局部边长(|R| 奇异或解出负边长)"),
    "axis_ambiguous_extents": "局部边长分不出主方向(近立方/近方形)",
}


def _long_axis(ent, hole):
    """`long_axis_world` 的 binding 侧封装:拒绝面抛 ``UnsolvedHole``,reason 同名。"""
    vec, length, reason = long_axis_world(ent)
    if reason is not None:
        raise UnsolvedHole(
            f"hole {hole.get('name')!r}: {_LONG_AXIS_REFUSAL_MSG[reason]}",
            hole=hole, reason=reason)
    return vec, length


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


# world 与 robot_base 重合的判定容差:位置 1 mm、四元数分量 1e-3。
_BASE_AT_ORIGIN_TOL_M = 1e-3
_BASE_IDENTITY_TOL = 1e-3


def _world_equals_robot_base(rt) -> bool:
    """断言 oracle 场景里 world 系与 robot_base 系重合。

    只有当实体表里存在**唯一**一个名字含 "robot" 的实体、且它位于世界原点、姿态
    为单位四元数时才成立;此时 world 系的数值同时也是 robot_base 系的数值。零个
    或多个候选都返回 ``False``(不猜哪个是根),读不到实体表也返回 ``False``。
    这条断言只用于特权 oracle 调试路径,不是通用的 frame 变换。
    """
    entities = getattr(rt, "_entities", None)
    if not callable(entities):
        return False
    try:
        table = entities()
    except Exception:
        return False
    hits = [key for key in table if "robot" in str(key).lower()]
    if len(hits) != 1:
        return False
    root = table[hits[0]]
    if not isinstance(root, Mapping):
        return False
    pos, quat = root.get("pos"), root.get("quat")
    if not _numeric_vector(tuple(pos or ()), 3):
        return False
    if not _numeric_vector(tuple(quat or ()), 4):
        return False
    if max(abs(float(item)) for item in pos) > _BASE_AT_ORIGIN_TOL_M:
        return False
    w, x, y, z = (float(item) for item in quat)       # /state 物体四元数 wxyz
    return (abs(abs(w) - 1.0) <= _BASE_IDENTITY_TOL
            and max(abs(x), abs(y), abs(z)) <= _BASE_IDENTITY_TOL)


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
    _BASE_FRAMES = {"robot_base", "base", "robot"}
    anchor_free = (rg is None and ca is None and inside is None
                   and so.get("manipulated") is None and so.get("target") is None)
    requested = str(hole.get("frame", "")).lower()
    # live 契约要求几何洞 frame=robot_base,但本求解器只会算 world 系数值。
    # 特权 oracle 调试路径下,若能断言 world 与 robot_base 重合(机器人根实体在原点、
    # 单位四元数),world 数值同时就是 base 数值,可以给数;断言不成立时维持拒绝,
    # 绝不无条件放行。ee 系不在此列——末端在动,原点重合断言对它没有意义。
    world_is_base = (requested in _BASE_FRAMES
                     and not anchor_free
                     and _world_equals_robot_base(rt))
    if (requested in _ROBOT_FRAMES and not world_is_base) or anchor_free:
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
    # 归一化区带 → **沿真实长轴**取段(s∈[0,1]),不再沿世界 z。region 缺省
    # (非抓取语义)取质心高度;词表外 region 不静默退化成质心(与
    # regions.region_preference 同规)。
    end_ambiguous = None
    if region in _REGION_BAND_CENTER:
        s = _REGION_BAND_CENTER[region]
        axis, length = _long_axis(ent, hole)
        if axis[2] < 0.0:
            axis = [-item for item in axis]      # 端序只由世界 +z 定,翻转不改变轴
        # s=0.5 的段中点:xy 取质心、z 取 AABB 竖直中点,与旧实现的 s=0.5 同一点。
        mid = [x, y, 0.5 * (lo[2] + hi[2])]
        end_ambiguous = abs(axis[2]) < _AXIS_VERTICAL_DOT
        if end_ambiguous:
            # 横躺:没有可靠信号说明哪一端算 upper,取段中点,不猜。副产品正是
            # 「抓取高度 = 质心高度」——横躺圆柱在赤道处夹,而不是赤道以上(实测
            # 8/6 ep1:upper_body 沿世界 z 取点高出赤道 12.7 mm,半径才 16.8 mm,
            # 光滑圆柱在赤道以上夹必滑出)。
            offset = 0.0
        else:
            offset = (s - 0.5) * length
        x, y, z = (mid[i] + offset * axis[i] for i in range(3))
        region_status = "band"
    elif region in _REGION_UNCHECKABLE:
        z, region_status = ent["pos"][2], "uncheckable"
    elif region is None:
        z, region_status = ent["pos"][2], "centroid"
    else:
        raise ValueError(
            f"未知 region {region!r};合法(vocab.GRASP_REGIONS):{vocab.GRASP_REGIONS}")

    xyz = [x, y, z]
    handle = {"kind": "pose", "hole": hole.get("name"), "xyz": xyz, "quat": None,
              "frame": "world", "requested_frame": hole.get("frame"),
              "ref": obj, "ref_source": ref_source, "region": region,
              "region_status": region_status, "end_ambiguous": end_ambiguous}
    if world_is_base:
        # 数值仍然是 world 系算出来的;能交给 robot_base 洞是因为断言了两系重合。
        handle["anchor_source"] = ref_source
        handle["ref_source"] = "world_equals_base_asserted"
    return handle


def solve_axis_3d(hole, stage, constraints, rt):
    """axis_3d:方向向量。参照物取自 axis_* / approach_direction 约束。

    长轴由 ``_long_axis`` 给出:解 ``|R|·e = S`` 从世界 AABB 反求局部边长,取局部最长
    边所在的轴。不再无条件取物体局部 +z(横躺资产的局部 +z 仍近竖直,拿它当长轴会让
    下游 ``_grasp_quat`` 的 yaw 跟着噪声抖),也不再把世界边序号当局部轴序号。
    """
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
    if ent is None or "quat" not in ent or ent.get("aabb") is None:
        raise UnsolvedHole(
            f"axis_3d hole {hole.get('name')!r}: 参照物 {obj!r} 的姿态无法解析",
            hole=hole,
            reason="axis_unobserved",
        )
    vec, _length = _long_axis(ent, hole)
    return {"kind": "axis", "hole": hole.get("name"), "vec": vec,
            "frame": "world", "requested_frame": hole.get("frame"),
            "ref": obj, "ref_source": ref_source,
            "axis_source": "local_extents_from_aabb"}


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
