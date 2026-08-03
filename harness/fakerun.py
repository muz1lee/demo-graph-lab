"""[runtime] Fake 运行时 + 可信 runner(两级 ReAct 骨架)。Phase 0 干跑用;Phase 1 换真适配器。"""

from __future__ import annotations


class Handle:
    """不透明句柄:policy 拿得到、读不出数字。"""

    def __init__(self, kind: str, name: str):
        self.kind, self.name = kind, name

    def __repr__(self):
        return f"<{self.kind}:{self.name}>"


class FakeRuntime:
    """按 contract.Runtime 的签名实现;全部调用进日志;可注入 gate 失败测重试分支。"""

    def __init__(self, graph: dict, fail_once_at: int | None = None):
        self.graph = graph
        self.calls: list[dict] = []
        self._declared_holes = {h["name"] for s in graph["stages"] for h in s.get("holes", [])}
        self._fail_once_at = fail_once_at
        self._failed_once = False

    def _log(self, op, **kw):
        self.calls.append({"op": op, **kw})

    def solve(self, hole_name):
        if hole_name not in self._declared_holes:
            raise ValueError(f"undeclared hole: {hole_name!r}")
        self._log("solve", hole=hole_name)
        return Handle("hole", hole_name)

    def residual(self, constraint):
        self._log("residual", constraint=constraint.get("name"))
        return Handle("residual", constraint.get("name", "?"))

    def verify(self, constraint) -> bool:
        self._log("verify", constraint=constraint.get("name"),
                  stage=constraint.get("_stage"))
        if (self._fail_once_at is not None and not self._failed_once
                and not constraint.get("_probe")   # 入口探针不消耗注入的失败
                and constraint.get("_stage") == self._fail_once_at):
            self._failed_once = True
            return False
        return True

    def __getattr__(self, name):
        # push 与 kwadapter 硬 stub 同语义:D-14 挂起,干跑必须炸,不许被吞成 no-op(P0-06/G4)
        if name == "push":
            def _push_stub(*a, **kw):
                raise NotImplementedError("push 任务挂起(老板指示),M1 不实现")
            return _push_stub
        # 控制原语统一记日志(approach/grasp_at/lift/transport/align/lower_until/release)
        if name in ("approach", "grasp_at", "lift", "transport", "align",
                    "lower_until", "release"):
            def prim(*a, **kw):
                self._log(name, args=[repr(x) for x in a],
                          kwargs={k: repr(v) for k, v in kw.items()})
            return prim
        raise AttributeError(f"illegal runtime API: {name}")


def run_policy(stage_handlers: dict, graph: dict, rt: FakeRuntime,
               max_attempts: int = 2, strict_gates: bool = True) -> dict:
    """可信 runner:逐阶段 [入口快照 → handler → gate],不过则重试,重试尽则回退标记。
    两级 ReAct 的骨架在此,不在生成代码里。
    gate 由 harness.gates 判定:验收约束成立 **且** 世界确有变化(见该模块文档)。"""
    from . import gates as gatemod

    result = {"stages": [], "ok": True}
    for st in graph["stages"]:
        idx = st["index"]
        handler = stage_handlers.get(idx)
        if handler is None:
            result["stages"].append({"index": idx, "status": "no_handler"})
            result["ok"] = False
            continue
        status, verdict = "failed", None
        for attempt in range(max_attempts):
            entry = gatemod.snapshot(rt, st)
            handler(rt)
            verdict = gatemod.evaluate(rt, st, entry, strict=strict_gates)
            if verdict["passed"]:
                status = "passed" if attempt == 0 else f"passed_retry{attempt}"
                break
        else:
            result["ok"] = False
        result["stages"].append({"index": idx, "name": st["name"],
                                 "status": status, "gate": verdict})
        if status == "failed":
            result["rollback_at"] = idx
            break
    result["vacuous_pass_total"] = sum((s.get("gate") or {}).get("vacuous_pass", 0)
                                       for s in result["stages"])
    return result
