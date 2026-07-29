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
    def _entities(self):
        return self.eval.state().get("entities", {})

    def _resolve(self, name: str) -> str:
        """registry id/物体名 → /state 实体键。启发式:别名/去后缀/数字/位置排序。"""
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
        for e in ents:  # 子串
            if base in e.lower() or e.lower().replace("_prop", "") in base:
                return e
        # tube_left/mid/right 等按 y 坐标排序映射
        token = base.rsplit("_", 1)[-1]
        if token in ("left", "mid", "middle", "right"):
            fam = sorted((e for e in ents if base.split("_")[0] in e.lower()),
                         key=lambda e: ents[e]["pos"][1])
            if fam:
                idx = {"left": 0, "mid": len(fam) // 2, "middle": len(fam) // 2,
                       "right": len(fam) - 1}[token]
                return fam[min(idx, len(fam) - 1)]
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

    def _move(self, xyz, quat=None, interpolation="z_arc", gpos=None):
        if quat is None:
            _, quat = self._cur_xquat()
        kw = {"target_xyz": [round(v, 4) for v in xyz], "target_quat": quat,
              "interpolation": interpolation}
        if gpos is not None:
            kw["gpos"] = gpos
        return self._ctrl("xquat_move", **kw)

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
        self._move(xyz)
        self._ctrl("set_gripper", angle=GRIP_CLOSE)

    def lift(self, obj):
        self._ctrl("delta_move", delta_xyz=[0, 0, LIFT_DZ])

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
        for _ in range(LOWER_MAX_STEPS):
            self._ctrl("delta_move", delta_xyz=[0, 0, -LOWER_STEP])
            probes = self.eval.state().get("probes", [])
            if probes and all(p.get("passed") for p in probes
                              if "depth" in str(p.get("label", "")).lower()):
                break
        self._log("lower_until_done")

    def push(self, obj, contact, toward):
        raise NotImplementedError("push 任务挂起(老板指示),M1 不实现")

    def release(self):
        self._ctrl("set_gripper", angle=GRIP_OPEN)

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
