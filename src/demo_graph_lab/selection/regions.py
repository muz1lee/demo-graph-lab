"""Task-independent ranking by qualitative region and approach direction.

This module only reorders candidates; it never removes them.  Hard feasibility
checks belong before this step.  Preferences use normalized geometry rather than
task names, object names, or scene-specific constants.

Region preferences:
    upper_body → f(s) = s            (越靠上越优,线性)
    bottom     → f(s) = 1 − s        (越靠下越优,线性)
    middle     → f(s) = 1 − |s−0.5|·2 (越靠中越优,三角)
    top        → f(s) = s²           (越靠顶越优,凸)
    rim / handle → UNCHECKABLE       (当前无几何特征检测器;排序退化为**恒等**并标注,
                                      不许用区间硬凑当 middle 处理)

其中 s = (p·u − min)/(max − min):候选点在物体竖直轴上的投影归一化坐标;
extent 用**全边长**(max−min,不是半长)。候选若已带 height_fraction 即直接用作 s。
"""

from __future__ import annotations

from collections.abc import Mapping
import math

from ..graph import vocab


# 显式不可检查态:排序退化为恒等,调用方须能从返回中读到 uncheckable 标志。
UNCHECKABLE = "UNCHECKABLE"
PARTIAL = "PARTIAL"


