"""Compile-time fake implementation of the PerceptionProgram contract.

干跑只证明链能按契约走完、每个程序发布哪些洞,不产生任何几何数值:handle 是不透明的,
anchor 只被原样记录。真实 grounding/segmentation/几何实现接进来之前,这里是唯一
能验证程序表达力的执行面,和 `policy/fake_runtime.py` 之于 StageProgram 同位。
"""

from __future__ import annotations

from copy import deepcopy

from .program import OPERATORS, program_id, validate_perception_program


class PerceptionHandle:
    """不透明句柄:链上一步拿得到、读不出数字。"""

    def __init__(self, kind: str, name: str):
        self.kind, self.name = kind, name

    def __repr__(self):
        return f"<{self.kind}:{self.name}>"


def qualified_hole(stage: int, hole: str) -> str:
    """洞名只在 stage 内唯一,所以干跑结果用 ``s<stage>.<hole>`` 作键。"""
    return f"s{stage}.{hole}"


class FakePerceptionRuntime:
    """逐程序干跑已校验的 PerceptionProgram;算子只记日志,可注入单点失败。

    ``fail_at=(program_index, op_name)`` 让某个程序在该算子处失败。失败语义是
    all-or-nothing:该程序的全部 provides 都不产出。部分成功会让上层以为洞已填,
    那是 bug,不是可接受的降级。注入没打中时直接报错,不让测试静默通过。
    """

    def __init__(self, graph: dict, fail_at: tuple[int, str] | None = None):
        self.graph = graph
        self.log: list[dict] = []
        self._fail_at = fail_at
        self._fired = False
        self._holes_by_stage: dict[int, dict[str, dict]] = {}
        for stage in graph["stages"]:
            index = int(stage["index"])
            if index in self._holes_by_stage:
                raise ValueError(f"duplicate stage index: {index}")
            holes: dict[str, dict] = {}
            for hole in stage.get("holes", []):
                name = hole["name"]
                if name in holes:
                    raise ValueError(f"duplicate hole name in stage {index}")
                holes[name] = hole
            self._holes_by_stage[index] = holes

    def _log(self, op, **kw):
        self.log.append({"op": op, **kw})

    def run(self, doc: dict) -> dict:
        """Dry-run every program; return published holes, missed holes and the log."""
        violations = validate_perception_program(doc, self.graph)
        if violations:
            raise ValueError(f"PerceptionProgram validation failed: {violations[:3]}")

        self._fired = False
        filled: dict[str, str] = {}
        unfilled: list[str] = []
        for offset, program in enumerate(doc["programs"]):
            stage = program["stage"]
            identity = program_id(stage, offset)
            holes = self._holes_by_stage[stage]
            targets = [
                (qualified_hole(stage, entry["hole"]), entry["field"])
                for entry in program["provides"]
            ]
            # 校验已保证同一程序的洞共享同一个 anchor,取第一个即可。
            anchor = holes[program["provides"][0]["hole"]]["anchor"]

            handle, failed_at = None, None
            for op in program["chain"]:
                if self._fail_at == (offset, op):
                    self._fired, failed_at = True, op
                    self._log("fail", program=identity, stage=stage, at=op)
                    break
                if handle is None:
                    # 链的第一步消费 anchor 而不是 handle;查询文本由可信实现渲染。
                    self._log(op, program=identity, stage=stage,
                              anchor=deepcopy(anchor))
                else:
                    self._log(op, program=identity, stage=stage, input=repr(handle))
                handle = PerceptionHandle(OPERATORS[op]["produces"], f"{identity}.{op}")

            if failed_at is not None:
                unfilled.extend(name for name, _ in targets)
                continue
            for name, field in targets:
                filled[name] = field
                self._log("publish", program=identity, stage=stage,
                          hole=name, field=field, handle=repr(handle))

        if self._fail_at is not None and not self._fired:
            raise ValueError(f"injected failure never fired: {self._fail_at!r}")
        return {"filled": filled, "unfilled": sorted(unfilled), "log": self.log}

