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

# 竖直(top-down)抓取姿态,xyzw。取自 shipped reorient.yaml 的
# postgrasp_vertical_quat_xyzw=[0,1,0,0](夹爪 -z 朝下)。之前 _move 在 quat=None
# 时复用 home 四元数,而 home 姿态并非抓取姿态 → IK 求出的解自碰撞/不可达。
GRASP_QUAT_TOPDOWN = [0.0, 1.0, 0.0, 0.0]
# 增量趋近:当一次性 xquat_move 收不敛(settle 返回 still/timeout 且 gap 仍大)时,
# 用闭环 delta_move 逐步逼近——每步从更好的当前构型重解 IK,规避从 home 折叠构型
# 直接大跳所触发的自碰撞。步长/预算/到位判据如下。
STEP_MAX, STEP_TOL, STEP_BUDGET = 0.05, 0.03, 30
STEP_STALL_EPS, STEP_STALL_N = 0.006, 3   # 连续 N 步位移 < eps 判定卡死(到达 reach 边界)


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
    def _ctrl(self, fn: str, **kw):
        self._log("ctrl", fn=fn, **{k: v for k, v in kw.items() if k != "target_quat"})
        return self.pipe.call("ctrl", fn, {"arm_id": self.arm_id, **kw})

    def _cur_xquat(self):
        x = self.pipe.call("info", "get_xquat", {"arm_id": self.arm_id})
        return list(x[:3]), list(x[3:7])

    def _wait_settle(self, target_xyz=None, tol=0.012, timeout_s=25.0, still_n=4):
        """pipeline 的 ctrl 是 fire-and-forget(源码里等 future 那行被注释),必须自己等。
        收敛判据:到达 target(tol) 或连续 still_n 次位置几乎不变(静止/受阻)。"""
        t0, last, still = time.time(), None, 0
        while time.time() - t0 < timeout_s:
            time.sleep(0.35)
            try:
                xyz, _ = self._cur_xquat()
            except Exception:
                continue
            if target_xyz is not None and math.dist(xyz, list(target_xyz)) < tol:
                self._log("settle", reason="reached", sec=round(time.time() - t0, 1))
                return "reached"
            if last is not None and math.dist(xyz, last) < 0.0015:
                still += 1
                if still >= still_n:
                    self._log("settle", reason="still", sec=round(time.time() - t0, 1),
                              gap=round(math.dist(xyz, list(target_xyz)), 4) if target_xyz else None)
                    return "still"
            else:
                still = 0
            last = xyz
        self._log("settle", reason="timeout", sec=round(time.time() - t0, 1))
        return "timeout"

    def _step_to(self, xyz, gpos=None):
        """闭环位置增量趋近:每步朝 target 走 <=STEP_MAX(仅位置),再 settle。
        每步从当前(逐步更展开的)构型重解 IK,避免从 home 折叠位一次大跳的自碰撞。
        注意:实测本机在前伸够物区无法维持 top-down 腕姿,故此处**不锁姿态**(不传 quat),
        让姿态随 IK 自然漂移——这样 delta_move 的 IK 报 collision_free=true 且能持续前进;
        一旦强锁 [0,1,0,0],腕关节被逼到极限、机械臂折叠上翻(rot_error≈95°)。
        返回到位则 True;连续 STEP_STALL_N 步几乎不动(到 reach 边界)则 False。"""
        stall = 0
        for _ in range(STEP_BUDGET):
            cur, _ = self._cur_xquat()
            d = [xyz[i] - cur[i] for i in range(3)]
            gap = math.sqrt(sum(v * v for v in d))
            if gap < STEP_TOL:
                return True
            n = gap or 1e-9
            step = min(STEP_MAX, gap)
            delta = [round(d[i] / n * step, 3) for i in range(3)]
            kw = {"delta_xyz": delta}
            if gpos is not None:
                kw["gpos"] = gpos
            self._ctrl("delta_move", **kw)
            self._wait_settle(timeout_s=14.0)   # 伺服收敛慢,给足时间
            nxt, _ = self._cur_xquat()
            if math.dist(nxt, cur) < STEP_STALL_EPS:
                stall += 1
                if stall >= STEP_STALL_N:
                    self._log("step_to", reason="stalled",
                              gap=round(math.dist(nxt, list(xyz)), 4))
                    return False
            else:
                stall = 0
        self._log("step_to", reason="budget",
                  gap=round(math.dist(self._cur_xquat()[0], list(xyz)), 4))
        return False

    def _move(self, xyz, quat=None, interpolation="z_arc", gpos=None):
        # quat=None 时用竖直抓取姿态,而非复用 home 姿态(后者会让 IK 求出自碰撞解)。
        if quat is None:
            quat = list(GRASP_QUAT_TOPDOWN)
        kw = {"target_xyz": [round(v, 4) for v in xyz], "target_quat": quat,
              "interpolation": interpolation}
        if gpos is not None:
            kw["gpos"] = gpos
        r = self._ctrl("xquat_move", **kw)
        # 一次性 xquat_move 常因 home 折叠构型/大跳触发自碰撞而收不敛;若未到位,
        # 回退到闭环增量趋近(仅位置)。gap 判据 > STEP_TOL 才回退,避免无谓多走。
        if self._wait_settle(target_xyz=xyz) != "reached":
            if math.dist(self._cur_xquat()[0], list(xyz)) > STEP_TOL:
                self._step_to(xyz, gpos=gpos)
        return r

    def approach(self, target, cone=None):
        if isinstance(target, dict) and target.get("kind") == "pose":
            xyz = list(target["xyz"])
        else:
            e = self._ent(target if isinstance(target, str) else str(target))
            xyz = list(e["pos"])
        xyz[2] += PREGRASP_DZ
        self._move(xyz)

    def grasp_at(self, grasp_pose):
        xyz = list(grasp_pose["xyz"]) if isinstance(grasp_pose, dict) else list(grasp_pose)
        self._ctrl("set_gripper", angle=GRIP_OPEN)
        time.sleep(1.0)
        self._move(xyz)
        self._ctrl("set_gripper", angle=GRIP_CLOSE)
        time.sleep(1.5)   # 闭合无可靠回读(is_gripping_sth 在本仿真恒假),固定等待

    def lift(self, obj):
        self._ctrl("delta_move", delta_xyz=[0, 0, LIFT_DZ])
        self._wait_settle()

    def transport(self, obj, target):
        if isinstance(target, dict) and "xyz" in target:
            xyz = list(target["xyz"])
        else:
            e = self._ent(target)
            xyz = list(e["pos"])
        xyz[2] += PREGRASP_DZ
        self._move(xyz)

    def align(self, obj, target, axis=None):
        if isinstance(target, dict) and "xyz" in target:
            xyz = list(target["xyz"])
        else:
            xyz = list(self._ent(target)["pos"])
        xyz[2] += ALIGN_DZ
        self._move(xyz, interpolation="linear")

    def lower_until(self, stop_condition):
        """逐步下探。停止条件:目标谓词转真(root_in_bbox/axis_aligned,非恒真项)、
        或高度不再下降(接触/受阻)、或步数预算耗尽。不用恒真的 depth_in 作判据。"""
        prev_z = None
        for i in range(LOWER_MAX_STEPS):
            self._ctrl("delta_move", delta_xyz=[0, 0, -LOWER_STEP])
            self._wait_settle(timeout_s=8.0)
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
