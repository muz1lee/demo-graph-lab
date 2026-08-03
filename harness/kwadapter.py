"""[phase1] KWRuntime:contract.Runtime 的 knowin-world 实现(M1a ORACLE 模式)。

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

from . import binding
from . import predicates
from . import regions

ORACLE_BANNER = "ORACLE-M1A"      # 本模式产出一律带此标签,不得报为方法结果
PREGRASP_DZ, LIFT_DZ, ALIGN_DZ = 0.10, 0.12, 0.06
LOWER_STEP, LOWER_MAX_STEPS = 0.02, 12
GRIP_OPEN, GRIP_CLOSE = 0.0, 160.0   # close 值沿用审计记录的 hand_tuned 常数
IDLE_ARM = {0: 1, 1: 0}              # 右臂作业时先把左臂归位,反之亦然
# 接触力阈值(N):空载 ~1.1 N,碰桌面瞬间跳到 ~57 N。原为 lower_until 内联字面量 20.0,
# P0-14 提到模块级命名(数值不变,位置沿用)。lift 的 attach 判据从此常数派生,不新拍脑袋:
#   CONTACT_FORCE_N   —— 触底/受阻的接触事件阈值(lower_until 沿用,值不变)。
#   LIFT_LOAD_FORCE_N —— 抬起后夹爪仍承载物体的残余负载阈值(< 触底阈值,从 CONTACT_FORCE_N 派生)。
CONTACT_FORCE_N = 20.0
LIFT_LOAD_FORCE_N = CONTACT_FORCE_N / 4.0   # 派生:残余负载远轻于触底冲击,取触底阈值的 1/4
# lift 的非特权位移判据:抬升指令确有执行的最小 EEF 上移量(m)。从既有伺服进展容差派生,
# 不新增魔数(SERVO_PROGRESS_EPS_M 见下,是"算作有进展"的最小位移)。

# ---- v3 实机标定(2026-07-30,phase1/orient_probe.py 扫描 + robot.urdf 核对)----
# 1) 四元数一律 xyzw:arm_node.local_rotation_move 用 scipy R.from_quat(xquat[3:]),
#    scipy 默认 xyzw;get_xquat 回读与 xquat_move 下发同序。
# 2) **接近轴是工具 +Z**(2026-07-30 用户指出并经腕部相机实证纠正)。
#    先前依 urdf 里手指连杆挂在 +x 而推断"接近轴=+x",并把姿态改成 Ry(+90) —— 错误。
#    实证:在该姿态下腕部相机拍到**地平线**(桌沿 + 上方空背景),说明爪子是水平前伸;
#    朝下时腕相机应被桌面填满。手指连杆的 +x 偏置是爪体自身的几何,不是 TCP 接近方向。
#    → 恢复 Ry(180):把工具 +z 转到世界 -z,即爪子垂直朝下(与旧常数一致)。
TDX0 = [0.0, 1.0, 0.0, 0.0]   # Ry(180):工具 +z → 世界 -z,爪子垂直朝下
APPROACH_AXIS_IDX = 2         # _tool_axes 返回 (x,y,z);接近轴取 +z
FINGER_AXIS_IDX = 1           # 开合轴 = 工具 +y
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
SERVO_POS_TOL, SERVO_ROT_TOL = 0.015, 8.0
# 5) **欠行程**:MotorNode 的平滑让实际位移只有指令的 ~20%(实测发 0.02 m 只走
#    0.002~0.005 m)。所以 ①卡死不能按"单步位移小"判——真实步进本来就小,旧阈值
#    3 mm 比步进还大,会把正在前进的下探误判成卡死;改为按**与目标的剩余距离是否
#    在缩小**判。②迭代预算按剩余距离动态给,并按实测效率放大指令步长(有上限)。
SERVO_ITERS = 40
SERVO_STEP_MAX_M = 0.12          # 效率补偿后单条指令的位移上限
SERVO_PROGRESS_EPS_M, SERVO_PROGRESS_EPS_DEG = 0.0015, 0.4
SERVO_PATIENCE = 3               # 连续无进展多少轮判定真卡死


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


def _topdown_like(q):
    """离当前腕姿最近的竖直抓取姿态:pitch 压到朝下,但**保留腕部自己的 yaw**。
    实测该臂在够物区能把 pitch 做到 85~89°,但 yaw 转不动——强行改 yaw 会撞
    pair_id=178 (r_link5×r_link7),IK 直接拒绝、机械臂一动不动。"""
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
        """签名不动(contract.py:19 单参)。内部自取 stage 里的 hole 与本阶段 constraints,
        委派 binding 按 hole["type"] 派发(C-1/C-2/C-3,见 docs/TODO.md §1.2、EXECUTION §2.5)。
        `_hole_index` 查不到 → raise UnsolvedHole(L2_bind),**不回退到当前阶段猜**(C-2)。"""
        try:
            st, hole = self._hole_index[hole_name]
        except KeyError as e:
            raise binding.UnsolvedHole(
                f"solve: 图中无声明的 hole {hole_name!r}"
                f"(已声明:{sorted(self._hole_index)})",
                reason="hole_not_declared") from e
        val = binding.solve_hole(hole, stage=st, constraints=st.get("constraints") or [],
                                 rt=self)
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

    def _ee_extforce_max(self):
        """末端外力标量(非特权信号):取各分量绝对值的最大值。读不到 → None。
        PRIMITIVE_API §7 口径纠正:BLOCKED 分级对直连 pipeline 的我们不成立,
        get_ee_extforce 实测可调。空载 ~1.1 N,触底跳到 ~57 N,抬物残余负载居中。"""
        try:
            f = self.pipe.call("info", "get_ee_extforce", {"arm_id": self.arm_id})
            return max(abs(float(v)) for v in _flatten(f))
        except Exception:
            return None

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
            # 欠行程下"单步位移小"是常态,判卡死要放到进展语义上(见 SERVO_* 注释 5)
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
        """位姿闭环伺服——本栈唯一可靠的运动方式。
        为什么不能一次性下发目标:MotorNode 对大幅 qpos 跳变收不敛(停在 70~80% 处
        就超时放弃),同时 IK 对"相对当前超过 ~90°"的姿态请求直接拒绝、机械臂纹丝不动。
        所以每轮只下发一个**限幅子目标**(平移 <=SERVO_STEP_M、旋转 <=SERVO_STEP_DEG),
        settle 后回读真实位姿再解下一步——每步都从更展开的构型重解 IK,顺带避开了
        一次大跳才会踩到的自碰撞解。quat=None 时取"离当前腕姿最近的竖直姿态"。"""
        no_progress, best_dp, best_dr, eff = 0, None, None, 1.0
        for i in range(SERVO_ITERS):
            p, q = self._cur_xquat()
            tq = _topdown_like(q) if quat is None else list(quat)
            dp, dr = math.dist(p, list(xyz)), _qang(q, tq)
            if dp <= SERVO_POS_TOL and dr <= SERVO_ROT_TOL:
                self._log("move", reason="reached", i=i,
                          pos_gap=round(dp, 4), rot_gap=round(dr, 2))
                return True
            # 进展判据:剩余距离/角度是否较历史最好值有实质缩小
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

    # ---------- 契约参数消费 / 显式记账(P0-15,破口③) ----------
    def _unsupported(self, param, value, reason):
        """参数被读到但当前 runtime 无法映射为行为时的**显式记账**。
        静默丢弃归零:凡是读到却用不上的契约参数,都在这里进调用账本
        (param/value/reason 三字段,测试可断言),绝不再无声吞掉。"""
        self._log("unsupported_param", param=param, value=repr(value), reason=reason)

    def _consume_obj(self, obj, *, op):
        """消费 align/transport 的 `obj`(被操作物):按参数解析成 oracle 实体,
        走与 binding._resolve_ref 同款路径(rt._ent → _resolve)。解析成功记
        obj_resolved(供 gate/评测审计「这条动作作用在哪个实体」);
        解析不到则 UNSUPPORTED 记账——不再当作不存在而静默忽略。
        返回解析出的实体 dict 或 None。"""
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
        """由对齐轴 `axis` 推出末端目标腕姿(几何通用,零任务分支)。
        语义:竖直朝下抓取(TDX0),把开合方向(工具 +y)的世界投影绕接近轴转到
        与 axis 的**水平投影**一致——即 axis 决定腕部 yaw 这个自由度。
        用既有 _tdx(psi) 生成,不引入任何新度量常数。
        返回 (quat 或 None, 说明)。axis 缺失或近竖直(无水平分量定不出 yaw)→
        quat=None(退回旧的「离当前腕姿最近的竖直姿态」),说明供 UNSUPPORTED 记账。"""
        v = self._axis_vec(axis)
        if v is None:
            return None, "no_axis_vec"
        horiz = math.sqrt(v[0] * v[0] + v[1] * v[1])
        if horiz < 1e-6:
            # 轴近竖直:与接近轴共线,无法据此约束 yaw 自由度。
            return None, "axis_vertical_yaw_unconstrained"
        psi = math.degrees(math.atan2(v[0], v[1]))
        return _tdx(psi), "yaw_from_axis"

    # 任务无关的候选 approach 方向调色板(单位向量,世界系)。**与 cone 无关地生成**:
    # cone 只在下面的排序步进入,绝不参与候选生成(否则 E-CAUSAL 变同义反复,见 TODO C-5)。
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

    def approach(self, target, cone=None):
        """先把闲置臂归位,再纯位置靠到物体的**预抓取位**(此时不锁腕姿),最后才把腕姿压成
        竖直——顺序很关键:腕部 yaw 在够物区转不动,只有先到位、再取"当前腕姿的竖直版"
        才解得出来。

        C-5:`cone` 真正参与排序 —— 候选 approach 方向由任务无关调色板生成(不看 cone),
        再用 regions.rank_by_cone 按 cone 偏好(与锥轴夹角越小越优)排序,top-1 方向决定
        预抓取偏置的朝向。cone=None 时保持旧行为(正上方下探)。**候选生成不消费 cone**。"""
        self._park_idle_arm()
        xyz = self._target_xyz(target)
        off = PREGRASP_DZ + CLAW_TIP_DZ
        if cone is None:
            xyz[2] += off                       # 无 cone:沿用竖直下探(向下 → 偏置在正上方)
        else:
            # 候选与 cone 无关地生成;cone 仅在此排序步进入。
            best = regions.rank_by_cone(self._APPROACH_DIR_CANDIDATES, cone)[0]
            d = best["approach_dir"]
            n = math.sqrt(sum(v * v for v in d)) or 1.0
            u = [v / n for v in d]
            # 预抓取位在物体沿「−approach 方向」偏置 off:approach 向下→偏置在上方(旧行为),
            # approach 侧向→偏置在侧方。方向由 cone 排序 top-1 决定,幅度是既有标定常量。
            xyz = [xyz[i] - u[i] * off for i in range(3)]
            self._log("approach_cone", cone=cone, dir=best["id"])
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
        # !! 参数名只能是 angle。2026-07-30 晚更正:此前本注释断言"夹爪根本不动",
        # 那是**测试用错参数名**得出的错误结论——用 gpos=... 调 set_gripper 会静默无效
        # (pipeline 照样回 ok=True),导致画面零变化而被误读成通道不通。
        # 用 angle=0..100 在 v4 栈(k1u_v4_w_claw_26w27_1d)上实测:腕部相机可见指垫开合,
        # 图像 md5 与体积均变化。**夹爪可动,捏取不是不可能。**
        # 仍然成立的三条:
        #   ① angle 被 gripper.max_angle=100 截断,所以 160 实际等于 100(全闭);
        #   ② MotorNode 会秒回 result=SUCCESS——_wait_gripper 拿"上一条指令值"跟目标比,
        #      所以 SUCCESS 不能单独当"已到位"的证据,要看物理量或画面;
        #   ③ is_gripping_sth 在本仿真恒假(见下),闭合无可靠回读。
        # 教训:判断"通道通不通"前先确认参数名与调用形态,否则会把自己的调用错误
        # 归因成栈的能力缺失。
        self._ctrl("set_gripper", angle=GRIP_CLOSE)
        time.sleep(3.5)   # 闭合无可靠回读(is_gripping_sth 在本仿真恒假),固定等待

    def lift(self, obj):
        """分小步抬升并逐步核对:一次 0.12 m 的 delta_move 会让 MotorNode 收不敛。

        P0-14 去特权:attached 判据**不再读实体位姿**(旧代码用 `_ent(obj)["pos"][2]` 前后差,
        那是特权实体态进了方法路径的控制回路,违反 D-04 GT 防火墙)。改用非特权代理证据:
          ① EEF 位移:抬升前后 get_xquat 的 z 上移量(指令是否真执行);
          ② 接触力残留:抬起后 get_ee_extforce 仍有负载 = 夹爪确在承重(抓住了东西)。
        三值(与 predicates 同语义,不 fail-open):
          PASS_evidence(attached="likely") = EEF 确有上移 且 残余负载 ≥ LIFT_LOAD_FORCE_N;
          FAIL_evidence (attached="empty")  = EEF 确有上移 但 无残余负载(抬了个空);
          UNKNOWN(attached=None)            = EEF 没上移(指令没执行) 或 力信号读不到 → 判不出。
        obj 仅作审计标签记账,不用于任何判定;绝不因判不出而默认成功。"""
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
        if not ee_rose or load is None:
            attached, reason = None, ("ee_did_not_rise" if not ee_rose else "force_unreadable")
        elif load >= LIFT_LOAD_FORCE_N:
            attached, reason = "likely", "ee_rose_and_loaded"
        else:
            attached, reason = "empty", "ee_rose_no_load"
        self._log("lift_done", obj=str(obj), ee_dz=round(ee_dz, 4),
                  load_n=None if load is None else round(load, 1),
                  attached=attached, reason=reason)

    def transport(self, obj, target):
        # P0-15:`obj`(被搬运物)按参数解析并记账,不再静默忽略。M1a 携物移动的
        # 落点由 target 决定,obj 用于审计「这条 transport 作用在哪个实体」;解析失败记 UNSUPPORTED。
        self._consume_obj(obj, op="transport")
        xyz = self._target_xyz(target)
        xyz[2] += PREGRASP_DZ + CLAW_TIP_DZ
        self._move(xyz)

    def align(self, obj, target, axis=None):
        """P0-15:让 align 真正按 `axis` 约束末端姿态,而不再是「只差 DZ 常数」的 transport。
        - `obj`:被对齐物按参数解析并记账(_consume_obj,同 binding._resolve_ref 路径)。
        - `axis`:决定腕部 yaw 自由度——由 _align_quat 从轴的水平投影推出目标腕姿,
          交给 _move(quat=...) 约束姿态。**不同 axis → 不同目标腕姿 → 不同末端行为**。
          axis 缺失/近竖直(定不出 yaw)→ quat=None 退回旧竖直姿态,并 UNSUPPORTED 记账。
        位置分量仍走既有对准高度(ALIGN_DZ,旧常数,P0-16 再清)。"""
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
    # **路由**到其中一类;判据实现本身(oracle 态)不在本任务动——去特权是 P0-14。
    _STOP_KINDS = ("contact", "predicate", "plateau")

    def _stop_kind(self, stop_condition):
        """P0-15:从 `stop_condition` 参数读出停止判据类别(不再静默丢弃)。
        只认参数上**显式**、任务无关的 `stop_kind` 字段(binding/policy 可置为
        contact/predicate/plateau 之一);句柄/字典无该字段 → 返回 None。
        **不**解析 solver_hint 自由文本或洞名子串(那会重演旧的名字派发反面教材、
        并把任务语义走私进来)。返回 (kind 或 None, 原始值)。"""
        if stop_condition is None:
            return None, None
        raw = stop_condition.get("stop_kind") if isinstance(stop_condition, dict) else None
        if raw in self._STOP_KINDS:
            return raw, raw
        return None, raw

    def lower_until(self, stop_condition):
        """逐步下探。停止条件全部走**非特权信号**(P0-14 去特权):
          contact  —— get_ee_extforce 接触力跳变(空载 ~1 N,触底 ~57 N);非特权 ✓
          plateau  —— get_xquat 的 z 不再下降(受阻/触底);非特权 ✓
          predicate—— 旧实现读 rt.probes()(特权实体态谓词 root_in_bbox/axis_aligned),
                      违反 D-04 GT 防火墙。**P0-14 起不再调 probes():该类判据无非特权实现,
                      改为 UNSUPPORTED 记账 + 保守停止**(退回 contact+plateau 两类非特权判据,
                      绝不静默继续用 probes)。
        或步数预算耗尽兜底。

        P0-15 的 stop_kind 路由保留:
        - 参数带显式 stop_kind → 只启用该类判据;其中 predicate 会走去特权分支(见上)。
        - 参数缺 stop_kind(现有语料均如此)→ 记 UNSUPPORTED 并保持非特权判据全开旧行为。"""
        kind, raw = self._stop_kind(stop_condition)
        if kind is None:
            if stop_condition is not None:
                self._unsupported("lower_until.stop_condition", raw,
                                  "no_explicit_stop_kind:keep_all_criteria")
            enabled = set(self._STOP_KINDS)     # 旧行为:非特权判据全开
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
            if "contact" in enabled:  # 接触力:本栈最灵敏的触底信号(非特权)
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

    def push(self, obj, contact, toward):
        raise NotImplementedError("push 任务挂起(老板指示),M1 不实现")

    def release(self):
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        time.sleep(1.2)

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
        """三值检验(P0-05,破口②):委派 harness.predicates,返回 Predicate。
        检查不了(未覆盖/缺参照/异常)一律 UNKNOWN——不再静默 True。"""
        snap = self._ent_snapshot(constraint)
        pred = predicates.check(constraint, snap, **ctx)
        self._log("verify", name=constraint.get("name"), stage=constraint.get("_stage"),
                  status=pred.status, margin=pred.margin, detail=pred.detail)
        return pred

    def verify(self, constraint: dict) -> bool:
        """契约 bool 接口(gate 用 verify3 拿三值;此处保留兼容)。
        **fail-open 归零**:UNKNOWN 不再返回 True——映射为 False 并显式记 status=UNKNOWN,
        计数进 ledger(不是静默默认);PASS→True / FAIL→False。判定方向由 status 决定,不由异常吞成 True。"""
        pred = self.verify3(constraint)
        return pred.status == predicates.PASS

    # ---------- 旁路:官方谓词快照 ----------
    def probes(self):
        return self.eval.state().get("probes", [])
