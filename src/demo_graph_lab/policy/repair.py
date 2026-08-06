"""Show the model one failed episode of its own program and let it revise that program.

修复回路只作用在**模型自己的产物**上:输入是已发布的编译产物加一份 episode 报告,
输出是修订版 StageProgram。graph、约束、验收条件和 gate 判据是示范与可信评测的证词,
不在可改集合里——它们根本不在模型的输出 schema 中,validator 再兜一道。

修订版走与 ``policy/compiler.py`` **完全相同**的发布门(零 program violation + 确定性
重编译 + AST 静态检查 + FakeRuntime 双干跑),并且写进独立的 ``repairs/r<N>/``:原发布
产物一行不动,想执行修订版必须显式 ``--program-dir`` 指过去,执行前的一致性门照跑。

口径提醒:episode 报告目前来自 ``OracleRuntime``(特权调试)。由这种 episode 驱动的
修复继承**第 3 档「privileged Oracle 调试」**,不构成方法性能;``banner`` 因此一路带进
摘要与台账。
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

from ..common import artifacts
from .compiler import compile_perception, dry_run, report_ready, static_check
from .program import (
    ARGUMENT_SPECS,
    PRIMITIVES,
    compile_program,
    unwired_holes,
    validate_program,
)

# 每个 run 目录最多三次修复:计的是**尝试**次数,被拒的修订同样占一格。
MAX_REPAIRS = 3
# 摘要里保留的调用流水尾长。执行在失败阶段就停,所以尾部就是失败现场。
SUMMARY_TAIL_CALLS = 12

# 进摘要的 gate 判据字段:结论、三值状态与被点名的键。世界坐标与位移数值不进摘要,
# 只有 gate 自己写的 ``reason`` 里可能带一个量级。
VERDICT_FIELDS = (
    "acceptance_hold",
    "constraints_hold",
    "violated_midway",
    "unknown_keys",
    "vacuous_keys",
    "needs_effect",
    "effect_observable",
    "effect_status",
    "manipulated",
    "manipulated_entity",
    "top_mover",
    "reason",
)

REPLY_KEYS = frozenset({"attribution", "program"})


def episode_fingerprint(report: dict) -> str:
    """Canonical fingerprint of one episode report, independent of file formatting."""
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
    return f"blake2b:{hashlib.blake2b(payload, digest_size=16).hexdigest()}"


def _probe_diff(before, after) -> list[dict]:
    order: list = []
    merged: dict = {}
    for probes, key in ((before, "before"), (after, "after")):
        for probe in probes or []:
            if not isinstance(probe, dict):
                continue
            label = probe.get("label")
            if label not in merged:
                merged[label] = {"label": label, "before": None, "after": None}
                order.append(label)
            merged[label][key] = probe.get("passed")
    return [merged[label] for label in order]


def summarize_episode(report: dict) -> dict:
    """Distill one episode report into the fixed, token-bounded repair input.

    确定性:同一份报告永远得到同一份摘要。墙钟字段(调用记录里的 ``t``)被丢弃,正是
    因为它会让"同一次失败"看起来每次都不同。整份 JSON 不灌进 prompt——需要的是失败
    位置、判据结论和失败现场的调用尾巴。
    """
    if not isinstance(report, dict):
        raise ValueError("episode report: 顶层必须是对象")
    result = report.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("stages"), list):
        raise ValueError("episode report: 缺少 result.stages")
    stages = [stage for stage in result["stages"] if isinstance(stage, dict)]
    if result.get("ok") is True:
        raise ValueError("episode 没有失败 stage;修复回路只在失败轨迹上运行")

    failed_index = result.get("failed_at")
    failed = next(
        (stage for stage in stages if stage.get("index") == failed_index),
        next((stage for stage in stages
              if stage.get("status") in ("failed", "no_handler")), None),
    )
    verdict = (failed or {}).get("gate") or {}
    calls = [call for call in report.get("calls", []) or [] if isinstance(call, dict)]
    return {
        "banner": report.get("banner"),
        "task": report.get("task"),
        "task_id": report.get("task_id"),
        "failed_stage": None if failed is None else {
            "index": failed.get("index"),
            "name": failed.get("name"),
            "status": failed.get("status"),
        },
        "gate": {field: verdict[field] for field in VERDICT_FIELDS if field in verdict},
        "stages": [{
            "index": stage.get("index"),
            "name": stage.get("name"),
            "status": stage.get("status"),
            "reason": (stage.get("gate") or {}).get("reason"),
        } for stage in stages],
        "probes": _probe_diff(
            report.get("probes_before"), report.get("probes_after")),
        "n_calls": len(calls),
        "calls_tail": [
            {key: value for key, value in call.items() if key != "t"}
            for call in calls[-SUMMARY_TAIL_CALLS:]
        ],
    }


def _render_primitive_table() -> str:
    """Render the primitive closed set from code; the prompt keeps no second copy."""
    from .api import RuntimeAPI

    lines = ["| primitive | argument | accepts |", "|---|---|---|"]
    for op in PRIMITIVES:
        specs = ARGUMENT_SPECS[op]
        if not specs:
            lines.append(f"| `{op}` | — | no arguments |")
            continue
        signature = inspect.signature(getattr(RuntimeAPI, op))
        optional = {
            name for name, parameter in signature.parameters.items()
            if parameter.default is not inspect.Parameter.empty
        }
        for name in specs:
            spec = specs[name]
            accepts = []
            if spec.get("holes"):
                accepts.append("hole of type " + " / ".join(
                    f"`{hole_type}`" for hole_type in sorted(spec["holes"])))
            if spec.get("object"):
                accepts.append("stage object")
            if spec.get("values"):
                accepts.append("one of " + ", ".join(
                    f"`{value}`" for value in sorted(spec["values"])))
            if spec.get("purpose"):
                accepts.append(f"hole `purpose` must be `{spec['purpose']}`")
            if spec.get("semantic") == "retreat_target":
                accepts.append("hole must declare retract/retreat semantics")
            suffix = " (optional)" if name in optional else ""
            lines.append(f"| `{op}` | `{name}`{suffix} | {'; '.join(accepts)} |")
    return "\n".join(lines)


def _source_program_dir(run_dir: Path, report: dict) -> Path:
    """Which published program produced this episode: the run itself or one repair."""
    reference = report.get("program_dir")
    if reference in (None, "", "."):
        return run_dir
    if not isinstance(reference, str):
        raise ValueError(f"episode report: program_dir 必须是字符串,实际为 {reference!r}")
    parts = Path(reference).parts
    if len(parts) != 2 or parts[0] != "repairs" or ".." in parts:
        raise ValueError(
            f"episode report: program_dir 只能是 run 目录自身或 repairs/r<N>,"
            f"实际为 {reference!r}"
        )
    return run_dir / reference


def _ledger_path(run_dir: Path) -> Path:
    return run_dir / "repairs" / "repair_ledger.json"


def _read_ledger(run_dir: Path) -> dict:
    path = _ledger_path(run_dir)
    if not path.exists():
        return {"max_repairs": MAX_REPAIRS, "repairs": []}
    ledger = artifacts.read_json(path)
    if not isinstance(ledger, dict) or not isinstance(ledger.get("repairs"), list):
        raise ValueError(f"repair ledger 损坏,拒绝继续: {path}")
    return ledger


def _prompt(graph: dict, program: dict, summary: dict) -> str:
    body = (artifacts.PROMPT_ROOT / "repair_policy.md").read_text().split("---", 1)[1]
    return (body
            + "\n\n## PRIMITIVE CONTRACT\n" + _render_primitive_table()
            + "\n\n## GRAPH JSON\n```json\n"
            + json.dumps(graph, ensure_ascii=False, indent=1) + "\n```"
            + "\n\n## CURRENT STAGE PROGRAM\n```json\n"
            + json.dumps(program, ensure_ascii=False, indent=1) + "\n```"
            + "\n\n## FAILED EPISODE SUMMARY\n```json\n"
            + json.dumps(summary, ensure_ascii=False, indent=1) + "\n```")


def validate_reply(doc, program: dict, graph: dict) -> tuple[str, dict, list[str]]:
    """Return attribution, revised program and every contract violation found."""
    if not isinstance(doc, dict):
        return "", {}, ["repair: 顶层必须是对象"]
    violations = []
    missing = sorted(REPLY_KEYS - set(doc))
    extra = sorted(set(doc) - REPLY_KEYS)
    if missing:
        violations.append(f"repair: 缺少字段 {missing}")
    if extra:
        violations.append(f"repair: 未知字段 {extra}")
    attribution = doc.get("attribution")
    if not isinstance(attribution, str) or not attribution.strip():
        violations.append("repair.attribution: 必须是非空字符串")
        attribution = ""
    revised = doc.get("program")
    if not isinstance(revised, dict):
        return attribution, {}, violations + ["repair.program: 必须是 StageProgram 对象"]
    violations.extend(validate_program(revised, graph))
    if revised == program:
        violations.append("repair.program: 与当前 StageProgram 完全相同,没有提出修复")
    return attribution, revised, violations


def _preflight(run_dir: Path, program_dir: Path) -> tuple[dict, dict, dict]:
    """Reuse the execution-side consistency gates: repairing stale artifacts is moot."""
    from ..execution.cli import _load_artifacts

    graph, objects, _ = _load_artifacts(run_dir, program_dir)
    program = artifacts.read_json(program_dir / "stage_program.json")
    return graph, objects, program


def run(run_dir, episode, model: str | None = None) -> Path:
    """Ask the backend for one revision of its own program; publish under the same gate."""
    from ..common import llm

    run_dir = Path(run_dir).expanduser()
    episode_path = Path(episode).expanduser()
    episode_report = artifacts.read_json(episode_path)
    summary = summarize_episode(episode_report)
    program_dir = _source_program_dir(run_dir, episode_report)
    graph, objects, program = _preflight(run_dir, program_dir)

    ledger = _read_ledger(run_dir)
    if len(ledger["repairs"]) >= MAX_REPAIRS:
        raise ValueError(
            f"repair 上限已达 {MAX_REPAIRS} 次(已记录 {len(ledger['repairs'])} 次),"
            f"拒绝继续: {_ledger_path(run_dir)}"
        )
    index = len(ledger["repairs"]) + 1
    out_dir = run_dir / "repairs" / f"r{index}"
    out_dir.mkdir(parents=True, exist_ok=True)
    source_ref = "." if program_dir == run_dir else str(program_dir.relative_to(run_dir))

    tag = f"repair_r{index}"
    messages = [{"role": "user", "content": _prompt(graph, program, summary)}]
    input_refs = ["graph.json",
                  f"{source_ref}/stage_program.json".removeprefix("./"),
                  episode_path.name,
                  "package:prompts/repair_policy.md"]
    request = llm.request_record(
        messages, tag=tag, role="repair_policy_program",
        model=llm.resolve_model(model), max_tokens=4000, temperature=0.1,
        input_refs=input_refs)
    out = llm.cached_response(run_dir, tag, request)
    if out is None:
        out = llm.chat(
            messages, run_dir, tag=tag, model=model, max_tokens=4000,
            temperature=0.1, role="repair_policy_program", input_refs=input_refs)

    fingerprint = episode_fingerprint(episode_report)
    report = {
        "task": graph.get("task"),
        "graph_validation": "passed",
        "program_violations": [],
        "static_violations": [],
        "unwired_holes": [],
        "repair": {
            "index": index,
            "source_program": source_ref,
            "episode": episode_path.name,
            "episode_fingerprint": fingerprint,
            "banner": summary.get("banner"),
            "attribution": "",
        },
    }
    try:
        doc = llm.parse_json_block(out)
    except ValueError as error:
        llm.record_result(run_dir, tag, parse_error=str(error))
        report["program_violations"] = [str(error)]
        return _finish(run_dir, out_dir, ledger, report, None)

    attribution, revised, violations = validate_reply(doc, program, graph)
    llm.record_result(run_dir, tag, parsed=doc, validation_errors=violations)
    report["repair"]["attribution"] = attribution
    report["program_violations"] = violations
    if not violations:
        report["unwired_holes"] = unwired_holes(revised, graph)
        code = compile_program(revised, graph)
        report["static_violations"] = static_check(code)
        if not report["static_violations"]:
            try:
                report["dryrun"] = dry_run(code, graph)
            except Exception as error:
                report["dryrun_error"] = f"{type(error).__name__}: {error}"
            else:
                normal = report["dryrun"].get("normal", {}).get("ok") is True
                retry = report["dryrun"].get("retry_injection", {}).get("ok") is True
                if not (normal and retry):
                    report["dryrun_error"] = "normal or retry-injection dry-run failed"
                else:
                    try:
                        if (artifacts.read_json(run_dir / "graph.json") != graph
                                or artifacts.read_json(
                                    run_dir / "objects.json") != objects):
                            raise RuntimeError(
                                "graph or object registry changed during repair")
                        artifacts.write_json(out_dir / "compiled_graph.json", graph)
                        artifacts.write_json(out_dir / "compiled_objects.json", objects)
                        artifacts.write_json(out_dir / "stage_program.json", revised)
                        (out_dir / "policy.py").write_text(code)
                        report["compiled_program"] = revised
                    except Exception as error:
                        report["publish_error"] = f"{type(error).__name__}: {error}"
    # 感知段与 compile 同规:只在 StageProgram 发布之后跑,覆盖目标来自新的 hole wiring。
    if report_ready(report):
        report["perception_program"] = compile_perception(
            run_dir, report["task"], graph, revised, model,
            out_dir=out_dir, tag=f"repair_perception_r{index}")
    return _finish(run_dir, out_dir, ledger, report, attribution)


def _finish(run_dir: Path, out_dir: Path, ledger: dict, report: dict,
            attribution: str | None) -> Path:
    """Write the repair artifacts and the ledger entry, published or not."""
    published = report_ready(report) and (out_dir / "policy.py").exists()
    if attribution:
        (out_dir / "attribution.txt").write_text(attribution + "\n")
    artifacts.write_json(out_dir / "compile_report.json", report)
    ledger["max_repairs"] = MAX_REPAIRS
    ledger["repairs"].append({
        **report["repair"],
        "ref": str(out_dir.relative_to(run_dir)),
        "published": published,
        "violations": (report["program_violations"] + report["static_violations"]
                       + [error for error in (report.get("dryrun_error"),
                                              report.get("publish_error")) if error]),
        "perception_program": (report.get("perception_program") or {}).get("status"),
    })
    artifacts.write_json(_ledger_path(run_dir), ledger)
    entry = ledger["repairs"][-1]
    status = "PUBLISHED" if published else "REJECTED"
    print(f"[repair] r{entry['index']} {report['task']}: {status} "
          f"episode={entry['episode']} "
          f"violations={entry['violations'][:2]} "
          f"perception={entry['perception_program']}")
    return out_dir / "compile_report.json"
