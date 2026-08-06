"""Deterministic sequential stage runner shared by fake and oracle runtimes."""

from __future__ import annotations

from ..evaluation import gates

# 谓词专用输入里,可以从 runtime 自己的调用记录中读出来的三分量世界系向量。
# region_grasp 要抓取点、approach_direction 要接近方向;gate 不传这两个值时，两条
# 谓词永远 UNKNOWN(ep1/ep2 两集实测都是这样)。
_CTX_VECTOR_KEYS = ("grasp_point", "approach_dir")


def _runtime_calls(runtime) -> list:
    """runtime 的调用记录;没有或形态不对就当空(gate 侧照旧 UNKNOWN)。"""
    calls = getattr(runtime, "calls", None)
    return calls if isinstance(calls, list) else []


def _vector3(value):
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return None
    return None


def _stage_ctx(runtime, since: int) -> dict:
    """本次 attempt 里 runtime **实际记下**的抓取点与接近方向,交给 gate 当谓词输入。

    只看 ``calls[since:]``，也就是本次 attempt 期间新增的记录，取每个键的**最近一次**;
    上一阶段或上一次 attempt 的值不能拿来给这一阶段验收。值必须是 runtime 在世界系
    记录的三分量向量,任何别的形态都当作没记(不猜、不换算)。

    runner 只做搬运:runtime 没记就返回空 ctx，那两条谓词维持既有的 UNKNOWN，
    fail-closed 不放松。
    """
    ctx: dict = {}
    for record in reversed(_runtime_calls(runtime)[since:]):
        if not isinstance(record, dict):
            continue
        for key in _CTX_VECTOR_KEYS:
            if key in ctx:
                continue
            vector = _vector3(record.get(key))
            if vector is not None:
                ctx[key] = vector
        if len(ctx) == len(_CTX_VECTOR_KEYS):
            break
    return ctx


def run_policy(
    stage_handlers: dict,
    graph: dict,
    runtime,
    max_attempts: int = 2,
    strict_gates: bool = True,
    resolve_object=None,
) -> dict:
    """Execute ``snapshot → handler → gate`` for each stage.

    A failed stage is retried with the same handler.  No rollback or candidate
    change is implemented, so the report says ``failed_at`` rather than implying
    recovery that did not happen.

    ``resolve_object`` 把图对象名映成实体字典的键,供 gate 的位移检查使用;不给时
    从 runtime 取它自己的 ``_resolve``(``OracleRuntime`` 有,``FakeRuntime`` 没有,
    取不到就保持既有行为)。映射由 runtime 提供而不是 gate 自己猜,见
    ``evaluation.gates.manipulated_entity_key``。

    每次 attempt 结束时,runner 还从 runtime 的调用记录里取本阶段实际执行的抓取点与
    接近方向(见 ``_stage_ctx``)交给 gate,``region_grasp`` 与 ``approach_direction``
    才有输入可判;runtime 没记这两个值时它们维持 UNKNOWN。
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    if resolve_object is None:
        resolve_object = getattr(runtime, "_resolve", None)

    result = {"stages": [], "ok": True}
    for stage in graph["stages"]:
        index = stage["index"]
        handler = stage_handlers.get(index)
        if handler is None:
            result["stages"].append(
                {"index": index, "name": stage["name"], "status": "no_handler"}
            )
            result["ok"] = False
            result["failed_at"] = index
            break

        begin_stage = getattr(runtime, "begin_stage", None)
        if callable(begin_stage):
            begin_stage(stage)

        status, verdict = "failed", None
        for attempt in range(max_attempts):
            entry = gates.snapshot(runtime, stage)
            mark = len(_runtime_calls(runtime))     # 本次 attempt 的记录窗口起点
            handler(runtime)
            verdict = gates.evaluate(runtime, stage, entry, strict=strict_gates,
                                     resolve_object=resolve_object,
                                     ctx=_stage_ctx(runtime, mark))
            if verdict["passed"]:
                status = "passed" if attempt == 0 else f"passed_retry{attempt}"
                break
        else:
            result["ok"] = False

        result["stages"].append(
            {
                "index": index,
                "name": stage["name"],
                "status": status,
                "gate": verdict,
            }
        )
        if status == "failed":
            result["failed_at"] = index
            break

    result["vacuous_pass_total"] = sum(
        (stage.get("gate") or {}).get("vacuous_pass", 0)
        for stage in result["stages"]
    )
    return result
