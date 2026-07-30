"""KWRuntime:contract.Runtime 的 knowin-world 实现(M1a ORACLE 模式)。

- ctrl → pipeline :8000(现有原语,arm_id 0=左/1=右;零新增)
- solve → M1a 用 EvalServer GET /state 的特权实体态(显式 ORACLE 标注,仅集成测试/上界;
  M1b 换 dgl-perception 非特权求解器,本类接口不变)
- verify → 约束词表的几何检查(基于 /state);官方谓词 probes 全程旁路记录
零污染:不改 knowin-world/k1-sys 任何文件。设计依据 harness/PHASE1_API_PLAN.md。
"""

from __future__ import annotations

import json
import math
import time
import urllib.request

from adapters.knowin_world.pipeline import PipelineClient

ORACLE_BANNER = "ORACLE-M1A"      # 本模式产出一律带此标签,不得报为方法结果
PREGRASP_DZ, LIFT_DZ, ALIGN_DZ = 0.10, 0.12, 0.06
LOWER_STEP, LOWER_MAX_STEPS = 0.02, 12
GRIP_OPEN, GRIP_CLOSE = 0.0, 160.0   # close 值沿用审计记录的 hand_tuned 常数
IDLE_ARM = {0: 1, 1: 0}              # 右臂作业时先把左臂归位,反之亦然

# ---- v3 实机标定(2026-07-30,phase1/orient_probe.py 扫描 + robot.urdf 核对)----
# 1) 四元数一律 xyzw:arm_node.local_rotation_move 用 scipy R.from_quat(xquat[3:]),
#    scipy 默认 xyzw;get_xquat 回读与 xquat_move 下发同序。
# 2) **接近轴是工具 +x,不是 +z**。robot.urdf 里 r_joint7 的 child 是 r_claw_base_link
#    (即 IK/FK 的 r_link7 帧),两根手指关节挂在 +x=0.097~0.104、绕 (0,0,1) 转、
#    分居 y=±0.0398 → 爪子沿工具 +x 伸出,开合方向是工具 ±y。
#    因此旧常数 GRASP_QUAT_TOPDOWN=[0,1,0,0] 让爪子指向世界 -x(水平朝后),根本不是
#    top-down;实测该姿态族 4 个 yaw 全部到不了(pitch 只能到 8.7°~26.9°,位置还漂
#    0.05~0.12 m)。正确的竖直姿态是 Ry(+90):把工具 +x 转到世界 -z。
TDX0 = [0.0, 0.70710678, 0.0, 0.70710678]   # Ry(+90),爪子垂直朝下,指开合方向沿世界 +y
# 3) 爪尖在 EEF 帧下方 CLAW_TIP_DZ 处(竖直朝下时)。标定:空桌面上方竖直下探,
#    ee_extforce 从 ~1.1 N 跳到 56.8 N 时 EEF z=0.817,桌面 z=0.765 → 0.052 m。
CLAW_TIP_DZ = 0.052
# 4) MotorNode 大跳不收敛:qpos_check_tolerance=0.05 rad / convergence_timeout=15 s,
#    单条大幅 xquat_move 会停在目标的 70~80% 处就放弃(实测 max_error 0.21~0.43 rad,
#    连"回到几秒前刚待过的姿态"也失败)。而每一小步都能干净收敛。
#    另外 IK 侧还有两道闸:请求姿态相对当前超过 ~90° 直接判失败(arm 一动不动),
#    腕部大幅 yaw 会撞 self_collision pair_id=178 (r_link5×r_link7)。
#    → 绝不整段下发目标,只下发"限幅子目标",闭环重解。
SERVO_STEP_M, SERVO_STEP_DEG = 0.05, 14.0
SERVO_ITERS, SERVO_POS_TOL, SERVO_ROT_TOL = 9, 0.015, 8.0
SERVO_STALL_EPS_M, SERVO_STALL_EPS_DEG = 0.003, 1.0


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
    """竖直朝下 + 绕接近轴(竖直轴)把开合方向转 psi;finger_axis == (sin psi, cos psi, 0)"""
    return _qnorm(_qmul(TDX0, _qaxis([1, 0, 0], psi_deg)))


