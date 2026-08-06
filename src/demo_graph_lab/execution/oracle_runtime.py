"""Privileged Knowin World runtime for integration debugging.

High-level actions use the pipeline control API. ``solve`` and ``verify`` read
privileged entity state from EvalServer, so this runtime is an oracle upper bound,
not the non-privileged research method. Official probes are recorded only as
evaluation evidence and never drive control.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.request

from ..evaluation import predicates
from ..selection import binding, regions
from . import robot_api
from .pipeline import PipelineClient

ORACLE_BANNER = "PRIVILEGED_ORACLE"

# Calibrated motion and gripper constants used by the Oracle bring-up runtime.
PREGRASP_DZ, LIFT_DZ, ALIGN_DZ = 0.10, 0.12, 0.06
# 下探细步:8/6 任务 B 实测,间隙只剩 1.5 mm 时单步 20 mm 下探的**第 1 步**就打出
# 656 N(接触判据阈 CONTACT_FORCE_N=20 N,冲击超 30 倍)——一步就撞穿了,判据再灵
# 也来不及在步内停。步长降到 5 mm 让接触发生在判据的采样粒度之内。
# 证据:5090 `~/dgl-stack/evidence/taskb/`,privileged-debug 档,2026-08-06。
LOWER_STEP, LOWER_MAX_STEPS = 0.005, 12
# 判「停止下降」的单步最小降距,随步长按 20% 走(0.02 步长时代即 0.004)。
# 固定 0.004 会在 5mm 步长×上游 74% 交付率(实得≈3.7mm)下第 2 步误判 plateau。
LOWER_PLATEAU_M = 0.2 * LOWER_STEP
GRIP_OPEN, GRIP_CLOSE = 100.0, 0.0  # Pipeline convention: 100=open, 0=closed.
GRIP_CLOSE_TUBE = GRIP_CLOSE
GRIP_SETTLE_S = 4.0
# 夹爪角回读的判定容差(度)。原本只是 `_wait_grip` 的「到没到目标角」局部容差,
# `reorient_held_axis` 的持物证据要用同一把尺子从 GRIP_CLOSE/GRIP_OPEN 两端向内收
# 出「夹住带」,所以提成常量,不在两处各写一个 2.0。
GRIP_ANGLE_TOL_DEG = 2.0
# lift 的闭环参数(语义 = 「抬到目标高度」,不是「发够步数」;见 `lift`)。
LIFT_TOL_M = 0.005          # 达标容差:剩余量 ≤ 5 mm 即收敛退出
LIFT_MAX_ITERS = 12         # 迭代上限;到顶仍未收敛按实得高度如实记账,不假装成功
LIFT_STEP_MAX_M = LIFT_DZ   # 单步指令上限:读数异常导致剩余量爆掉时不发超过一次标称抬升
LIFT_CREEP_S = 1.5          # _wait_settle 返回后还会继续爬 1.3-1.9 mm,额外短等吸收
IDLE_ARM = {0: 1, 1: 0}
ARM_LEFT, ARM_RIGHT = 0, 1
# 目标 y 的选臂死区(m):|y| 小于它时左右分不开,保持当前臂,不猜。
# 8/6 ep2 2×2 矩阵实测两根管在 y=+0.258 / −0.365,离死区远得很;死区只兜住
# 「目标恰在身体正前方」这一类没有左右信号的情形。
ARM_SELECT_DEADZONE_Y_M = 0.05
CONTACT_FORCE_N = 20.0
LIFT_LOAD_FORCE_N = CONTACT_FORCE_N / 4.0

# Tool quaternions use xyzw; object quaternions from /state use wxyz.
TDX0 = [0.0, 1.0, 0.0, 0.0]  # Ry(180): tool +z points down.
APPROACH_AXIS_IDX = 2
FINGER_AXIS_IDX = 1
# 爪尖相对 EEF 帧的 z 偏移(m)。**开合状态不同,同一副爪子差 21.9 mm**,所以必须
# 分成两个常数,按消费点当时的爪子状态取用:
#   张爪 `CLAW_TIP_DZ_OPEN`   → `grasp_at`(以 `gpos=GRIP_OPEN` 张着爪下探定位)、
#                               `approach`(预抓取偏置,此时还没闭爪);
#   闭爪 `CLAW_TIP_DZ_CLOSED` → `transport` / `align`(夹着物体移动,爪子是闭的)。
# 8/6 v4 三方交叉实测:自由腕姿张爪 **−3.34 mm**、抓取腕姿张爪 **−3.57 mm**
# ——两者只差 0.23 mm,互为独立佐证;同一抓取腕姿**闭爪**为 **+18.35 mm**。
# 8/6 任务 B 实测背书这次拆分:`align` 的总错配 **−27.2 mm**,其中 **−21.9 mm**
# 正是张/闭爪指尖差(此前两处都沿用张爪值);沿用张爪值时管底与 rack 顶只剩
# **+1.5 mm** 余量,而名义应有 **+23.4 mm**。
# v3 的 −0.010 疑为同一「张爪」语义,但当时没做闭爪交叉验证,遗留待核。
# 证据:5090 `~/dgl-stack/evidence/slip/claw_tip_dz_remeasure.json` 与
# `~/dgl-stack/evidence/taskb/`,privileged-debug 档,2026-08-06。
CLAW_TIP_DZ_OPEN = -0.0035
CLAW_TIP_DZ_CLOSED = 0.01835

# 抓取到位后仍可接受的笛卡尔 xy 残差(mm);超过则试另一个 IK 分支(见 _retry_flipped_branch)。
# 来源:8/6 v4 单世界栈实测——同一个物理抓取,默认分支 xy 误差 15.3 mm、最小关节裕度
# 0.297 rad;绕工具接近轴翻转 180° 后 3.6 mm、0.700 rad。阈值取两次实测之间。
GRASP_XY_RETRY_MM = 8.0

# 抓取定位(含 `_retry_flipped_branch` 翻转兜底)之后仍剩的 xy 残差超过这个量,
# 就判**目标够不着**:记 `unreachable_target` 失败并且**不闭爪**。
# 来源:8/6 ep2 的 2×2 选臂矩阵实测(证据 5090 `~/dgl-stack/evidence/ep2/`),
# 两簇完全分开——同侧够得着:arm0→左管(y=+0.258) **4.9 mm**、arm1→右管
# (y=−0.365) **9.0 mm**;跨身体够不着:**25–69 mm**。阈值取两簇之间。
# 立这条的理由是**失败类要分得开**:此前够不到也照样在空中闭爪,最后由 `lift`
# 以 `attached=empty` 结案,于是「够不到」和「夹了滑掉」两种物理事件被压成同一
# 个 reason,归因无从下手。
UNREACHABLE_XY_MM = 15.0

SERVO_STEP_M, SERVO_STEP_DEG = 0.05, 14.0
SERVO_POS_TOL, SERVO_ROT_TOL = 0.015, 8.0
MP_MIN_DIST_M = 2 * SERVO_STEP_M
SERVO_ITERS = 40
SERVO_STEP_MAX_M = 0.12
SERVO_PROGRESS_EPS_M, SERVO_PROGRESS_EPS_DEG = 0.0015, 0.4
SERVO_PATIENCE = 3

# ---- reorient_held_axis 的闭环参数 ----
# 角度判据**复用伺服自身的旋转容差**:执行器分辨不出比 SERVO_ROT_TOL 更小的角差,
# 单独立一个更严的阈值只会造出永不收敛的循环。同一个数同时当三件事的判据——
# 「已经平行了(不用转)」「转到位了(收敛退出)」「没转到位(如实记 converged=False)」,
# 三者本来就该是同一条线,分开取值会出现「不满足收敛、但也不算未对齐」的空档。
REORIENT_TOL_DEG = SERVO_ROT_TOL
# 迭代上限与 lift 闭环同量级;到顶仍未收敛按实得转角如实记账,不假装成功。
REORIENT_MAX_STEPS = LIFT_MAX_ITERS


# ---------- 工具端四元数运算(xyzw;与 /state 里物体的 wxyz 区分开) ----------
def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def _qaxis(axis, deg):
    h = math.radians(deg) / 2.0
    s = math.sin(h)
    n = math.sqrt(sum(v * v for v in axis)) or 1.0
    return [axis[0] / n * s, axis[1] / n * s, axis[2] / n * s, math.cos(h)]


def _qnorm(q):
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    return [v / n for v in q]


def _qang(a, b):
    """两个姿态之间的测地角(度),对 q/-q 同号不敏感"""
    d = abs(sum(x * y for x, y in zip(_qnorm(a), _qnorm(b))))
    return math.degrees(2 * math.acos(max(-1.0, min(1.0, d))))


def _qslerp(a, b, t):
    a, b = _qnorm(a), _qnorm(b)
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:
        b, d = [-v for v in b], -d
    if d > 0.9995:
        return _qnorm([a[i] + t * (b[i] - a[i]) for i in range(4)])
    th = math.acos(max(-1.0, min(1.0, d)))
    s = math.sin(th)
    return [(math.sin((1 - t) * th) / s) * a[i] + (math.sin(t * th) / s) * b[i] for i in range(4)]


def _tool_axes(q):
    x, y, z, w = _qnorm(q)
    return ([1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)],      # +x 接近轴
            [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)],      # +y 开合轴
            [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)])


def _tdx(psi_deg):
    """竖直朝下 + 绕接近轴(工具 +z)把开合方向转 psi。"""
    return _qnorm(_qmul(TDX0, _qaxis([0, 0, 1], psi_deg)))


def _flip_about_approach(q):
    """绕**工具**接近轴转 180°:得到同一个物理抓取的另一个 IK 分支。

    平行夹爪两指对称,yaw 与 yaw+180 描述的是同一次抓取(指轴同一条直线、只是两指
    互换),但对 IK 是两个不同的解。合成方向必须**右乘**,与 `_tdx` 的
    `_qmul(TDX0, Rz)` 是同一套工具系约定;左乘会变成绕世界轴转,那是另一个姿态。
    """
    axis = [0.0, 0.0, 0.0]
    axis[APPROACH_AXIS_IDX] = 1.0
    return _qnorm(_qmul(list(q), _qaxis(axis, 180.0)))


def _topdown_like(q):
    """返回接近当前腕姿的竖直抓取姿态，并保留腕部 yaw 以降低 IK 冲突。"""
    f = _tool_axes(q)[FINGER_AXIS_IDX]
    return _tdx(math.degrees(math.atan2(f[0], f[1])))


class EvalClient:
    def __init__(self, base_url: str, timeout_s: float = 60.0):
        self.base_url, self.timeout_s = base_url.rstrip("/"), timeout_s

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read())

    def health(self):
        return self._req("GET", "/health")

    def reset(self, task_id: str):
        return self._req("POST", "/session/reset", {"task_id": task_id})

    def state(self):
        return self._req("GET", "/state")


def _flatten(v):
    out = []
    for x in (v if isinstance(v, (list, tuple)) else [v]):
        out.extend(_flatten(x) if isinstance(x, (list, tuple)) else [x])
    return out


# Pipeline 可能返回无逗号的 numpy 字符串；正则解析保证力信号仍可读取。
_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _as_numbers(v):
    """把原语返回值规约成 float 列表,兼容 numpy 的空格分隔字符串形态。
    解析不出任何数字 → 返回 [](调用方据此判"读不到",不 fail-open)。"""
    flat = _flatten(v)
    out = []
    for x in flat:
        if isinstance(x, str):
            out.extend(float(m) for m in _NUM_RE.findall(x))
        else:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                pass
    return out


# MP 熔断的判别串。`PipelineClient` 把 urllib 的 `HTTPError` 原样拼进消息,
# 其 `__str__` 恒为 "HTTP Error <code>: <reason>",所以这是精确匹配而不是搜 "400"
# (坐标里也会出现 400)。
_MP_UNAVAILABLE_MARK = "HTTP Error 400"


def _is_mp_unavailable(exc) -> bool:
    """规划失败是不是「后端不存在/拒绝受理」(HTTP 400)。

    只有 400 才熔断:它表示这条总线上压根没有 `motion_planning_stereo` 后端,
    再试一万次也是同一个结果。超时、传输中断、返回结构异常等**可能是瞬时的**,
    保留原来的逐次 fallback 语义。
    """
    return _MP_UNAVAILABLE_MARK in str(exc)


class OracleRuntime:
    def __init__(self, graph: dict, objects: list | None = None,
                 eval_url="http://127.0.0.1:7480", pipe_url="http://127.0.0.1:8000",
                 arm_id: int = 1, log=None):
        self.graph, self.arm_id = graph, arm_id
        self.eval = EvalClient(eval_url)
        self.pipe = PipelineClient(pipe_url)
        self.registry = objects or []
        self.calls: list[dict] = [] if log is None else log
        self._stages_by_index: dict[int, dict] = {}
        self._holes_by_stage: dict[int, dict[str, dict]] = {}
        for stage in graph.get("stages", []):
            stage_index = int(stage["index"])
            if stage_index in self._stages_by_index:
                raise ValueError(f"duplicate stage index: {stage_index}")
            self._stages_by_index[stage_index] = stage
            holes: dict[str, dict] = {}
            for hole in stage.get("holes", []):
                name = hole["name"]
                if name in holes:
                    raise ValueError(
                        f"duplicate hole {name!r} in stage {stage_index}"
                    )
                holes[name] = hole
            self._holes_by_stage[stage_index] = holes
        self._active_stage_index: int | None = None
        # 持物态:闭爪后为 True,`release` 后回 False。持物期间臂跟随不换(见
        # `_select_arm_for_stage`)。
        self._holding = False
        # MP 熔断闸(同一 episode 一次性):首次 HTTP 400 之后不再调规划。
        self._mp_disabled = False

    def begin_stage(self, stage: dict) -> None:
        """Bind subsequent ``solve(name)`` calls to the current stage."""

        stage_index = int(stage["index"])
        if stage_index not in self._holes_by_stage:
            raise ValueError(f"stage {stage_index} is not declared in graph")
        self._active_stage_index = stage_index
        self._select_arm_for_stage(stage)

    # ---------- 选臂(按目标位置,不按命令行默认值) ----------
    def _stage_target_name(self, stage: dict):
        """这一阶段手臂要去够的物体名:优先被操作物,没有就用参照物。"""
        so = stage.get("stage_objects") or {}
        return so.get("manipulated") or so.get("target")

    def _select_arm_for_stage(self, stage: dict) -> None:
        """按目标实体的 y 符号选臂(机器人系 +y 为左 → arm0,−y 为右 → arm1)。

        立这条的理由是 8/6 ep2 的 2×2 矩阵实测:选臂与目标同侧时 xy 残差
        **4.9 / 9.0 mm**,跨身体时 **25–69 mm**——同一副机械臂,够不够得着完全由
        「目标在哪一侧」决定。此前臂来自命令行 `--arm`(默认 1=右),目标在左时
        就是 31 mm 够不着,还照样往下走。

        三条克制:
        ① **持物期间不换臂**——手上有东西时换臂等于把物体丢在半空,只有 `release`
           之后才允许重选;
        ② **死区内保持当前臂**——|y| < ``ARM_SELECT_DEADZONE_Y_M`` 时左右分不开,
           保持现状,不猜;
        ③ **解析不到目标就保持当前臂**并记原因,不 fail-open 成「随便挑一只」。

        换臂时用现有 ``_park_idle_arm`` 把刚空出来的那条臂归位(它按新的
        ``arm_id`` 取闲臂,所以必须在赋值之后调用)。
        """
        if self._holding:
            self._log("arm_select", arm=self.arm_id, switched=False,
                      reason="holding_object")
            return
        name = self._stage_target_name(stage)
        if not name:
            self._log("arm_select", arm=self.arm_id, switched=False,
                      reason="no_stage_object")
            return
        try:
            y = float(self._ent(name)["pos"][1])
        except Exception as e:
            self._log("arm_select", arm=self.arm_id, switched=False, obj=str(name),
                      reason=f"unresolved:{type(e).__name__}")
            return
        if abs(y) < ARM_SELECT_DEADZONE_Y_M:
            self._log("arm_select", arm=self.arm_id, switched=False, obj=str(name),
                      y=round(y, 4), reason="deadzone_keep_current")
            return
        want = ARM_LEFT if y > 0 else ARM_RIGHT
        prev = self.arm_id
        self.arm_id = want
        self._log("arm_select", arm=want, prev_arm=prev, switched=want != prev,
                  obj=str(name), y=round(y, 4), reason="target_y_sign")
        if want != prev:
            self._park_idle_arm()

    # ---------- 日志 ----------
    def _log(self, op, **kw):
        self.calls.append({"t": round(time.time(), 2), "op": op, **kw})

    # ---------- 实体解析(oracle) ----------
    def _entities(self, max_age_s: float = 0.4):
        """/state 每次都会重算谓词,短 TTL 缓存避免风暴。"""
        now = time.time()
        if not hasattr(self, "_ents_cache") or now - self._ents_cache[0] > max_age_s:
            self._ents_cache = (now, self.eval.state().get("entities", {}))
        return self._ents_cache[1]

    # 语义同义词组:图里的通用名 → 场景实体的命名习惯
    SYNONYMS = [
        ("rack", "holder", "slot", "stand", "socket", "bank", "piggy"),
        ("table", "desk", "surface", "workspace"),
        ("tube", "test_tube", "vial"),
        ("bowl", "dish"), ("coin", "disc"), ("pad", "marker", "target_zone"),
        ("block", "cube", "tblock", "t_block"),
    ]

    # 空间词 → y 序偏好(机器人系 +y 为左)。用于同类多实体的稳定双射分配。
    _SPATIAL = {"left": +1.0, "leftmost": +1.5, "right": -1.0, "rightmost": -1.5,
                "mid": 0.0, "middle": 0.0, "center": 0.0, "centre": 0.0,
                "top": 0.0, "upper": 0.0, "bottom": 0.0, "lower": 0.0}

    def _family(self, base: str, ents: dict) -> list:
        """与 base 同类的实体键(按同义词组或前缀词命中),按 y 排序。"""
        cat = None
        for group in self.SYNONYMS:
            if any(w in base for w in group):
                cat = group
                break
        if cat:
            fam = [e for e in ents if any(w in e.lower() for w in cat)]
        else:
            head = base.split("_")[0]
            fam = [e for e in ents if head and head in e.lower()]
        return sorted(fam, key=lambda e: ents[e]["pos"][1])   # 左(+y)→右(-y)? 见下

    def _graph_object_names(self) -> list:
        """从图的 stage_objects 收集所有被引用的物体名(去重,保序)。"""
        seen, out = set(), []
        for st in self.graph.get("stages", []):
            so = st.get("stage_objects") or {}
            for key in ("manipulated", "target"):
                v = so.get(key)
                if v and v not in seen:
                    seen.add(v)
                    out.append(v)
        return out

    def _spatial_key(self, base: str) -> float:
        """名字里空间词的左右得分(越大越靠左/+y)。无空间词返回 0。"""
        toks = [t for t in base.replace("-", "_").split("_") if t in self._SPATIAL]
        return sum(self._SPATIAL[t] for t in toks)

    # 两个同类实体的 y 相差不到这个量就认为左右分不开(双射无解)。
    BIJECTION_Y_TOL_M = 0.01

    def _family_bijection(self, base: str, ents: dict):
        """同类图对象 ↔ 同类场景实体的空间双射;定不下来时返回 ``None``。

        唯一依据是左右次序:图名一侧按名字里的空间词得分排,实体一侧按 y 排
        (机器人系 +y 为左)。以下三种情况没有确定的对应关系,一律返回 ``None``
        让调用方拒绝,不留"取第一个"或"多个图名共用一个实体"的静默出口:

        ① 两侧基数不等——基数不等就不存在双射。旧实现用 ``min(i, len-1)`` 截断,
           图名多于实体时会把多出来的名字全部塌到最后一个实体;
        ② 图名的空间得分有并列——并列时次序只能靠字典序决定,那是猜;
        ③ 实体 y 并列(相差 < ``BIJECTION_Y_TOL_M``)——左右分不开。
        """
        cat_key = None
        for group in self.SYNONYMS:
            if any(w in base for w in group):
                cat_key = group
                break
        head = base.split("_")[0]
        cache = getattr(self, "_bij_cache", None)
        if cache is None:
            cache = self._bij_cache = {}
        ckey = cat_key or head
        if ckey in cache:
            return cache[ckey]
        # 同类图名
        def _same_cat(nm):
            nb = nm.split(".")[0].lower()
            if cat_key:
                return any(w in nb for w in cat_key)
            return head and head in nb
        names = [n for n in self._graph_object_names() if _same_cat(n)]
        fam = self._family(base, ents)   # 已按 y 排序(左+y→右-y? 见 _family)
        fam_l2r = sorted(fam, key=lambda e: -ents[e]["pos"][1])   # 左(+y)→右(-y)
        keys = [self._spatial_key(n.split(".")[0].lower()) for n in names]
        ys = [ents[e]["pos"][1] for e in fam_l2r]
        mapping = None
        if (names and len(names) == len(fam_l2r)
                and len(set(keys)) == len(keys)
                and all(ys[i] - ys[i + 1] >= self.BIJECTION_Y_TOL_M
                        for i in range(len(ys) - 1))):
            # 图名按空间得分从左到右排(得分大=左);得分已保证互异,无需字典序兜底。
            names_l2r = sorted(
                names, key=lambda n: -self._spatial_key(n.split(".")[0].lower()))
            mapping = dict(zip(names_l2r, fam_l2r))
        cache[ckey] = mapping
        return mapping

    def _bijection_hit(self, name: str, base: str, ents: dict):
        """同类双射里 ``name`` 对应的实体键;双射定不下来或没这个名字 → ``None``。"""
        mapping = self._family_bijection(base, ents)
        if not mapping:
            return None
        return (mapping.get(name) or mapping.get(base)
                or mapping.get(str(name).split(".")[0]))

    def _resolve(self, name: str) -> str:
        """registry id/物体名 → /state 实体键。
        启发式顺序:精确 → 别名 → 子串 → **同类双射(空间词)** → 同义词兜底。

        别名/子串/同义词三个分支只在**唯一命中**时直取。命中多个实体说明这个名字
        本身分不开(ep1 三根管的 ``trace_aliases`` 都是 ``["tube"]``,取第一个会把
        ``tube_left/tube_right/tube_third`` 全部塌进 ``tube0_prop``),此时降级到
        空间双射;双射也定不下来就抛 ``UnsolvedHole``,宁拒绝不静默。
        """
        ents = self._entities()
        if name in ents:
            return name
        base = str(name).split(".")[0].lower()

        def _pick(candidates, branch):
            """唯一命中直取;多命中降级到空间双射;双射也定不下来 → 拒绝。"""
            candidates = sorted(set(candidates))
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]
            hit = self._bijection_hit(name, base, ents)
            if hit is not None:
                return hit
            raise binding.UnsolvedHole(
                f"cannot resolve object {name!r}: {branch} 命中多个实体 "
                f"{candidates},同类空间双射也定不下来",
                reason="ambiguous_object_reference")

        alias_candidates = []
        for reg in self.registry:  # registry 的 trace_aliases 桥接
            if reg.get("id", "").lower() == base:
                for alias in reg.get("trace_aliases", []):
                    alias_candidates.extend(
                        e for e in ents if alias.lower() in e.lower())
        hit = _pick(alias_candidates, "trace_alias")
        if hit:
            return hit
        hit = _pick([e for e in ents          # 子串(如 bowl0 ↔ bowl0_prop)
                     if base in e.lower() or e.lower().replace("_prop", "") in base],
                    "substring")
        if hit:
            return hit
        # 同类双射:名字含空间词、且同类图物体 >=2 个时,用缓存双射拿到唯一实体。
        toks = [t for t in base.replace("-", "_").split("_") if t in self._SPATIAL]
        if toks:
            hit = self._bijection_hit(name, base, ents)
            if hit:
                return hit
        for group in self.SYNONYMS:  # 同义词组兜底
            if any(w in base for w in group):
                hit = _pick([e for e in ents
                             if any(w in e.lower() for w in group)], "synonym")
                if hit:
                    return hit
        raise KeyError(f"cannot resolve object {name!r} among {sorted(ents)}")

    def _ent(self, name: str) -> dict:
        return self._entities()[self._resolve(name)]

    # ---------- contract: 感知求解 ----------
    def solve(self, hole_name: str):
        """Resolve a hole declared by the active stage; never guess a fallback."""
        matches = []
        if self._active_stage_index is not None:
            holes = self._holes_by_stage.get(self._active_stage_index, {})
            if hole_name in holes:
                stage = self._stages_by_index[self._active_stage_index]
                matches.append((stage, holes[hole_name]))
        else:
            for stage in self.graph.get("stages", []):
                hole = self._holes_by_stage.get(int(stage["index"]), {}).get(hole_name)
                if hole is not None:
                    matches.append((stage, hole))

        if not matches:
            raise binding.UnsolvedHole(
                f"solve: 图中无声明的 hole {hole_name!r}"
                f"(stage={self._active_stage_index!r})",
                reason="hole_not_declared")
        if len(matches) > 1:
            raise binding.UnsolvedHole(
                f"solve: hole {hole_name!r} 跨阶段重名，runner 未设置 active stage",
                reason="ambiguous_hole")
        st, hole = matches[0]
        val = binding.solve_hole(hole, stage=st, constraints=st.get("constraints") or [],
                                 rt=self)
        self._log("solve", hole=hole_name, kind=val["kind"])
        return val

    # ---------- contract: 控制原语(pipeline ctrl 透传) ----------
    def _ctrl(self, fn: str, arm_id=None, **kw):
        """下发控制请求；调用方必须通过状态回读确认动作效果。"""
        self._log("ctrl", fn=fn, arm=self.arm_id if arm_id is None else arm_id,
                  **{k: v for k, v in kw.items() if k != "target_quat"})
        return self.pipe.call("ctrl", fn, {"arm_id": self.arm_id if arm_id is None else arm_id,
                                           **kw})

    def _cur_xquat(self):
        x = self.pipe.call("info", "get_xquat", {"arm_id": self.arm_id})
        return list(x[:3]), list(x[3:7])

    def _ee_extforce_max(self):
        """末端外力标量(非特权信号):取各分量绝对值的最大值。读不到 → None。
        标定观测为空载约 1.1 N、触底约 57 N。"""
        try:
            f = self.pipe.call("info", "get_ee_extforce", {"arm_id": self.arm_id})
            nums = _as_numbers(f)          # 兼容 numpy 空格分隔字符串,见 _as_numbers
            return max(abs(v) for v in nums) if nums else None
        except Exception:
            return None

    def _grip_angle(self):
        """夹爪当前开合角(0=闭 100=开)。**唯一可信的开合回读**:
        get_sensor_info(key="angle") 的第 ARM_JOINT_NUM(=7) 位。
        /state 的 robot_qpos 爪子分量对 set_gripper 无响应,不能用。读不到 → None。"""
        try:
            return round(float(self.pipe.call(
                "info", "get_sensor_info", {"arm_id": self.arm_id, "key": "angle"})[7]), 3)
        except Exception:
            return None

    def _is_gripping(self):
        """是否夹住了东西(非特权)。语义见 motor_node.Gripper.is_gripping:
        朝闭合方向走 且 电流达限幅 且 未到目标角 → 指垫被物体卡住。读不到 → None。"""
        try:
            return bool(self.pipe.call("info", "is_gripping_sth", {"arm_id": self.arm_id}))
        except Exception:
            return None

    def _wait_grip(self, target, timeout_s=GRIP_SETTLE_S, tol=GRIP_ANGLE_TOL_DEG):
        """等待夹爪到达目标角或被物体挡住；回读不可用时等待到超时。"""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            time.sleep(0.2)
            a = self._grip_angle()
            if a is None:                     # 回读不可用时等待到超时
                continue
            if abs(a - target) <= tol or self._is_gripping():
                return round(time.time() - t0, 2)
        return round(time.time() - t0, 2)

    def _verify_moved(self, before, xyz=None, quat=None, tol=SERVO_POS_TOL,
                      rot_tol=SERVO_ROT_TOL, op=""):
        """唯一可信的成功判据:笛卡尔回读。before 是动作前的 (xyz, quat)。
        返回 (到位?, 实际位移 m, 实际转角 deg)。"""
        p, q = self._cur_xquat()
        moved = math.dist(p, before[0])
        turned = _qang(q, before[1])
        ok = True
        if xyz is not None:
            ok = ok and math.dist(p, list(xyz)) <= tol
        if quat is not None:
            ok = ok and _qang(q, quat) <= rot_tol
        self._log("verify_moved", src=op, ok=ok, moved=round(moved, 4), turned=round(turned, 2),
                  pos_gap=round(math.dist(p, list(xyz)), 4) if xyz is not None else None,
                  rot_gap=round(_qang(q, quat), 2) if quat is not None else None)
        return ok, moved, turned

    def _park_idle_arm(self):
        """尽力将空闲臂归位以减少双臂碰撞；归位结果不作为阶段成功条件。"""
        idle = IDLE_ARM.get(self.arm_id)
        if idle is None:
            return
        self._ctrl("go_home", arm_id=idle)
        # 等待空闲臂静止，最多 6 秒。
        self._wait_settle(arm_id=idle, timeout_s=6.0)

    def _arm_qpos(self, arm_id=None):
        """某条臂的 7 个关节角(默认本臂),取自 pipeline ``info:get_qpos``——**按臂**的
        既有接口,与 robot_api 的收敛核对同源,是被验证过的关节真值。

        **不要**改回从 eval ``/state`` 的 ``robot_qpos`` 切片。那条旧路径假设两臂交错
        (左偶右奇,``[a::2][:7]``),8/6 v4 单世界栈实测证伪:``robot_qpos`` 长度 29,
        右臂的真实下标是 ``1,3,6,9,11,13,15``(间隔 +2/+3/+3/+2/+2/+2,并不等距)。
        判别依据是物理不可能性——交错切片取出的 j6 = -2.1813,落在该关节自身限位
        [-1.308, +1.570] 之外,关节不可能越过自己的限位;同一瞬间 ``get_qpos`` 返回的
        7 元组全部在限位内。布局随机器人代次变化,这里不猜索引:读不到规定形状就抛错,
        由调用方按"读不到"处理,不返回错值。
        """
        a = self.arm_id if arm_id is None else arm_id
        q = [float(v) for v in self.pipe.call("info", "get_qpos", {"arm_id": a})]
        if len(q) != robot_api.JOINTS_PER_WAYPOINT:      # 7-DoF 单臂
            raise ValueError(
                f"get_qpos(arm={a}) 返回 {len(q)} 个关节,"
                f"期望 {robot_api.JOINTS_PER_WAYPOINT}")
        return q

    def _wait_settle(self, target_xyz=None, tol=0.012, timeout_s=25.0, still_n=3,
                     arm_id=None):
        """通过 ``/state`` 的关节角等待指定手臂静止；默认等待当前手臂。"""
        t0, last, still = time.time(), None, 0
        while time.time() - t0 < timeout_s:
            time.sleep(0.4)
            try:
                q = self._arm_qpos(arm_id)
            except Exception:
                continue
            if last is not None and max(abs(a - b) for a, b in zip(q, last)) < 0.004:
                still += 1
                if still >= still_n:
                    if target_xyz is not None:
                        gap = math.dist(self._cur_xquat()[0], list(target_xyz))
                        self._log("settle", reason="still", sec=round(time.time() - t0, 1),
                                  gap=round(gap, 4))
                        return "reached" if gap < tol else "still"
                    self._log("settle", reason="still", sec=round(time.time() - t0, 1))
                    return "still"
            else:
                still = 0
            last = q
        self._log("settle", reason="timeout", sec=round(time.time() - t0, 1))
        return "timeout"

    def _step_to(self, xyz, gpos=None):
        """纯位置闭环趋近:每步 delta_move <=SERVO_STEP_M,姿态交给 IK 自然漂移。
        用在"还不知道该锁什么腕姿"的阶段(比如先靠近物体上方,再据此挑姿态)。"""
        stall = 0
        for _ in range(SERVO_ITERS * 2):
            cur, _ = self._cur_xquat()
            d = [xyz[i] - cur[i] for i in range(3)]
            gap = math.sqrt(sum(v * v for v in d))
            if gap < SERVO_POS_TOL:
                return True
            n = gap or 1e-9
            delta = [round(d[i] / n * min(SERVO_STEP_M, gap), 4) for i in range(3)]
            kw = {"delta_xyz": delta}
            if gpos is not None:
                kw["gpos"] = gpos
            before = self._cur_xquat()
            self._ctrl("delta_move", **kw)
            self._wait_settle(timeout_s=14.0)
            _, moved, _ = self._verify_moved(before, op="step_to")
            # 连续多步缺少有效进展才判定卡住。
            if moved < SERVO_PROGRESS_EPS_M:
                stall += 1
                if stall >= SERVO_PATIENCE:
                    self._log("step_to", reason="stalled",
                              gap=round(math.dist(self._cur_xquat()[0], list(xyz)), 4))
                    return False
            else:
                stall = 0
        self._log("step_to", reason="budget",
                  gap=round(math.dist(self._cur_xquat()[0], list(xyz)), 4))
        return False

    def _move(self, xyz, quat=None, interpolation="linear", gpos=None):
        """位姿移动入口。

        长距离优先使用运动规划；规划失败时转入限幅伺服并记录 ``mp_fallback``。
        短距离直接使用局部伺服。``quat=None`` 时选择接近当前腕姿的竖直姿态。

        规划一旦以 HTTP 400 失败,同一 episode 内**熔断**不再调用(见
        ``_is_mp_unavailable``)。8/6 ep2 实测:隔离总线上没有
        ``motion_planning_stereo`` 后端,每次调用要白等 400+20 s,单集烧掉 200 s
        (**29% wall**),100% 失败后全部走 degraded 伺服——重试没有任何信息增量,
        只在买同一个已知答案。非 400 的失败**不熔断**,保留原逐次 fallback 语义。
        """
        p0, q0 = self._cur_xquat()
        tq = _topdown_like(q0) if quat is None else list(quat)
        target_pose = [float(v) for v in list(xyz)[:3]] + [float(v) for v in tq]
        # 短距离处于当前构型邻域，直接伺服并单独记为 move_local。
        dist0 = math.dist(p0, [float(v) for v in list(xyz)[:3]])
        if dist0 < MP_MIN_DIST_M and _qang(q0, tq) <= SERVO_ROT_TOL:
            self._log("move_local", dist=round(dist0, 4), reason="short_range")
            return self._move_servo(xyz, quat=quat, interpolation=interpolation, gpos=gpos)
        if self._mp_disabled:
            self._log("mp_fallback", reason="mp_disabled_after_400", degraded=True)
            return self._move_servo(xyz, quat=quat, interpolation=interpolation, gpos=gpos)
        try:
            plan = robot_api.plan_joint_path(self, self.arm_id, target_pose,
                                            planning_mode="cartesian_goal")
        except robot_api.PlanFailed as e:
            if _is_mp_unavailable(e):
                self._mp_disabled = True
                self._log("mp_disabled_after_400", err=str(e)[:160])
            self._log("mp_fallback", reason=getattr(e, "reason", None) or "plan_failed",
                      err=str(e)[:160], degraded=True)
            return self._move_servo(xyz, quat=quat, interpolation=interpolation, gpos=gpos)
        ex = robot_api.execute_path(self, plan.waypoints, arm=self.arm_id, gpos=gpos)
        p, q = self._cur_xquat()
        pos_gap, rot_gap = math.dist(p, list(xyz)), _qang(q, tq)
        ok = pos_gap <= SERVO_POS_TOL and rot_gap <= SERVO_ROT_TOL
        self._log("move_mp", n_waypoints=plan.n_waypoints, n_converged=ex.n_converged,
                  reached=ex.reached, ok=ok, pos_gap=round(pos_gap, 4),
                  rot_gap=round(rot_gap, 2))
        if ok:
            return True
        # 关节收敛不保证笛卡尔到位；用笛卡尔伺服补齐残差并记为 mp_refine。
        self._log("mp_refine", pos_gap=round(pos_gap, 4), rot_gap=round(rot_gap, 2),
                  reason="joint_converged_but_cartesian_off")
        return self._move_servo(xyz, quat=quat, interpolation=interpolation, gpos=gpos)

    def _move_servo(self, xyz, quat=None, interpolation="linear", gpos=None):
        """限幅笛卡尔闭环伺服，用于短距离移动和规划失败后的退化路径。

        每轮限制平移与旋转步长，等待状态回读后再计算下一步。``quat=None`` 时
        选择接近当前腕姿的竖直姿态。
        """
        no_progress, best_dp, best_dr, eff = 0, None, None, 1.0
        for i in range(SERVO_ITERS):
            p, q = self._cur_xquat()
            tq = _topdown_like(q) if quat is None else list(quat)
            dp, dr = math.dist(p, list(xyz)), _qang(q, tq)
            if dp <= SERVO_POS_TOL and dr <= SERVO_ROT_TOL:
                self._log("move", reason="reached", i=i,
                          pos_gap=round(dp, 4), rot_gap=round(dr, 2))
                return True
            # 进展判据：剩余距离或角度是否继续缩小。
            improved = ((best_dp is None)
                        or (best_dp - dp) > SERVO_PROGRESS_EPS_M
                        or (best_dr - dr) > SERVO_PROGRESS_EPS_DEG)
            best_dp = dp if best_dp is None else min(best_dp, dp)
            best_dr = dr if best_dr is None else min(best_dr, dr)
            if improved:
                no_progress = 0
            else:
                no_progress += 1
                if no_progress >= SERVO_PATIENCE:
                    self._log("move", reason="no_progress", i=i,
                              pos_gap=round(dp, 4), rot_gap=round(dr, 2))
                    return False
            step_m = min(SERVO_STEP_MAX_M, SERVO_STEP_M / max(eff, 0.15))
            f = min(1.0, step_m / dp) if dp > 1e-9 else 1.0
            t = min(1.0, SERVO_STEP_DEG / dr) if dr > 1e-9 else 1.0
            kw = {"target_xyz": [round(p[j] + (xyz[j] - p[j]) * f, 4) for j in range(3)],
                  "target_quat": [round(v, 6) for v in _qslerp(q, tq, t)],
                  "interpolation": interpolation}
            if gpos is not None:
                kw["gpos"] = gpos
            cmd_m = math.dist(p, kw["target_xyz"])
            self._ctrl("xquat_move", **kw)
            self._wait_settle(target_xyz=kw["target_xyz"], timeout_s=18.0)
            _, moved, turned = self._verify_moved((p, q), op="move")
            if cmd_m > 1e-4:   # 在线估计行程效率,用于补偿指令步长
                eff = 0.6 * eff + 0.4 * max(0.05, min(1.0, moved / cmd_m))
        p, q = self._cur_xquat()
        ok = math.dist(p, list(xyz)) <= SERVO_POS_TOL
        self._log("move", reason="budget", ok=ok, pos_gap=round(math.dist(p, list(xyz)), 4))
        return ok

    def _target_xyz(self, target):
        if isinstance(target, dict) and "xyz" in target:
            return list(target["xyz"])
        e = self._ent(target if isinstance(target, str) else str(target))
        return list(e["pos"])

    # ---------- 契约参数消费 / 显式记账 ----------
    def _unsupported(self, param, value, reason):
        """记录当前 runtime 无法映射为行为的契约参数。"""
        self._log("unsupported_param", param=param, value=repr(value), reason=reason)

    def _consume_obj(self, obj, *, op):
        """解析被操作物并记录结果；无法解析时记录 UNSUPPORTED。"""
        if obj is None:
            return None
        try:
            ent = self._ent(obj if isinstance(obj, str) else str(obj))
        except Exception as e:
            self._unsupported(f"{op}.obj", obj, f"unresolved:{type(e).__name__}")
            return None
        self._log("obj_resolved", prim=op, obj=str(obj),
                  pos=[round(v, 4) for v in ent["pos"]])
        return ent

    def _axis_vec(self, axis):
        """把 align 的 `axis` 参数规约成世界系方向向量。
        接受三种形态:①binding.solve_axis_3d 的句柄 {"kind":"axis","vec":[...]};
        ②裸 3 向量 [x,y,z] 或 dict{"vec":...};③None/字符串标签。
        取不到向量(None、字符串、零向量)→ 返回 None(调用方据此 UNSUPPORTED 记账)。"""
        if axis is None:
            return None
        vec = None
        if isinstance(axis, dict):
            vec = axis.get("vec")
        elif isinstance(axis, (list, tuple)) and len(axis) == 3:
            vec = list(axis)
        if not vec:
            return None
        try:
            v = [float(vec[0]), float(vec[1]), float(vec[2])]
        except (TypeError, ValueError, IndexError):
            return None
        if math.sqrt(sum(c * c for c in v)) < 1e-9:
            return None
        return v

    def _align_quat(self, axis):
        """让工具开合方向与 ``axis`` 的水平投影平行。

        轴缺失或近竖直时无法确定 yaw，返回 ``None`` 和原因。
        """
        v = self._axis_vec(axis)
        if v is None:
            return None, "no_axis_vec"
        horiz = math.sqrt(v[0] * v[0] + v[1] * v[1])
        if horiz < 1e-6:
            # 轴近竖直:与接近轴共线,无法据此约束 yaw 自由度。
            return None, "axis_vertical_yaw_unconstrained"
        psi = math.degrees(math.atan2(v[0], v[1]))
        return _tdx(psi), "yaw_from_axis"

    def _grasp_quat(self, axis):
        """让工具开合方向与 ``axis`` 的水平投影正交。"""
        v = self._axis_vec(axis)
        if v is None:
            return None, "no_axis_vec"
        if math.sqrt(v[0] * v[0] + v[1] * v[1]) < 1e-6:
            # 轴近竖直时，竖直下探已满足正交关系。
            return None, "axis_vertical_topdown_already_orthogonal"
        psi = math.degrees(math.atan2(v[0], v[1]))
        return _tdx(psi + 90.0), "yaw_orthogonal_to_axis"

    # 任务无关的候选 approach 方向调色板(单位向量,世界系)。**与 cone 无关地生成**:
    # cone 只在排序步进入，不能参与候选生成。
    # 覆盖竖直下探 / 四个水平朝向 / 四个斜向,足以让任一 cone 排序都有可分的 top-1。
    _APPROACH_DIR_CANDIDATES = (
        {"id": "down",   "approach_dir": [0.0, 0.0, -1.0]},
        {"id": "east",   "approach_dir": [1.0, 0.0, 0.0]},
        {"id": "west",   "approach_dir": [-1.0, 0.0, 0.0]},
        {"id": "north",  "approach_dir": [0.0, 1.0, 0.0]},
        {"id": "south",  "approach_dir": [0.0, -1.0, 0.0]},
        {"id": "obl_e",  "approach_dir": [1.0, 0.0, -1.0]},
        {"id": "obl_w",  "approach_dir": [-1.0, 0.0, -1.0]},
        {"id": "obl_n",  "approach_dir": [0.0, 1.0, -1.0]},
        {"id": "obl_s",  "approach_dir": [0.0, -1.0, -1.0]},
    )

    def _cone_name(self, cone):
        """把字符串或约束 args 字典规约成封闭词表中的锥名。

        这里只归一输入形状，不加入任务分支。无法取得锥名时返回 ``None``。
        """
        if cone is None:
            return None
        if isinstance(cone, str):
            return cone
        if isinstance(cone, dict):
            inner = cone.get("cone")
            if isinstance(inner, str):
                return inner
        return None

    def approach(self, target, cone=None):
        """先归位空闲臂，再移动到预抓取位并调整腕姿。

        `cone` 只参与排序:候选 approach 方向由任务无关调色板生成(不看 cone),
        再用 regions.rank_by_cone 按 cone 偏好(与锥轴夹角越小越优)排序,top-1 方向决定
        预抓取偏置的朝向。cone=None 时采用正上方下探。候选生成不消费 cone。
        """
        self._park_idle_arm()
        xyz = self._target_xyz(target)
        off = PREGRASP_DZ + CLAW_TIP_DZ_OPEN    # 预抓取时爪子还张着
        cone_name = self._cone_name(cone)       # 形状归一:policy 传的是约束 args 整块
        if cone_name is None:
            if cone is not None:                # 给了 cone 却取不出锥名 → 记账,不静默当无 cone
                self._unsupported("approach.cone", cone, "no_cone_name")
            xyz[2] += off                       # 无 cone:沿用竖直下探(向下 → 偏置在正上方)
        else:
            # 候选与 cone 无关地生成;cone 仅在此排序步进入。
            best = regions.rank_by_cone(self._APPROACH_DIR_CANDIDATES, cone_name)[0]
            d = best["approach_dir"]
            n = math.sqrt(sum(v * v for v in d)) or 1.0
            u = [v / n for v in d]
            # 沿 approach 反方向偏置；方向来自排序，幅度使用标定常量。
            xyz = [xyz[i] - u[i] * off for i in range(3)]
            self._log("approach_cone", cone=cone_name, dir=best["id"])
        self._step_to(xyz)
        return self._move(xyz)

    def _xy_err_mm(self, xyz):
        """当前 EEF 与目标点的**水平**残差(mm)。z 另有下探与爪尖补偿,不进这个判据。"""
        p, _ = self._cur_xquat()
        return math.hypot(p[0] - xyz[0], p[1] - xyz[1]) * 1000.0

    def _retry_flipped_branch(self, eef, quat):
        """抓取到位残差过大时,换 IK 的另一个分支重试一次,停在实测更好的那支。

        触发判据只用**到位后实测的 xy 残差**,不查关节裕度:仓内没有限位表,
        重试-on-error 是零新依赖的 v1。拿到限位表后可升级成"先比最小关节裕度、
        再决定用哪支",那样能省掉一次移动(8/6 v4 实测里裕度差了一倍:
        0.297 rad vs 0.700 rad,比 xy 残差更早可判)。
        """
        err = self._xy_err_mm(eef)
        if err <= GRASP_XY_RETRY_MM:
            self._log("grasp_branch", branch="default", retried=False,
                      xy_err_mm=round(err, 1))
            return
        flipped = _flip_about_approach(quat)
        self._move(eef, quat=flipped, gpos=GRIP_OPEN)
        err_flipped = self._xy_err_mm(eef)
        if err_flipped < err:
            self._log("grasp_branch", branch="flipped", retried=True,
                      xy_err_mm=round(err_flipped, 1),
                      default_xy_err_mm=round(err, 1),
                      flipped_xy_err_mm=round(err_flipped, 1),
                      reason="xy_err_over_threshold")
            return
        # 翻转分支更差:退回默认分支,不在更差的构型上闭爪。
        self._move(eef, quat=quat, gpos=GRIP_OPEN)
        self._log("grasp_branch", branch="default", retried=True, restored=True,
                  xy_err_mm=round(self._xy_err_mm(eef), 1),
                  default_xy_err_mm=round(err, 1),
                  flipped_xy_err_mm=round(err_flipped, 1),
                  reason="flip_not_better")

    def _log_grasp_evidence(self, p_pre):
        """记 ``grasp_point`` 与 ``approach_dir`` 两条 gate ctx 证据(闭爪前的定位完成时刻)。

        两条**都取回读实得量,不取命令值**。这是已定裁决:gate 若拿到的是自己这条
        链选定的命令方向,就变成「验证自己选的值」,恒 PASS,没有牙齿。

        - ``grasp_point``:世界系**爪尖**点。`pred_region_grasp` 拿它的 z 去和物体
          AABB 的竖直跨度比,所以必须是爪尖而不是 EEF 原点——管子直径才 33.6 mm,
          3.5 mm 的偏移就是归一化坐标 s 的 10%。EEF 回读 z 减去张爪指尖偏移即爪尖
          (与 `grasp_at` 里 `eef = tip + CLAW_TIP_DZ_OPEN` 互逆)。
        - ``approach_dir``:**实测达成**的接近方向 = 下探段起止两次 `get_xquat` 的
          位置差归一化。取的是**下探段**(预抓取位 → 抓取位)而不是 `approach` 原语
          那一段:`approach` 走的是到「站位点」的转移,而站位点恰恰是沿接近方向的
          **反向**偏置出来的,那一段位移的方向与「从哪个方向接近物体」无关(它只取
          决于上一阶段把手臂停在了哪儿)。`regions.cone_angle_deg` 量的是方向相对
          竖直向下的倾角,用转移段会把一次正常的竖直抓取算成 ~90° 倾角、把
          `top_down` 判成 FAIL。位移小到测不出方向时如实记 ``dir=None``
          (谓词据此给 UNKNOWN,不 fail-open)。
        """
        p, _ = self._cur_xquat()
        self._log("grasp_point",
                  xyz=[round(p[0], 4), round(p[1], 4),
                       round(p[2] - CLAW_TIP_DZ_OPEN, 4)])
        d = [p[i] - p_pre[i] for i in range(3)]
        n = math.sqrt(sum(v * v for v in d))
        if n < SERVO_PROGRESS_EPS_M:
            self._log("approach_dir", dir=None, reason="no_measurable_displacement")
            return
        self._log("approach_dir", dir=[round(v / n, 4) for v in d],
                  source="measured_descent")

    def grasp_at(self, grasp_pose, axis=None):
        """grasp_pose 给的是**爪尖**要到的世界点;EEF 帧要比它高 CLAW_TIP_DZ_OPEN
        (下探时爪子是张开的,定位常数必须与下探时的指尖同状态)。

        若提供物体长轴 ``axis``，工具开合方向与其水平投影正交。轴缺失或
        近竖直时保持当前腕姿，并在无法消费参数时记录 UNSUPPORTED。
        下探到位后 xy 残差超过 ``GRASP_XY_RETRY_MM`` 时翻转一次 IK 分支再闭爪，
        见 ``_retry_flipped_branch``。翻转兜底之后残差仍超过
        ``UNREACHABLE_XY_MM`` 说明这条臂**够不到**目标:记 ``unreachable_target``
        并**直接返回,不闭爪**——在空中闭一次爪只会把「够不到」伪装成「夹空了」。
        定位完成、闭爪之前记 ``grasp_point`` 与 ``approach_dir`` 两条 gate ctx
        证据(都取回读实得量),见 ``_log_grasp_evidence``。
        """
        xyz = list(grasp_pose["xyz"]) if isinstance(grasp_pose, dict) else list(grasp_pose)
        eef = [xyz[0], xyz[1], xyz[2] + CLAW_TIP_DZ_OPEN]
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        self._wait_grip(GRIP_OPEN)
        self._move([eef[0], eef[1], eef[2] + PREGRASP_DZ])
        p_pre = self._cur_xquat()[0]      # 下探起点(回读实得,不是命令值)
        gq, why = self._grasp_quat(axis)
        if gq is None:
            if axis is not None:
                self._unsupported("grasp_at.axis", axis, why)
            _, gq = self._cur_xquat()             # 退回:锁住已到位的腕姿
        else:
            self._log("grasp_axis", why=why, quat=[round(v, 4) for v in gq])
        self._move(eef, quat=gq, gpos=GRIP_OPEN)  # 下探时锁住抓取腕姿,只走 z
        self._retry_flipped_branch(eef, gq)       # xy 残差过大 → 试对称的另一个 IK 分支
        # 翻转也救不回来 → 这条臂够不到,记独立失败类并停在这里(不闭爪)。
        err = self._xy_err_mm(eef)
        if err > UNREACHABLE_XY_MM:
            self._log("grasp_failed", reason="unreachable_target", arm=self.arm_id,
                      xy_err_mm=round(err, 1), threshold_mm=UNREACHABLE_XY_MM,
                      closed=False)
            return
        self._log_grasp_evidence(p_pre)
        # !! 参数名只能是 angle:gpos 传给 set_gripper 会被静默丢弃且仍回 ok=True。
        # 开合方向见模块顶部 GRIP_* 注释:
        # 闭合 = 往 **更小** 的 angle 走,GRIP_CLOSE=0 才是全闭。
        self._ctrl("set_gripper", angle=GRIP_CLOSE_TUBE)
        self._wait_grip(GRIP_CLOSE_TUBE)
        # 闭过爪就算持物态:此后到 `release` 之前不换臂。这里**不看**夹持回读——
        # 「夹到没夹到」由 lift 的承重证据判,而不论夹没夹到,换臂都是危险动作。
        self._holding = True
        # 记录角度和夹持回读；抓取结果由 lift 的承重证据和 gate 判定。
        self._log("grasp_close", target=GRIP_CLOSE_TUBE,
                  angle=self._grip_angle(), gripping=self._is_gripping())

    def lift(self, obj):
        """闭环抬升到 ``LIFT_DZ``，并用非特权信号记录夹持证据。

        语义是**达到目标高度**，不是「发够步数」:每轮回读 EEF 高度(非特权
        ``get_xquat``)算出剩余量,剩余量 ≤ ``LIFT_TOL_M`` 即收敛退出,否则按剩余量
        再发一条 ``delta_move``;到 ``LIFT_MAX_ITERS`` 仍未收敛就按实得高度如实记账
        (``converged=False`` + ``iters``),不假装成功。

        **为什么必须闭环**:8/6 v4 实测,上游控制器每条 ``delta_move`` 只交付约
        **74%** 的指令量,空载与带载相同(负载无关 → 证伪了「重力把手臂压下去」的假设),
        而且渐近停住——一条指令发完就不再动了。因此固定步数的开环必然欠冲。按 74%
        交付率,剩余量每轮乘 0.26,几何收敛;实测 6 次迭代到位(轨迹 0 → 42.9 → 85.0
        → 93.9 → 94.8 → 94.9 → 95.0 mm)。**根因在上游控制器**,这里不改上游,只在
        我方语义层把「抬到目标高度」兜住。

        控制回路不读取特权实体位姿，只使用 EEF 上移量、末端外力和夹持回读。
        ``obj`` 仅用于日志，不参与控制判定。无法读取证据时保持 UNKNOWN。
        """
        p0, q0 = self._cur_xquat()
        f0 = self._ee_extforce_max()
        cur = (p0, q0)
        iters = 0
        while iters < LIFT_MAX_ITERS:
            remaining = LIFT_DZ - (cur[0][2] - p0[2])
            if abs(remaining) <= LIFT_TOL_M:
                break
            step = max(-LIFT_STEP_MAX_M, min(LIFT_STEP_MAX_M, remaining))
            self._ctrl("delta_move", delta_xyz=[0, 0, round(step, 4)])
            self._wait_settle(timeout_s=10.0)
            # _wait_settle 判静止后末端还会继续爬 1.3-1.9 mm;不吸收这段会把未完成的
            # 运动记成「已达高度」,下一轮的剩余量就是错的。
            time.sleep(LIFT_CREEP_S)
            self._verify_moved(cur, op="lift")
            cur = self._cur_xquat()             # 本轮实得,也是下一轮算剩余量的基准
            iters += 1
            self._log("lift_step", i=iters, cmd_dz=round(step, 4),
                      achieved_dz=round(cur[0][2] - p0[2], 4),
                      remaining_dz=round(LIFT_DZ - (cur[0][2] - p0[2]), 4))
        p1 = cur[0]
        converged = abs(LIFT_DZ - (p1[2] - p0[2])) <= LIFT_TOL_M
        f1 = self._ee_extforce_max()
        ee_dz = p1[2] - p0[2]                       # 非特权:末端自身上移量(不看物体)
        ee_rose = ee_dz >= SERVO_PROGRESS_EPS_M     # 指令是否真执行(派生自伺服进展容差)
        load = f1 if f1 is not None else None        # 抬起后残余负载(承重证据)
        # 外力不可读时才使用夹持回读；它不覆盖明确的空载证据。
        grip = self._is_gripping()
        if not ee_rose:
            attached, reason = None, "ee_did_not_rise"
        elif load is None:
            # 力信号不可用时退到夹持信号;两个都读不到才判 UNKNOWN。
            if grip is None:
                attached, reason = None, "force_and_grip_unreadable"
            else:
                attached, reason = ("likely", "grip_held_force_unreadable") if grip \
                    else ("empty", "no_grip_force_unreadable")
        elif load >= LIFT_LOAD_FORCE_N:
            attached, reason = "likely", "ee_rose_and_loaded"
        else:
            attached, reason = "empty", "ee_rose_no_load"
        self._log("lift_done", obj=str(obj), ee_dz=round(ee_dz, 4),
                  target_dz=LIFT_DZ, converged=converged, iters=iters,
                  load_n=None if load is None else round(load, 1),
                  gripping=grip, grip_angle=self._grip_angle(),
                  attached=attached, reason=reason)

    def _held_evidence(self):
        """非特权持物证据:夹爪角落在「夹住带」内 **且** 末端有持续外力。

        判据与常量跟 `lift` 的承重记账同源:力阈直接用 ``LIFT_LOAD_FORCE_N``;角度带由
        ``GRIP_ANGLE_TOL_DEG`` 从 ``GRIP_CLOSE``/``GRIP_OPEN`` 两端向内收——夹住东西时
        指垫被物体挡住,既到不了全闭也不停在全开,两端各留一个回读容差。

        ``is_gripping`` **只当停转信号**(语义见 `_is_gripping`:朝闭合方向走且电流限幅
        且未到目标角),它读到 False 只说明「此刻没在顶着走」,不足以判「没夹住」,所以
        它只进账本、不参与判定。

        返回 ``(held, why, detail)``。任一信号读不到 → ``held=None``(未知),调用方按
        拒绝处理,不 fail-open。
        """
        angle = self._grip_angle()
        force = self._ee_extforce_max()
        detail = {"grip_angle": angle,
                  "load_n": None if force is None else round(force, 1),
                  "gripping": self._is_gripping()}
        if angle is None or force is None:
            return None, "grip_or_force_unreadable", detail
        if not (GRIP_CLOSE + GRIP_ANGLE_TOL_DEG < angle
                < GRIP_OPEN - GRIP_ANGLE_TOL_DEG):
            return False, "gripper_not_in_holding_band", detail
        if force < LIFT_LOAD_FORCE_N:
            return False, "no_sustained_load", detail
        return True, "band_and_load", detail

    def reorient_held_axis(self, obj, object_axis, target_direction):
        """把已持有物体的 ``object_axis`` 转到与 ``target_direction`` 平行。

        契约由 backend 模型在 2026-08-06 的受控提案实验中提出,人类评审修订后 admit;
        修订项与出处见 `docs/API.md` 与 `docs/DEVLOG.md` 同日条目。

        两个轴句柄解出**世界系刚体旋转**(叉积定轴、点积定角),腕部走完这段旋转、全程
        保持夹持,**不发平移指令**(每步的 ``target_xyz`` 就是当轮回读到的 EEF 位置)。
        长轴是**无向的直线**而不是射线,所以 ``dot < 0`` 时先把目标方向取反走短程——
        否则会为了对齐同一条线白转 180°,还多担一次可达性风险。

        目标腕姿用**左乘**合成(``_qmul(R_world, q0)``):右乘是工具系,那是另一个姿态,
        约定见 `_flip_about_approach`。

        **闭环而不是发够步数**,两条理由:与 `lift` 同一个上游事实(单条指令只交付一部分,
        开环必然欠冲);以及 ep2/ep3 实测腕姿残差有 18° 量级。每轮回读 ``get_xquat``
        (非特权)算剩余角,按 ``SERVO_STEP_DEG`` 限幅 slerp 一步;剩余角 ≤
        ``REORIENT_TOL_DEG`` 收敛退出,连续 ``SERVO_PATIENCE`` 轮没有进展就**停下并如实
        记账**(``reason="no_rotation_progress"``),不硬转;走满 ``REORIENT_MAX_STEPS``
        记 ``reason="budget"`` 且 ``converged=False``。

        三条拒绝/短路路径:
        ① 任一轴句柄缺失或退化 → `unsupported_param` + ``reorient_refused``;
        ② 非特权持物证据不成立**或读不到** → ``reorient_refused``(读不到也拒,不 fail-open);
        ③ 两轴已近平行(剩余角 ≤ ``REORIENT_TOL_DEG``) → 解出的旋转是恒等,记
           ``already_aligned`` 成功并**不发任何指令**。

        剩余角是拿**腕姿**量的,不是拿物体长轴量的——前提正是路径②:物体被刚性夹住时
        它随腕部同步转,腕姿残差即长轴残差。这也是为什么持物证据是硬前置而不是记账项。

        **本实现只把 EEF 原点锁住,爪尖会随腕部旋转画弧**:真要做到抓取点零平移,得先
        知道抓取点在工具系里的确切位置,再绕它补一段平移。这里如实记下这个近似,
        不假装已经做到。

        评审注记:模型漏提「抓取朝向影响可达性」——不做成硬前置,policy 可以先用
        `grasp_at(axis=)` 优化抓取朝向,够不着的情形由无进展停止兜底。
        ``target_direction`` 在插入任务里天然由 rack 孔轴(``part_axis`` + hole anchor)
        求解,模型选 ``axis_3d`` 这个洞类型是合理的。
        """
        u, v = self._axis_vec(object_axis), self._axis_vec(target_direction)
        if u is None or v is None:
            param = "object_axis" if u is None else "target_direction"
            value = object_axis if u is None else target_direction
            self._unsupported(f"reorient_held_axis.{param}", value, "no_axis_vec")
            self._log("reorient_refused", obj=str(obj),
                      reason=f"no_axis_vec:{param}")
            return
        held, why, detail = self._held_evidence()
        if held is not True:
            self._log("reorient_refused", obj=str(obj),
                      reason="not_holding" if held is False else "hold_unreadable",
                      evidence=why, **detail)
            return

        nu = math.sqrt(sum(c * c for c in u))
        nv = math.sqrt(sum(c * c for c in v))
        u, v = [c / nu for c in u], [c / nv for c in v]
        d = sum(a * b for a, b in zip(u, v))
        flipped = d < 0.0
        if flipped:                 # 长轴无向:走短程,不为对齐同一条线白转 180°
            v, d = [-c for c in v], -d
        angle = math.degrees(math.acos(max(-1.0, min(1.0, d))))
        q0 = self._cur_xquat()[1]
        if angle <= REORIENT_TOL_DEG:
            q_target, reason, aligned = list(q0), "already_aligned", True
        else:
            axis = [u[1] * v[2] - u[2] * v[1],
                    u[2] * v[0] - u[0] * v[2],
                    u[0] * v[1] - u[1] * v[0]]
            q_target = _qnorm(_qmul(_qaxis(axis, angle), q0))
            reason, aligned = "budget", False

        best, stall, iters = _qang(q0, q_target), 0, 0
        while not aligned and iters < REORIENT_MAX_STEPS:
            p, q = self._cur_xquat()
            gap = _qang(q, q_target)
            if gap <= REORIENT_TOL_DEG:
                reason = "reached"
                break
            t = min(1.0, SERVO_STEP_DEG / gap)
            # target_xyz 用**本轮回读**的位置:既不下发平移,也顺带纠掉旋转带来的漂移。
            self._ctrl("xquat_move", target_xyz=[round(c, 4) for c in p],
                       target_quat=[round(c, 6) for c in _qslerp(q, q_target, t)],
                       interpolation="linear", gpos=GRIP_CLOSE_TUBE)
            self._wait_settle(timeout_s=18.0)
            iters += 1
            _, moved, turned = self._verify_moved((p, q), op="reorient")
            gap_after = _qang(self._cur_xquat()[1], q_target)
            self._log("reorient_step", i=iters, cmd_deg=round(gap * t, 2),
                      turned_deg=round(turned, 2), moved_m=round(moved, 4),
                      rot_gap_deg=round(gap_after, 2))
            if best - gap_after > SERVO_PROGRESS_EPS_DEG:
                best, stall = gap_after, 0
            else:
                stall += 1
                if stall >= SERVO_PATIENCE:
                    reason = "no_rotation_progress"
                    break

        rot_gap = _qang(self._cur_xquat()[1], q_target)
        held_after, why_after, detail_after = self._held_evidence()
        self._log("reorient_done", obj=str(obj), angle_deg=round(angle, 2),
                  rot_gap_deg=round(rot_gap, 2),
                  converged=rot_gap <= REORIENT_TOL_DEG, iters=iters,
                  flipped_target=flipped, reason=reason,
                  held_after=held_after, hold_evidence_after=why_after,
                  **detail_after)

    def transport(self, obj, target):
        # `obj`(被搬运物)按参数解析并记账。携物移动的
        # 落点由 target 决定,obj 用于审计「这条 transport 作用在哪个实体」;解析失败记 UNSUPPORTED。
        self._consume_obj(obj, op="transport")
        xyz = self._target_xyz(target)
        xyz[2] += PREGRASP_DZ + CLAW_TIP_DZ_CLOSED   # 携物移动:爪子闭合
        self._move(xyz)

    def align(self, obj, target, axis=None):
        """移动到标定对准高度，并用 ``axis`` 的水平投影约束腕部 yaw。

        轴缺失或近竖直时使用默认竖直姿态；无法消费的轴参数会被记录。
        """
        self._consume_obj(obj, op="align")
        xyz = self._target_xyz(target)
        xyz[2] += ALIGN_DZ + CLAW_TIP_DZ_CLOSED      # 对准时夹着物体:爪子闭合
        quat, why = self._align_quat(axis)
        if quat is None:
            if axis is not None:
                self._unsupported("align.axis", axis, why)
            self._move(xyz)
        else:
            self._log("align_axis", why=why, quat=[round(v, 4) for v in quat])
            self._move(xyz, quat=quat)

    # lower_until 支持的停止判据类别(任务无关词表)。stop_condition 只能把下探
    # **路由**到其中一类;控制回路只使用非特权信号。
    _STOP_KINDS = ("contact", "predicate", "plateau")

    def _stop_kind(self, stop_condition):
        """读取显式 ``stop_kind``；不从自由文本或洞名猜测控制语义。"""
        if stop_condition is None:
            return None, None
        raw = stop_condition.get("stop_kind") if isinstance(stop_condition, dict) else None
        if raw in self._STOP_KINDS:
            return raw, raw
        return None, raw

    def lower_until(self, stop_condition):
        """使用非特权信号逐步下探。

        ``contact`` 读取末端外力，``plateau`` 检查 EEF 是否停止下降。
        ``predicate`` 需要特权状态，不能进入控制回路，因此记录 UNSUPPORTED 并
        使用 contact/plateau。缺少 stop_kind 时记录该情况并启用非特权判据。
        """
        if (not isinstance(stop_condition, dict)
                or stop_condition.get("purpose") != "lower_stop"):
            raise ValueError(
                "lower_until requires a runtime condition with purpose='lower_stop'"
            )
        kind, raw = self._stop_kind(stop_condition)
        if kind is None:
            if stop_condition is not None:
                self._unsupported("lower_until.stop_condition", raw,
                                  "no_explicit_stop_kind:keep_all_criteria")
            enabled = set(self._STOP_KINDS)     # 未指定时启用可用判据
        else:
            self._log("lower_stop_route", stop_kind=kind)
            enabled = {kind}
        # predicate 类需特权实体态,去特权后无非特权实现 → UNSUPPORTED 记账 + 保守停止:
        # 把 predicate 从启用集移除,退回 contact/plateau 两类非特权判据(不静默继续用 probes)。
        if "predicate" in enabled:
            self._unsupported("lower_until.stop_kind", "predicate",
                              "privileged_predicate_no_nonpriv_impl:fallback_contact_plateau")
            enabled = (enabled - {"predicate"}) or {"contact", "plateau"}
        prev_z = None
        for i in range(LOWER_MAX_STEPS):
            before = self._cur_xquat()
            self._ctrl("delta_move", delta_xyz=[0, 0, -LOWER_STEP])
            self._wait_settle(timeout_s=8.0)
            self._verify_moved(before, op="lower")
            if "contact" in enabled:  # 末端外力是非特权接触信号
                fmax = self._ee_extforce_max()
                if fmax is not None and fmax > CONTACT_FORCE_N:
                    self._log("lower_until_done", reason="contact_force", steps=i + 1,
                              f=round(fmax, 1))
                    return
            try:
                z = self._cur_xquat()[0][2]
            except Exception:
                continue
            # plateau 触发按实情记 reason="plateau",不再冒充 contact(账本失真,ep2 修复圈正名)。
            if "plateau" in enabled and prev_z is not None and prev_z - z < LOWER_PLATEAU_M:
                self._log("lower_until_done", reason="plateau", steps=i + 1)
                return
            prev_z = z
        self._log("lower_until_done", reason="budget", steps=LOWER_MAX_STEPS)

    def release(self):
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        self._wait_grip(GRIP_OPEN)
        self._holding = False      # 松手之后才允许下一阶段重新选臂

    def retreat(self, target):
        """Refuse motion until a trusted solver can produce a safe retreat pose."""
        hole_name = target.get("hole") if isinstance(target, dict) else None
        purpose = target.get("purpose", "") if isinstance(target, dict) else ""
        semantic = f"{hole_name or ''} {purpose}".lower()
        if "retreat" not in semantic and "retract" not in semantic:
            raise ValueError("retreat requires an explicit retract/retreat handle")
        raise NotImplementedError(
            "retreat execution is blocked until a trusted runtime solver provides "
            "a safe pose from current EEF state"
        )

    # ---------- contract: 验证(词表几何三值检验,oracle 态) ----------
    def _ent_snapshot(self, constraint: dict) -> dict:
        """把约束 args 里引用到的实体解析成 predicates 吃的快照 {name: ent}。
        参照物按约束 args 逐个 _resolve;解析不到的略过(谓词侧据此判 UNKNOWN)。"""
        snap = {}
        for v in (constraint.get("args", {}) or {}).values():
            base = str(v).split(".")[0]
            if not base:
                continue
            try:
                snap[base] = self._ent(base)
            except Exception:
                pass                       # 解析不到 → 快照缺该键 → 谓词返回 UNKNOWN(不 fail-open)
        return snap

    def verify3(self, constraint: dict, **ctx):
        """委派 evaluation.predicates 做三值检验；无法检查时返回 UNKNOWN。"""
        snap = self._ent_snapshot(constraint)
        pred = predicates.check(constraint, snap, **ctx)
        self._log("verify", name=constraint.get("name"), stage=constraint.get("_stage"),
                  status=pred.status, margin=pred.margin, detail=pred.detail)
        return pred

    def verify(self, constraint: dict) -> bool:
        """bool 兼容接口：只有 PASS 返回 True；FAIL 和 UNKNOWN 返回 False。"""
        pred = self.verify3(constraint)
        return pred.status == predicates.PASS

    # ---------- 旁路:官方谓词快照 ----------
    def probes(self):
        return self.eval.state().get("probes", [])
