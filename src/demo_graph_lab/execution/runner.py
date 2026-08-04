"""Deterministic sequential stage runner shared by fake and oracle runtimes."""

from __future__ import annotations

from ..evaluation import gates


def run_policy(
    stage_handlers: dict,
    graph: dict,
    runtime,
    max_attempts: int = 2,
    strict_gates: bool = True,
) -> dict:
    """Execute ``snapshot → handler → gate`` for each stage.

    A failed stage is retried with the same handler.  No rollback or candidate
    change is implemented, so the report says ``failed_at`` rather than implying
    recovery that did not happen.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

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
            handler(runtime)
            verdict = gates.evaluate(runtime, stage, entry, strict=strict_gates)
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
