"""Compile-time fake implementation of the generated policy contract."""

from __future__ import annotations


class Handle:
    """不透明句柄:policy 拿得到、读不出数字。"""

    def __init__(self, kind: str, name: str):
        self.kind, self.name = kind, name

    def __repr__(self):
        return f"<{self.kind}:{self.name}>"


class FakeRuntime:
    """实现高层 RuntimeAPI；动作只记日志，可注入一次 gate 失败。"""

    def __init__(self, graph: dict, fail_once_at: int | None = None):
        self.graph = graph
        self.calls: list[dict] = []
        self._holes_by_stage: dict[int, set[str]] = {}
        for stage in graph["stages"]:
            index = int(stage["index"])
            if index in self._holes_by_stage:
                raise ValueError(f"duplicate stage index: {index}")
            names = [hole["name"] for hole in stage.get("holes", [])]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate hole name in stage {index}")
            self._holes_by_stage[index] = set(names)
        self._active_stage_index: int | None = None
        self._fail_once_at = fail_once_at
        self._failed_once = False

    def _log(self, op, **kw):
        self.calls.append({"op": op, **kw})

    def begin_stage(self, stage: dict) -> None:
        self._active_stage_index = int(stage["index"])

    def solve(self, hole_name):
        declared = self._holes_by_stage.get(self._active_stage_index, set())
        if hole_name not in declared:
            raise ValueError(f"undeclared hole: {hole_name!r}")
        self._log("solve", hole=hole_name)
        return Handle("hole", hole_name)

    def begin_candidates(self, grasp_hole):
        declared = self._holes_by_stage.get(self._active_stage_index, set())
        if grasp_hole not in declared:
            raise ValueError(f"undeclared grasp hole: {grasp_hole!r}")
        self._log("begin_candidates", hole=grasp_hole)

    def rank_by(self, constraint_ref):
        self._log("rank_by", constraint=constraint_ref)

    def require_future(self, constraint_ref):
        self._log("require_future", constraint=constraint_ref)

    def choose(self, grasp_hole):
        self._log("choose", hole=grasp_hole)
        # Dry-run needs only the opaque dataflow shape.  Logging solve here would
        # incorrectly make the compiler report a hidden solve call.
        return Handle("hole", grasp_hole)

    def verify(self, constraint) -> bool:
        self._log("verify", constraint=constraint.get("name"),
                  stage=constraint.get("_stage"))
        if (self._fail_once_at is not None and not self._failed_once
                and not constraint.get("_probe")   # 入口探针不消耗注入的失败
                and constraint.get("_stage") == self._fail_once_at):
            self._failed_once = True
            return False
        return True

    # 非特权信号的最小 stub：lift/lower_until 的判据读 get_xquat/
    # get_ee_extforce。FakeRuntime 干跑路径本身经 __getattr__ 只记日志、不跑真原语体,
    # 通常不会触及这两个;但若有代码路径直接调用,给出行为记日志的最小 stub(而非
    # __getattr__ 的 AttributeError),保持干跑可用。返回中性零信号(无接触/未上移)。
    def get_xquat(self, arm_id=None):
        self._log("get_xquat", arm_id=arm_id)
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]   # xyz + xyzw(单位姿态)

    def get_ee_extforce(self, arm_id=None):
        self._log("get_ee_extforce", arm_id=arm_id)
        return [0.0, 0.0, 0.0]                        # 空载:无接触/无负载

    def __getattr__(self, name):
        # 控制原语统一记日志。
        if name in ("approach", "grasp_at", "lift", "reorient_held_axis",
                    "transport", "align", "lower_until", "release", "retreat"):
            def prim(*a, **kw):
                self._log(name, args=[repr(x) for x in a],
                          kwargs={k: repr(v) for k, v in kw.items()})
            return prim
        raise AttributeError(f"illegal runtime API: {name}")
