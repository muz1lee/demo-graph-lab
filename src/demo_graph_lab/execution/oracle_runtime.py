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
LOWER_STEP, LOWER_MAX_STEPS = 0.02, 12
GRIP_OPEN, GRIP_CLOSE = 100.0, 0.0  # Pipeline convention: 100=open, 0=closed.
GRIP_CLOSE_TUBE = GRIP_CLOSE
GRIP_SETTLE_S = 4.0
IDLE_ARM = {0: 1, 1: 0}
CONTACT_FORCE_N = 20.0
LIFT_LOAD_FORCE_N = CONTACT_FORCE_N / 4.0

# Tool quaternions use xyzw; object quaternions from /state use wxyz.
TDX0 = [0.0, 1.0, 0.0, 0.0]  # Ry(180): tool +z points down.
APPROACH_AXIS_IDX = 2
FINGER_AXIS_IDX = 1
CLAW_TIP_DZ = -0.010

# 抓取到位后仍可接受的笛卡尔 xy 残差(mm);超过则试另一个 IK 分支(见 _retry_flipped_branch)。
# 来源:8/6 v4 单世界栈实测——同一个物理抓取,默认分支 xy 误差 15.3 mm、最小关节裕度
# 0.297 rad;绕工具接近轴翻转 180° 后 3.6 mm、0.700 rad。阈值取两次实测之间。
GRASP_XY_RETRY_MM = 8.0