def _finite_number(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _vector3(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain three finite numbers")
    return [_finite_number(item, name) for item in value]


def _ranking_meta(kind, label, available, total):
    if available == 0:
        status = UNCHECKABLE
    elif available < total:
        status = PARTIAL
    else:
        status = "ranked"
    return {
        kind: label,
        "status": status,
        "uncheckable": available == 0,
        "available_count": available,
        "total_count": total,
    }


# ==========================================================================
# region 偏好函数:s∈[0,1] → 偏好分数(越大越优)。任务无关、纯几何。
# ==========================================================================
def _pref_upper_body(s):
    return s


def _pref_bottom(s):
    return 1.0 - s


def _pref_middle(s):
    return 1.0 - abs(s - 0.5) * 2.0


def _pref_top(s):
    return s * s


# region 名 → 偏好函数。仅收录**可检查**的四类;rim/handle 显式落在 UNCHECKABLE 集,
# 绝不映射到任何偏好函数(不许悄悄当 middle)。
_REGION_PREF = {
    "upper_body": _pref_upper_body,
    "bottom": _pref_bottom,
    "middle": _pref_middle,
    "top": _pref_top,
}
_REGION_UNCHECKABLE = {"rim", "handle"}   # 与 binding._REGION_UNCHECKABLE 同集


def region_preference(region):
    """返回 region 的偏好函数 f(s)->score;rim/handle 返回 UNCHECKABLE 哨兵(字符串)。

    未知 region(不在 vocab.GRASP_REGIONS)→ ValueError:偏好函数是封闭词表,
    不为词表外标签兜底(与 binding 的 unknown_type 抛异常同规)。
    """
    if region in _REGION_PREF:
        return _REGION_PREF[region]
    if region in _REGION_UNCHECKABLE:
        return UNCHECKABLE
    raise ValueError(
        f"未知 region {region!r};合法(vocab.GRASP_REGIONS):{vocab.GRASP_REGIONS}")


# ==========================================================================
# 候选的归一化高度 s。优先用候选自带的 height_fraction;否则从几何算。
# ==========================================================================
def _height_fraction(candidate):
    """取候选的归一化竖直坐标 s∈[0,1]。

    两条来源(按优先级):
      ① 候选自带 `height_fraction`（由上游感知或候选生成器计算）;
      ② 从几何算:s = (p·u − min)/(max − min),u 为竖直单位轴缺省 [0,0,1],
         p 取候选 `xyz`/`point`,min/max 取候选 `extent`={"min","max"}(全边长)。
    两者都取不到 → None(调用方据此判 s 不可算,稳定排序退化为恒等)。
    """
    if "height_fraction" in candidate and candidate["height_fraction"] is not None:
        fraction = _finite_number(
            candidate["height_fraction"], "height_fraction"
        )
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("height_fraction must be within [0, 1]")
        return fraction

    p = candidate.get("xyz")
    if p is None:
        p = candidate.get("point")
    ext = candidate.get("extent")
    if p is None or not isinstance(ext, Mapping):
        return None
    p = _vector3(p, "candidate point")
    axis_up = candidate.get("axis_up")
    if axis_up is None:
        axis_up = [0.0, 0.0, 1.0]
    u = _vector3(axis_up, "axis_up")
    lo, hi = ext.get("min"), ext.get("max")
    if lo is None or hi is None:
        return None
    lo = _vector3(lo, "extent.min")
    hi = _vector3(hi, "extent.max")
    if any(lower > upper for lower, upper in zip(lo, hi)):
        raise ValueError("extent min values must not exceed max values")
    proj = sum(p[i] * u[i] for i in range(3))
    # ``extent`` is a frame-space AABB.  Its projection extrema are generally
    # different corners when an axis contains negative components; simply
    # projecting the stored min and max corners is only correct for an axis in
    # the all-positive octant.
    proj_lo = sum(
        (lo[i] if u[i] >= 0.0 else hi[i]) * u[i]
        for i in range(3)
    )
    proj_hi = sum(
        (hi[i] if u[i] >= 0.0 else lo[i]) * u[i]
        for i in range(3)
    )   # 全边长:hi−lo,不是半长
    span = proj_hi - proj_lo
    if span == 0:
        return None
    fraction = (proj - proj_lo) / span
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("derived height_fraction must be within [0, 1]")
    return fraction


# ==========================================================================
# rank_by_region:候选集 → 按 region 偏好**稳定降序**(等分保序)。
# 返回新的排好序的候选列表，不原地修改，也不删除候选。
# ==========================================================================
def rank_by_region(candidates, region, *, with_meta=False):
    """把候选集按 region 偏好从优到劣稳定排序,返回新列表。

    candidates : dict 列表,每个至少能算出 s(带 height_fraction 或几何字段,见
                 _height_fraction)。
    region     : vocab.GRASP_REGIONS 之一。
    with_meta  : True 时返回 (ranked_list, meta);meta={"region","status","uncheckable"}。
                 默认 False 只返回 ranked_list(与测试签名一致)。

    语义纪律:
      • **不淘汰**:输出与输入等长,只是次序变。空集原样返回。
      • **稳定 + 等分保序**:偏好分相同(或 s 不可算)的候选保持输入相对次序
        (Python sorted 稳定 + 负分做 key,降序即「分高在前、同分保序」)。
      • **rim/handle → 恒等**:偏好不可检查时不排序,原样返回并把 status 标 UNCHECKABLE,
        绝不退化成 middle(那会悄悄给 rim/handle 造出一个假偏好)。
    """
    pref = region_preference(region)   # 未知 region 在此 ValueError
    items = list(candidates or [])

    if pref is UNCHECKABLE:
        meta = _ranking_meta("region", region, 0, len(items))
        return (items, meta) if with_meta else items

    available = sum(_height_fraction(item) is not None for item in items)

    # 稳定降序:key = −score;s 不可算的候选给 −inf 的分(沉到末尾但彼此保序)。
    def _key(c):
        s = _height_fraction(c)
        if s is None:
            return float("inf")        # 升序排在最后 = 偏好最低
        return -pref(s)

    ranked = sorted(items, key=_key)
    meta = _ranking_meta("region", region, available, len(items))
    return (ranked, meta) if with_meta else ranked


# ==========================================================================
# cone 角度偏好：只看 approach 方向相对重力的倾角。
# 与 region 同构处理为偏好函数，不在这里设置硬阈值。
# ==========================================================================
# 倾角从“竖直向下”起算：top_down=0°、oblique=45°、side=90°。
# 水平方位不属于 cone 语义；+x/-x/+y/-y 的同倾角候选必须等价。
_CONE_TILT_DEG = {
    "top_down": 0.0,
    "oblique": 45.0,
    "side": 90.0,
}


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    if n == 0:
        return None
    return [x / n for x in v]


def _cone_tilt_deg(cone):
    """Return the gravity-relative target tilt for a closed-vocabulary cone."""
    tilt = _CONE_TILT_DEG.get(cone)
    if tilt is None:
        raise ValueError(
            f"未知 cone {cone!r};合法(vocab.APPROACH_CONES):{vocab.APPROACH_CONES}")
    return tilt


def cone_angle_deg(approach_dir, cone):
    """Return angular error from the cone's gravity-relative target tilt.

    Horizontal azimuth is intentionally ignored. Missing and zero-length
    directions return ``None``; an unknown cone raises ``ValueError``.
    """
    if approach_dir is None:
        return None
    a = _unit(_vector3(approach_dir, "approach_dir"))
    target_tilt = _cone_tilt_deg(cone)
    if a is None:
        return None
    cos_from_down = max(-1.0, min(1.0, -a[2]))
    actual_tilt = math.degrees(math.acos(cos_from_down))
    return abs(actual_tilt - target_tilt)


def cone_preference(approach_dir, cone):
    """cone 偏好分:相对目标倾角的误差越小越高。分 = cos(误差)∈[−1,1]。

    与 region 偏好同一「越大越优」约定,便于 approach 侧用同一套稳定降序逻辑。
    零向量/未知 cone 分别按 None / ValueError 处理。
    """
    ang = cone_angle_deg(approach_dir, cone)
    if ang is None:
        return None
    return math.cos(math.radians(ang))


def rank_by_cone(candidates, cone, *, dir_key="approach_dir", with_meta=False):
    """把带方向向量的候选按 cone 偏好稳定降序(夹角小=分高=在前)。

    candidates : dict 列表,每个带 `dir_key`(缺省 "approach_dir")= 该候选的 approach 方向。
    与 rank_by_region 同规:不淘汰、稳定、等分/不可算保序(分不出的候选沉末尾且保序)。
    """
    _cone_tilt_deg(cone)     # 未知 cone 在此 ValueError
    items = list(candidates or [])
    for candidate in items:
        direction = candidate.get(dir_key)
        if direction is not None and _unit(
            _vector3(direction, dir_key)
        ) is None:
            raise ValueError(f"{dir_key} must be non-zero")
    available = sum(
        cone_preference(candidate.get(dir_key), cone) is not None
        for candidate in items
    )

    def _key(c):
        score = cone_preference(c.get(dir_key), cone)
        if score is None:
            return float("inf")
        return -score

    ranked = sorted(items, key=_key)
    meta = _ranking_meta("cone", cone, available, len(items))
    return (ranked, meta) if with_meta else ranked


def rank_by_gravity_tilt(
    candidates,
    cone,
    *,
    tilt_key="approach_tilt_deg",
    with_meta=False,
):
    """Rank candidate records by a precomputed gravity-relative tilt.

    Planning candidates use this scalar instead of a bare direction vector, so
    selection cannot silently treat camera-frame ``-z`` as gravity down.
    """

    target_tilt = _cone_tilt_deg(cone)
    items = list(candidates or [])
    tilts = {}
    for index, candidate in enumerate(items):
        value = candidate.get(tilt_key)
        if value is None:
            continue
        tilt = _finite_number(value, tilt_key)
        if not 0.0 <= tilt <= 180.0:
            raise ValueError(f"{tilt_key} must be within [0, 180]")
        tilts[index] = tilt

    ranked_with_index = sorted(
        enumerate(items),
        key=lambda item: (
            float("inf")
            if item[0] not in tilts
            else abs(tilts[item[0]] - target_tilt)
        ),
    )
    ranked = [item for _, item in ranked_with_index]
    meta = _ranking_meta("cone", cone, len(tilts), len(items))
    return (ranked, meta) if with_meta else ranked


# ==========================================================================
# _COHERENCE(与 selection.binding._REGION_BAND_CENTER 的同调对照,非运行期使用)
# ──────────────────────────────────────────────────────────────────────────
# binding 的 band center 是「单洞求解」代表点(一个 s);本模块偏好函数是候选集排序。
# 两者对同一 region 须指向同一高度方向 —— band center 落在偏好函数高分区:
#
#   region       band_center   argmax f(s)   f(band_center)   同调?
#   upper_body      0.80          1.00           0.80          ✓ 单调增,center 在高分侧
#   bottom          0.15          0.00           0.85          ✓ 单调减,center 在高分侧
#   middle          0.50          0.50           1.00          ✓ argmax 精确重合
#   top             0.95          1.00           0.9025        ✓ 单调增,center 在高分侧
#
# 注:upper_body 与 top 的偏好函数在 s=1.0 取极值,而 band center 分别为 0.80/0.95。
# 这不矛盾——band center 是「代表性」抓取高度(留一点顶端余量的工程取值),不是偏好极值点;
# 两者方向一致(都偏物体上部)，binding 常量无需改动。
# ==========================================================================
