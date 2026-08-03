"""[phase1 · regions] region/cone → 候选集的**单调偏好排序**(P0-03,改动 C-4/C-5)。

设计依据:docs/TODO.md §1.2 C-4/C-5、§1.3 CC-1′/CC-2′、docs/PROPOSAL.md v4 §2.1/§2.3。

━━ 本模块的定位(违反即失败)━━
  • **偏好函数任务无关、全任务共用、不淘汰。** 这里只做漏斗 L2(偏好排序);
    唯一的淘汰层是 L1(硬可行),不在本模块。region/cone 标签把候选集**重排**,
    从不删候选(PROPOSAL v4 §2.3:「L2 偏好排序」,§2.1:「反传不生成数值、不淘汰」)。
  • **本文件(含 docstring)不得出现任何任务名或物体名**(门禁 grep 硬扫):
    偏好只吃归一化几何量 s∈[0,1] 或方向向量,与被抓物是什么无关。这是消灭
    kwadapter 旧 `top - 0.03` 反面教材的第一条设计律:粗标签是排序器,不是生成器。
  • 与 harness.binding._REGION_BAND_CENTER **同调**:band center 是「单洞求解」时的
    代表点(一个 s 值),本模块的偏好函数是「候选集排序」;两者对同一 region 指向同一
    高度方向(band center 落在偏好函数的高分区)。数值对照见文件尾 _COHERENCE 注释。

━━ 偏好函数(docs/TODO.md §1.2 C-4 表,逐字)━━
    upper_body → f(s) = s            (越靠上越优,线性)
    bottom     → f(s) = 1 − s        (越靠下越优,线性)
    middle     → f(s) = 1 − |s−0.5|·2 (越靠中越优,三角)
    top        → f(s) = s²           (越靠顶越优,凸)
    rim / handle → UNCHECKABLE       (几何特征检测,v1 不做;排序退化为**恒等**并标注,
                                      不许用区间硬凑当 middle 处理)

其中 s = (p·u − min)/(max − min):候选点在物体竖直轴上的投影归一化坐标;
extent 用**全边长**(max−min,不是半长)。候选若已带 height_fraction 即直接用作 s。
"""

from __future__ import annotations

import math

from . import vocab


# 显式不可检查态:排序退化为恒等,调用方须能从返回中读到 uncheckable 标志。
UNCHECKABLE = "UNCHECKABLE"


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
      ① 候选自带 `height_fraction`(P1-07 点云链闭合后由 perception 算好,直接用);
      ② 从几何算:s = (p·u − min)/(max − min),u 为竖直单位轴缺省 [0,0,1],
         p 取候选 `xyz`/`point`,min/max 取候选 `extent`={"min","max"}(全边长)。
    两者都取不到 → None(调用方据此判 s 不可算,稳定排序退化为恒等)。
    """
    if "height_fraction" in candidate and candidate["height_fraction"] is not None:
        return float(candidate["height_fraction"])

    p = candidate.get("xyz") or candidate.get("point")
    ext = candidate.get("extent")
    if p is None or not isinstance(ext, dict):
        return None
    u = candidate.get("axis_up") or [0.0, 0.0, 1.0]
    lo, hi = ext.get("min"), ext.get("max")
    if lo is None or hi is None:
        return None
    proj = sum(p[i] * u[i] for i in range(3))
    proj_lo = sum(lo[i] * u[i] for i in range(3))
    proj_hi = sum(hi[i] * u[i] for i in range(3))   # 全边长:hi−lo,不是半长
    span = proj_hi - proj_lo
    if span == 0:
        return None
    return (proj - proj_lo) / span


# ==========================================================================
# rank_by_region:候选集 → 按 region 偏好**稳定降序**(等分保序)。
# API 名以 tests/test_constraint_causality.py 为准(from harness.regions import
# rank_by_region);返回**新的排好序的候选列表**(不原地改、不删候选)。
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
        meta = {"region": region, "status": UNCHECKABLE, "uncheckable": True}
        return (items, meta) if with_meta else items

    # 稳定降序:key = −score;s 不可算的候选给 −inf 的分(沉到末尾但彼此保序)。
    def _key(c):
        s = _height_fraction(c)
        if s is None:
            return float("inf")        # 升序排在最后 = 偏好最低
        return -pref(s)

    ranked = sorted(items, key=_key)
    meta = {"region": region, "status": "ranked", "uncheckable": False}
    return (ranked, meta) if with_meta else ranked


# ==========================================================================
# cone 角度偏好(改动 C-5):approach 方向与 cone 轴的夹角越小越优。
# 与 region 同构处理为**偏好函数**(不设 half_angle 硬阈,见 TODO 未决 #4 建议)。
# ==========================================================================
# cone → 世界系代表轴(单位向量)。approach 方向与该轴夹角越小,越贴合该 cone。
#   top_down : 从正上方下探      → 期望 approach 方向 ≈ −z(向下)
#   side     : 水平侧向接触      → 期望 approach 方向 ≈ 水平(与 z 正交)
#   oblique  : 斜向(约 45°)     → 期望 approach 方向 ≈ (−z 与水平各半)
# 轴向量只表达「方向偏好的参照」,不含任何场景度量魔数、不含任务/物体名。
_SIDE_AXIS_XY = 0.0                     # side/oblique 的水平分量方向自由,z 分量定成败
_CONE_AXIS = {
    "top_down": [0.0, 0.0, -1.0],
    "side":     [1.0, 0.0, 0.0],
    "oblique":  [1.0, 0.0, -1.0],
}


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    if n == 0:
        return None
    return [x / n for x in v]


def cone_axis(cone):
    """cone 名 → 世界系单位参照轴;未知 cone → ValueError(封闭词表,不兜底)。"""
    axis = _CONE_AXIS.get(cone)
    if axis is None:
        raise ValueError(
            f"未知 cone {cone!r};合法(vocab.APPROACH_CONES):{vocab.APPROACH_CONES}")
    return _unit(axis)


def cone_angle_deg(approach_dir, cone):
    """approach 方向向量与 cone 参照轴的夹角(度)∈[0,180];方向缺失/零向量 → None。"""
    if not approach_dir:
        return None
    a = _unit(approach_dir)
    b = cone_axis(cone)
    if a is None or b is None:
        return None
    dot = max(-1.0, min(1.0, sum(a[i] * b[i] for i in range(3))))
    return math.degrees(math.acos(dot))


def cone_preference(approach_dir, cone):
    """cone 偏好分:与参照轴越对齐越高。分 = cos(夹角)∈[−1,1](越大越优)。

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
    cone_axis(cone)          # 未知 cone 在此 ValueError
    items = list(candidates or [])

    def _key(c):
        score = cone_preference(c.get(dir_key), cone)
        if score is None:
            return float("inf")
        return -score

    ranked = sorted(items, key=_key)
    meta = {"cone": cone, "status": "ranked", "uncheckable": False}
    return (ranked, meta) if with_meta else ranked


# ==========================================================================
# _COHERENCE(与 harness.binding._REGION_BAND_CENTER 的同调对照,非运行期使用)
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
# 两者方向一致(都偏物体上部)。以 TODO §1.2 的偏好函数为准,binding 常量方向同调,无需改动。
# ==========================================================================