SERVO_STEP_M, SERVO_STEP_DEG = 0.05, 14.0
SERVO_POS_TOL, SERVO_ROT_TOL = 0.015, 8.0
MP_MIN_DIST_M = 2 * SERVO_STEP_M
SERVO_ITERS = 40
SERVO_STEP_MAX_M = 0.12
SERVO_PROGRESS_EPS_M, SERVO_PROGRESS_EPS_DEG = 0.0015, 0.4
SERVO_PATIENCE = 3


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

    def begin_stage(self, stage: dict) -> None:
        """Bind subsequent ``solve(name)`` calls to the current stage."""

        stage_index = int(stage["index"])
        if stage_index not in self._holes_by_stage:
            raise ValueError(f"stage {stage_index} is not declared in graph")
        self._active_stage_index = stage_index

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

    def _family_bijection(self, base: str, ents: dict):
        """按空间词和 y 坐标稳定匹配同类图对象与场景实体，并缓存结果。"""
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
        # 图名按空间得分从左到右排(得分大=左),同名再按字典序稳定
        names_l2r = sorted(names, key=lambda n: (-self._spatial_key(n.split(".")[0].lower()), n))
        mapping = {}
        for i, nm in enumerate(names_l2r):
            mapping[nm] = fam_l2r[min(i, len(fam_l2r) - 1)] if fam_l2r else None
        cache[ckey] = mapping
        return mapping

    def _resolve(self, name: str) -> str:
        """registry id/物体名 → /state 实体键。
        启发式顺序:精确 → 别名 → 子串 → **同类双射(空间词)** → 同义词兜底。"""
        ents = self._entities()
        if name in ents:
            return name
        base = str(name).split(".")[0].lower()
        for reg in self.registry:  # registry 的 trace_aliases 桥接
            if reg.get("id", "").lower() == base:
                for alias in reg.get("trace_aliases", []):
                    for e in ents:
                        if alias.lower() in e.lower():
                            return e
        for e in ents:  # 子串(如 bowl0 ↔ bowl0_prop)
            if base in e.lower() or e.lower().replace("_prop", "") in base:
                return e
        # 同类双射:名字含空间词、且同类图物体 >=2 个时,用缓存双射拿到唯一实体。
        toks = [t for t in base.replace("-", "_").split("_") if t in self._SPATIAL]
        if toks:
            mp = self._family_bijection(base, ents)
            hit = mp.get(name) or mp.get(base) or mp.get(str(name).split(".")[0])
            if hit:
                return hit
        for group in self.SYNONYMS:  # 同义词组兜底
            if any(w in base for w in group):
                for e in ents:
                    if any(w in e.lower() for w in group):
                        return e
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

    def _wait_grip(self, target, timeout_s=GRIP_SETTLE_S, tol=2.0):
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
        """
        p0, q0 = self._cur_xquat()
        tq = _topdown_like(q0) if quat is None else list(quat)
        target_pose = [float(v) for v in list(xyz)[:3]] + [float(v) for v in tq]
        # 短距离处于当前构型邻域，直接伺服并单独记为 move_local。
        dist0 = math.dist(p0, [float(v) for v in list(xyz)[:3]])
        if dist0 < MP_MIN_DIST_M and _qang(q0, tq) <= SERVO_ROT_TOL:
            self._log("move_local", dist=round(dist0, 4), reason="short_range")
            return self._move_servo(xyz, quat=quat, interpolation=interpolation, gpos=gpos)
        try:
            plan = robot_api.plan_joint_path(self, self.arm_id, target_pose,
                                            planning_mode="cartesian_goal")
        except robot_api.PlanFailed as e:
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
        off = PREGRASP_DZ + CLAW_TIP_DZ
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

    def grasp_at(self, grasp_pose, axis=None):
        """grasp_pose 给的是**爪尖**要到的世界点;EEF 帧要比它高 CLAW_TIP_DZ。

        若提供物体长轴 ``axis``，工具开合方向与其水平投影正交。轴缺失或
        近竖直时保持当前腕姿，并在无法消费参数时记录 UNSUPPORTED。
        下探到位后 xy 残差超过 ``GRASP_XY_RETRY_MM`` 时翻转一次 IK 分支再闭爪，
        见 ``_retry_flipped_branch``。
        """
        xyz = list(grasp_pose["xyz"]) if isinstance(grasp_pose, dict) else list(grasp_pose)
        eef = [xyz[0], xyz[1], xyz[2] + CLAW_TIP_DZ]
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        self._wait_grip(GRIP_OPEN)
        self._move([eef[0], eef[1], eef[2] + PREGRASP_DZ])
        gq, why = self._grasp_quat(axis)
        if gq is None:
            if axis is not None:
                self._unsupported("grasp_at.axis", axis, why)
            _, gq = self._cur_xquat()             # 退回:锁住已到位的腕姿
        else:
            self._log("grasp_axis", why=why, quat=[round(v, 4) for v in gq])
        self._move(eef, quat=gq, gpos=GRIP_OPEN)  # 下探时锁住抓取腕姿,只走 z
        self._retry_flipped_branch(eef, gq)       # xy 残差过大 → 试对称的另一个 IK 分支
        # !! 参数名只能是 angle:gpos 传给 set_gripper 会被静默丢弃且仍回 ok=True。
        # 开合方向见模块顶部 GRIP_* 注释:
        # 闭合 = 往 **更小** 的 angle 走,GRIP_CLOSE=0 才是全闭。
        self._ctrl("set_gripper", angle=GRIP_CLOSE_TUBE)
        self._wait_grip(GRIP_CLOSE_TUBE)
        # 记录角度和夹持回读；抓取结果由 lift 的承重证据和 gate 判定。
        self._log("grasp_close", target=GRIP_CLOSE_TUBE,
                  angle=self._grip_angle(), gripping=self._is_gripping())

    def lift(self, obj):
        """分小步抬升，并用非特权信号记录夹持证据。

        控制回路不读取特权实体位姿，只使用 EEF 上移量、末端外力和夹持回读。
        ``obj`` 仅用于日志，不参与控制判定。无法读取证据时保持 UNKNOWN。
        """
        p0, _ = self._cur_xquat()
        f0 = self._ee_extforce_max()
        n = max(1, int(round(LIFT_DZ / 0.02)))
        for _ in range(n):
            before = self._cur_xquat()
            self._ctrl("delta_move", delta_xyz=[0, 0, 0.02])
            self._wait_settle(timeout_s=10.0)
            self._verify_moved(before, op="lift")
        p1, _ = self._cur_xquat()
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
                  load_n=None if load is None else round(load, 1),
                  gripping=grip, grip_angle=self._grip_angle(),
                  attached=attached, reason=reason)

    def transport(self, obj, target):
        # `obj`(被搬运物)按参数解析并记账。携物移动的
        # 落点由 target 决定,obj 用于审计「这条 transport 作用在哪个实体」;解析失败记 UNSUPPORTED。
        self._consume_obj(obj, op="transport")
        xyz = self._target_xyz(target)
        xyz[2] += PREGRASP_DZ + CLAW_TIP_DZ
        self._move(xyz)

    def align(self, obj, target, axis=None):
        """移动到标定对准高度，并用 ``axis`` 的水平投影约束腕部 yaw。

        轴缺失或近竖直时使用默认竖直姿态；无法消费的轴参数会被记录。
        """
        self._consume_obj(obj, op="align")
        xyz = self._target_xyz(target)
        xyz[2] += ALIGN_DZ + CLAW_TIP_DZ
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
            if "plateau" in enabled and prev_z is not None and prev_z - z < 0.004:
                self._log("lower_until_done", reason="contact", steps=i + 1)
                return
            prev_z = z
        self._log("lower_until_done", reason="budget", steps=LOWER_MAX_STEPS)

    def release(self):
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        self._wait_grip(GRIP_OPEN)

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
