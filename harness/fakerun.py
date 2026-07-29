"""Fake 运行时 + 可信 runner(两级 ReAct 骨架)。Phase 0 干跑用;Phase 1 换真适配器。"""

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
                and constraint.get("_stage") == self._fail_once_at):
            self._failed_once = True
            return False
        return True

    def __getattr__(self, name):
        # 控制原语统一记日志(approach/grasp_at/lift/transport/align/lower_until/push/release)
        if name in ("approach", "grasp_at", "lift", "transport", "align",
                    "lower_until", "push", "release"):
            def prim(*a, **kw):
                self._log(name, args=[repr(x) for x in a],
                          kwargs={k: repr(v) for k, v in kw.items()})
            return prim
        raise AttributeError(f"illegal runtime API: {name}")


def run_policy(stage_handlers: dict, graph: dict, rt: FakeRuntime,
               max_attempts: int = 2) -> dict:
    """可信 runner:逐阶段 [handler → gate(acceptance 全过)],不过则重试,重试尽则回退标记。
    两级 ReAct 的骨架在此,不在生成代码里。"""
    result = {"stages": [], "ok": True}
    for st in graph["stages"]:
        idx = st["index"]
        handler = stage_handlers.get(idx)
        if handler is None:
            result["stages"].append({"index": idx, "status": "no_handler"})
            result["ok"] = False
            continue
        status = "failed"
        for attempt in range(max_attempts):
            handler(rt)
            gates = [dict(c, _stage=idx) for c in st.get("acceptance", [])]
            if all(rt.verify(g) for g in gates):
                status = "passed" if attempt == 0 else f"passed_retry{attempt}"
                break
        else:
            result["ok"] = False
        result["stages"].append({"index": idx, "name": st["name"], "status": status})
        if status == "failed":
            result["rollback_at"] = idx
            break
    return result