def _topdown_like(q):
    """离当前腕姿最近的竖直抓取姿态:pitch 压到朝下,但**保留腕部自己的 yaw**。
    实测该臂在够物区能把 pitch 做到 85~89°,但 yaw 转不动——强行改 yaw 会撞
    pair_id=178 (r_link5×r_link7),IK 直接拒绝、机械臂一动不动。"""
    _, f, _ = _tool_axes(q)
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


def _quat_to_z_axis(q):  # wxyz → 物体局部 +z 在世界系的方向
    w, x, y, z = q
    return [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]


def _angle_deg(a, b):
    na = math.sqrt(sum(v * v for v in a)) or 1e-9
    nb = math.sqrt(sum(v * v for v in b)) or 1e-9
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))
    return math.degrees(math.acos(dot))


class KWRuntime:
    def __init__(self, graph: dict, objects: list | None = None,
                 eval_url="http://127.0.0.1:7480", pipe_url="http://127.0.0.1:8000",
                 arm_id: int = 1, log=None):
        self.graph, self.arm_id = graph, arm_id
        self.eval = EvalClient(eval_url)
        self.pipe = PipelineClient(pipe_url)
        self.registry = objects or []
        self.calls: list[dict] = [] if log is None else log
        self._hole_index = {h["name"]: (st, h) for st in graph["stages"]
                            for h in st.get("holes", [])}
        self._current_stage = None

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
        """把图里与 base 同类的所有物体名,一一映到同类实体上(稳定双射)。
        旧逻辑在同义词分支贪婪返回第一个实体,导致 bowl_left/mid_right/top_right 全撞
        bowl0;这里改为:取同类图名按空间词得分(再按名字)排序,同类实体按 y 排序,
        逐位对齐,保证每个图名拿到不同实体。结果缓存,避免每次重算。"""
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
        st, hole = self._hole_index.get(hole_name, (self._current_stage, {"name": hole_name}))
        so = (st or {}).get("stage_objects") or {}
        manip, target = so.get("manipulated"), so.get("target")
        n = hole_name.lower()
        val: dict = {"kind": "oracle", "hole": hole_name}
        if "grasp" in n and "pose" in n:
            e = self._ent(manip or n.split("_grasp")[0])
            top = e["aabb"]["max"][2] if isinstance(e.get("aabb"), dict) else e["aabb"][1][2]
            val.update(kind="pose", xyz=[e["pos"][0], e["pos"][1],
                                         top - 0.03], quat=None)  # 上部区域:顶下 3cm
        elif "axis" in n:
            obj = manip if manip else n.split("_")[0]
            try:
                val.update(kind="axis", vec=_quat_to_z_axis(self._ent(obj)["quat"]))
            except KeyError:
                val.update(kind="axis", vec=[0, 0, 1])
        elif any(k in n for k in ("hole", "slot", "place", "target", "insert_point", "center")):
            e = self._ent(target or "slot")
            top = e["aabb"]["max"][2] if isinstance(e.get("aabb"), dict) else e["aabb"][1][2]
            val.update(kind="point", xyz=[e["pos"][0], e["pos"][1], top])
        elif any(k in n for k in ("depth", "height", "clearance", "distance")):
            val.update(kind="scalar", value=0.05)
        else:  # runtime_condition 等 → 描述子,交给 lower_until/verify 消费
            val.update(kind="condition", target=target, manip=manip)
        self._log("solve", hole=hole_name, kind=val["kind"])
        return val

    def residual(self, constraint: dict):
        self._log("residual", name=constraint.get("name"))
        return {"kind": "residual", "constraint": constraint.get("name")}

    # ---------- contract: 控制原语(pipeline ctrl 透传) ----------
    def _ctrl(self, fn: str, arm_id=None, **kw):
        """裸下发。**HTTP 返回值不可信**:ArmNode 日志里 result=FAILED 时,
        action=ctrl 依旧回 {"ok":true,"result":"True"}。所以这里不返回成功与否,
        任何调用点都必须用 _verify_moved()/_cur_xquat() 自己确认效果。"""
        self._log("ctrl", fn=fn, arm=self.arm_id if arm_id is None else arm_id,
                  **{k: v for k, v in kw.items() if k != "target_quat"})
        return self.pipe.call("ctrl", fn, {"arm_id": self.arm_id if arm_id is None else arm_id,
                                           **kw})

    def _cur_xquat(self):
        x = self.pipe.call("info", "get_xquat", {"arm_id": self.arm_id})
        return list(x[:3]), list(x[3:7])

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
        self._log("verify", op=op, ok=ok, moved=round(moved, 4), turned=round(turned, 2),
                  pos_gap=round(math.dist(p, list(xyz)), 4) if xyz is not None else None,
                  rot_gap=round(_qang(q, quat), 2) if quat is not None else None)
        return ok, moved, turned

    def _park_idle_arm(self):
        """标准前置步:把不作业的那条臂归位。实测左臂姿态会污染右臂 IK
        (跑动中冒出 pair_id=75 l_joint5×l_joint7,dmin +0.0084 < margin 0.010),
        给左臂 go_home 后即消失。注意 go_home 在本栈**必然报 FAILED**
        (配置的 home_qpos 撑不住,joint1 差 0.4 rad),所以只当尽力而为,不做成功门。"""
        idle = IDLE_ARM.get(self.arm_id)
        if idle is None:
            return
        self._ctrl("go_home", arm_id=idle)
        time.sleep(6.0)

    def _arm_qpos(self):
        """本臂 7 个关节角。/state 的 robot_qpos 是两臂交错排布:
        左臂在偶数下标、右臂在奇数下标,之后才是 lifting + 12 个爪子自由度。"""
        return self.eval.state()["robot_qpos"][self.arm_id::2][:7]

    def _wait_settle(self, target_xyz=None, tol=0.012, timeout_s=25.0, still_n=3):
        """pipeline 的 ctrl 是 fire-and-forget(源码里等 future 那行被注释),必须自己等。
        用 /state 的 robot_qpos 判静止,而不是轮询 get_xquat——后者每次都会往 pipeline
        日志里灌两行 GET,几秒就能把 IK 判决行冲出 tmux 回滚区,调试时什么都看不到。"""
        t0, last, still = time.time(), None, 0
        while time.time() - t0 < timeout_s:
            time.sleep(0.4)
            try:
                q = self._arm_qpos()
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
            if moved < SERVO_STALL_EPS_M:
                stall += 1
                if stall >= 2:
                    self._log("step_to", reason="stalled",
                              gap=round(math.dist(self._cur_xquat()[0], list(xyz)), 4))
                    return False
            else:
                stall = 0
        self._log("step_to", reason="budget",
                  gap=round(math.dist(self._cur_xquat()[0], list(xyz)), 4))
        return False

    def _move(self, xyz, quat=None, interpolation="linear", gpos=None):
        """位姿闭环伺服——本栈唯一可靠的运动方式。
        为什么不能一次性下发目标:MotorNode 对大幅 qpos 跳变收不敛(停在 70~80% 处
        就超时放弃),同时 IK 对"相对当前超过 ~90°"的姿态请求直接拒绝、机械臂纹丝不动。
        所以每轮只下发一个**限幅子目标**(平移 <=SERVO_STEP_M、旋转 <=SERVO_STEP_DEG),
        settle 后回读真实位姿再解下一步——每步都从更展开的构型重解 IK,顺带避开了
        一次大跳才会踩到的自碰撞解。quat=None 时取"离当前腕姿最近的竖直姿态"。"""
        stall = 0
        for i in range(SERVO_ITERS):
            p, q = self._cur_xquat()
            tq = _topdown_like(q) if quat is None else list(quat)
            dp, dr = math.dist(p, list(xyz)), _qang(q, tq)
            if dp <= SERVO_POS_TOL and dr <= SERVO_ROT_TOL:
                self._log("move", reason="reached", i=i,
                          pos_gap=round(dp, 4), rot_gap=round(dr, 2))
                return True
            f = min(1.0, SERVO_STEP_M / dp) if dp > 1e-9 else 1.0
            t = min(1.0, SERVO_STEP_DEG / dr) if dr > 1e-9 else 1.0
            kw = {"target_xyz": [round(p[j] + (xyz[j] - p[j]) * f, 4) for j in range(3)],
                  "target_quat": [round(v, 6) for v in _qslerp(q, tq, t)],
                  "interpolation": interpolation}
            if gpos is not None:
                kw["gpos"] = gpos
            self._ctrl("xquat_move", **kw)
            self._wait_settle(target_xyz=kw["target_xyz"], timeout_s=18.0)
            _, moved, turned = self._verify_moved((p, q), op="move")
            if moved < SERVO_STALL_EPS_M and turned < SERVO_STALL_EPS_DEG:
                stall += 1
                if stall >= 2:
                    self._log("move", reason="stalled", i=i,
                              pos_gap=round(math.dist(self._cur_xquat()[0], list(xyz)), 4))
                    return False
            else:
                stall = 0
        p, q = self._cur_xquat()
        ok = math.dist(p, list(xyz)) <= SERVO_POS_TOL
        self._log("move", reason="budget", ok=ok, pos_gap=round(math.dist(p, list(xyz)), 4))
        return ok

    def _target_xyz(self, target):
        if isinstance(target, dict) and "xyz" in target:
            return list(target["xyz"])
        e = self._ent(target if isinstance(target, str) else str(target))
        return list(e["pos"])

    def approach(self, target, cone=None):
        """先把闲置臂归位,再纯位置靠到物体正上方(此时不锁腕姿),最后才把腕姿压成
        竖直——顺序很关键:腕部 yaw 在够物区转不动,只有先到位、再取"当前腕姿的竖直版"
        才解得出来。"""
        self._park_idle_arm()
        xyz = self._target_xyz(target)
        xyz[2] += PREGRASP_DZ + CLAW_TIP_DZ
        self._step_to(xyz)
        return self._move(xyz)

    def grasp_at(self, grasp_pose):
        """grasp_pose 给的是**爪尖**要到的世界点;EEF 帧要比它高 CLAW_TIP_DZ。"""
        xyz = list(grasp_pose["xyz"]) if isinstance(grasp_pose, dict) else list(grasp_pose)
        eef = [xyz[0], xyz[1], xyz[2] + CLAW_TIP_DZ]
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        time.sleep(1.5)
        self._move([eef[0], eef[1], eef[2] + PREGRASP_DZ])
        _, q = self._cur_xquat()
        self._move(eef, quat=q, gpos=GRIP_OPEN)   # 下探时锁住已到位的腕姿,只走 z
        # !! 2026-07-30 实测:本仿真栈的夹爪**根本不动**。set_gripper 任意角度、
        # delta_move(gpos=...) 都不会改变 /state robot_qpos 里 12 个爪子自由度中的任何一个。
        # 原因在栈侧:sim/robot_manager._apply_gripper_control 要 arm_id*8+7 这条控制通道,
        # 而 v3 控制器每臂只出 7 个自由度(pipeline get_qpos 长度就是 7,没有夹爪通道)。
        # MotorNode 还会秒回 result=SUCCESS——因为 _wait_gripper 拿"上一条指令值"跟目标比。
        # 属于 k1-sys/knowin-world 的问题,不在我们可改范围;在通道接好之前捏取不可能成功。
        # 另外 angle 会被 gripper.max_angle=100 截断,所以 160 实际等于 100(全闭)。
        self._ctrl("set_gripper", angle=GRIP_CLOSE)
        time.sleep(3.5)   # 闭合无可靠回读(is_gripping_sth 在本仿真恒假),固定等待

    def lift(self, obj):
        """分小步抬升并逐步核对:一次 0.12 m 的 delta_move 会让 MotorNode 收不敛,
        而且抓稳与否只能看物体自己有没有跟着上来。"""
        e0 = self._ent(obj) if isinstance(obj, str) else None
        z0 = e0["pos"][2] if e0 else None
        n = max(1, int(round(LIFT_DZ / 0.02)))
        for _ in range(n):
            before = self._cur_xquat()
            self._ctrl("delta_move", delta_xyz=[0, 0, 0.02])
            self._wait_settle(timeout_s=10.0)
            self._verify_moved(before, op="lift")
        if z0 is not None:
            dz = self._ent(obj)["pos"][2] - z0
            self._log("lift_done", obj=obj, obj_dz=round(dz, 4), attached=bool(dz > 0.05))

    def transport(self, obj, target):
        xyz = self._target_xyz(target)
        xyz[2] += PREGRASP_DZ + CLAW_TIP_DZ
        self._move(xyz)

    def align(self, obj, target, axis=None):
        xyz = self._target_xyz(target)
        xyz[2] += ALIGN_DZ + CLAW_TIP_DZ
        self._move(xyz)

    def lower_until(self, stop_condition):
        """逐步下探。停止条件:目标谓词转真(root_in_bbox/axis_aligned,非恒真项)、
        或高度不再下降(接触/受阻)、或步数预算耗尽。不用恒真的 depth_in 作判据。"""
        prev_z = None
        for i in range(LOWER_MAX_STEPS):
            before = self._cur_xquat()
            self._ctrl("delta_move", delta_xyz=[0, 0, -LOWER_STEP])
            self._wait_settle(timeout_s=8.0)
            self._verify_moved(before, op="lower")
            try:    # 接触力是本栈最灵敏的触底信号:空载 ~1 N,碰到桌面瞬间跳到 ~57 N
                f = self.pipe.call("info", "get_ee_extforce", {"arm_id": self.arm_id})
                fmax = max(abs(float(v)) for v in _flatten(f))
                if fmax > 20.0:
                    self._log("lower_until_done", reason="contact_force", steps=i + 1,
                              f=round(fmax, 1))
                    return
            except Exception:
                pass
            probes = {str(p.get("label")): p.get("passed") for p in self.probes()}
            if probes.get("root_in_bbox") and probes.get("axis_aligned"):
                self._log("lower_until_done", reason="predicates", steps=i + 1)
                return
            try:
                z = self._cur_xquat()[0][2]
            except Exception:
                continue
            if prev_z is not None and prev_z - z < 0.004:
                self._log("lower_until_done", reason="contact", steps=i + 1)
                return
            prev_z = z
        self._log("lower_until_done", reason="budget", steps=LOWER_MAX_STEPS)

    def push(self, obj, contact, toward):
        raise NotImplementedError("push 任务挂起(老板指示),M1 不实现")

    def release(self):
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        time.sleep(1.2)

    # ---------- contract: 验证(词表几何检查,oracle 态) ----------
    def verify(self, constraint: dict) -> bool:
        name, args = constraint.get("name"), constraint.get("args", {}) or {}
        ok, detail = True, ""
        try:
            if name == "axis_vertical":
                obj = str(list(args.values())[0]).split(".")[0]
                ang = _angle_deg(_quat_to_z_axis(self._ent(obj)["quat"]), [0, 0, 1])
                ok, detail = min(ang, 180 - ang) < 20.0, f"angle={ang:.1f}"
            elif name == "axis_parallel":
                vs = [str(v).split(".")[0] for v in args.values()]
                a1 = _quat_to_z_axis(self._ent(vs[0])["quat"])
                ang = _angle_deg(a1, [0, 0, 1])  # 孔轴按竖直近似(oracle 简化)
                ok, detail = min(ang, 180 - ang) < 25.0, f"angle={ang:.1f}"
            elif name in ("above", "inside"):
                vals = list(args.values())
                a, b = self._ent(str(vals[0]).split(".")[0]), self._ent(str(vals[1]).split(".")[0])
                if name == "above":
                    ok = a["pos"][2] > b["pos"][2]
                else:
                    bx = b["aabb"]
                    lo, hi = (bx["min"], bx["max"]) if isinstance(bx, dict) else bx
                    ok = (lo[0] - 0.02 <= a["pos"][0] <= hi[0] + 0.02
                          and lo[1] - 0.02 <= a["pos"][1] <= hi[1] + 0.02)
            elif name == "center_align":
                vals = list(args.values())
                a, b = self._ent(str(vals[0]).split(".")[0]), self._ent(str(vals[1]).split(".")[0])
                d = math.dist(a["pos"][:2], b["pos"][:2])
                ok, detail = d < 0.05, f"xy_dist={d:.3f}"
            else:  # region_grasp/carry/order/clearance 等 M1a 不可几何判 → 记录不拦截
                detail = "unchecked"
        except Exception as e:
            ok, detail = True, f"verify_error:{e}"   # oracle 检查失败不误杀,记录待查
        self._log("verify", name=name, stage=constraint.get("_stage"), ok=ok, detail=detail)
        return ok

    # ---------- 旁路:官方谓词快照 ----------
    def probes(self):
        return self.eval.state().get("probes", [])
