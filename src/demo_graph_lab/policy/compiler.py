"""Ask the backend for a StageProgram, then compile and dry-run it locally."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from ..common import artifacts
from ..graph import validate as graph_validate
from .program import compile_program, unwired_holes, validate_program


def report_ready(report: dict) -> bool:
    """Return whether compile contracts and both fake dry-runs passed."""
    dryrun = report.get("dryrun", {})
    return bool(
        report.get("graph_validation") == "passed"
        and not report.get("program_violations")
        and not report.get("static_violations")
        and not report.get("dryrun_error")
        and not report.get("publish_error")
        and isinstance(report.get("compiled_program"), dict)
        and dryrun.get("normal", {}).get("ok") is True
        and dryrun.get("retry_injection", {}).get("ok") is True
    )


def _contract_methods() -> set[str]:
    from . import api
    return {n for n, _ in inspect.getmembers(api.RuntimeAPI, inspect.isfunction)
            if not n.startswith("_")}


def static_check(code: str) -> list[str]:
    """Restrict generated code to opaque handles and direct ``rt.*`` calls."""
    errs = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "STAGES" for t in node.targets):
            exempt |= {id(n) for n in ast.walk(node)}
    allowed = _contract_methods()
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            errs.append(f"L{node.lineno}: import 禁止")
        elif isinstance(node, ast.Subscript):
            errs.append(f"L{node.lineno}: 禁止下标读取；rt.solve 返回值是不透明 handle")
        elif isinstance(node, ast.Attribute):
            parent = parents.get(node)
            is_direct_rt_call = (
                isinstance(parent, ast.Call)
                and parent.func is node
                and isinstance(node.value, ast.Name)
                and node.value.id == "rt"
            )
            if is_direct_rt_call:
                if node.attr not in allowed:
                    errs.append(f"L{node.lineno}: 契约外 API rt.{node.attr}")
            else:
                errs.append(f"L{node.lineno}: 禁止属性读取；只能直接调用公开的 rt.* 方法")
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool) and id(node) not in exempt:
            errs.append(f"L{node.lineno}: 数字字面量 {node.value!r}(必须走 rt.solve)")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "rt":
                continue
            else:
                errs.append(f"L{node.lineno}: 只准调用 rt.* "
                            f"(发现 {ast.dump(f)[:40]})")
    return errs


def load_handlers(code: str, graph: dict) -> dict:
    """Validate policy code and return one callable handler per graph stage."""
    violations = static_check(code)
    if violations:
        raise ValueError(f"policy failed static validation: {violations[:3]}")

    indices = [stage["index"] for stage in graph.get("stages", [])]
    if len(indices) != len(set(indices)):
        raise ValueError("graph contains duplicate stage indices")

    namespace: dict = {"__builtins__": {}}
    exec(code, namespace)  # noqa: S102 - checked immediately above
    handlers = namespace.get("STAGES")
    if not isinstance(handlers, dict):
        raise ValueError("policy must define a STAGES dict")
    if set(handlers) != set(indices):
        raise ValueError(
            "policy stage handlers do not match graph: "
            f"expected={sorted(indices)} got={sorted(handlers)}"
        )
    if not all(callable(handler) for handler in handlers.values()):
        raise ValueError("every STAGES value must be callable")
    return handlers


def dry_run(code: str, graph: dict) -> dict:
    from .fake_runtime import FakeRuntime
    from ..execution.runner import run_policy
    handlers = load_handlers(code, graph)
    rt = FakeRuntime(graph)
    normal = run_policy(handlers, graph, rt, strict_gates=False)
    first = graph["stages"][0]["index"] if graph["stages"] else None
    rt2 = FakeRuntime(graph, fail_once_at=first)
    retry = run_policy(handlers, graph, rt2, strict_gates=False)
    return {"normal": normal, "retry_injection": retry,
            "n_calls": len(rt.calls),
            "holes_solved": sorted({c["hole"] for c in rt.calls if c["op"] == "solve"}),
            "gates_checked": sum(1 for c in rt.calls if c["op"] == "verify"),
            "calls": rt.calls}


def run(task: str, model: str | None = None) -> Path:
    from ..common import llm
    from . import api
    run_dir = artifacts.latest_run_dir(task)
    policy_path = run_dir / "policy.py"
    program_path = run_dir / "stage_program.json"
    graph_snapshot_path = run_dir / "compiled_graph.json"
    objects_snapshot_path = run_dir / "compiled_objects.json"
    compile_report_path = run_dir / "compile_report.json"
    policy_path.unlink(missing_ok=True)
    program_path.unlink(missing_ok=True)
    graph_snapshot_path.unlink(missing_ok=True)
    objects_snapshot_path.unlink(missing_ok=True)
    compile_report_path.unlink(missing_ok=True)
    graph = artifacts.read_json(run_dir / "graph.json")
    validation_path = run_dir / "validation.json"
    report = {
        "task": task,
        "graph_validation": "passed",
        "program_violations": [],
        "static_violations": [],
        "unwired_holes": [],
    }
    if not validation_path.exists():
        report["graph_validation"] = "missing"
        artifacts.write_json(run_dir / "compile_report.json", report)
        print(f"[compile] {task}: FAIL graph validation artifact missing")
        return run_dir / "compile_report.json"
    validation = artifacts.read_json(validation_path)
    if validation.get("passed") is not True:
        report["graph_validation"] = "failed"
        report["graph_violations"] = validation.get("violations", [])
        artifacts.write_json(run_dir / "compile_report.json", report)
        print(f"[compile] {task}: FAIL graph validation did not pass")
        return run_dir / "compile_report.json"
    validation = graph_validate.validate_run_dir(run_dir, task)
    if validation.get("passed") is not True:
        report["graph_validation"] = "failed"
        report["graph_violations"] = validation.get("violations", [])
        artifacts.write_json(run_dir / "compile_report.json", report)
        print(f"[compile] {task}: FAIL current graph validation did not pass")
        return run_dir / "compile_report.json"
    try:
        graph = artifacts.read_json(run_dir / "graph.json")
        objects = artifacts.read_json(run_dir / "objects.json")
    except (OSError, ValueError) as error:
        report["graph_validation"] = "failed"
        report["graph_violations"] = [
            f"validated inputs could not be frozen: {type(error).__name__}: {error}"
        ]
        artifacts.write_json(run_dir / "compile_report.json", report)
        print(f"[compile] {task}: FAIL validated inputs could not be frozen")
        return run_dir / "compile_report.json"

    prompt = (artifacts.PROMPT_ROOT / "compile_policy.md").read_text().split("---", 1)[1]
    msg = (prompt
           + "\n\n## CONTRACT SOURCE\n```python\n" + inspect.getsource(api) + "```"
           + "\n\n## GRAPH JSON\n```json\n"
           + json.dumps(graph, ensure_ascii=False, indent=1) + "\n```")
    tag = "compile"
    messages = [{"role": "user", "content": msg}]
    input_refs = ["graph.json", "package:policy/api.py",
                  "package:prompts/compile_policy.md"]
    request = llm.request_record(
        messages, tag=tag, role="policy_program", model=llm.resolve_model(model),
        max_tokens=4000, temperature=0.1, input_refs=input_refs)
    out = llm.cached_response(run_dir, tag, request)
    if out is None:
        out = llm.chat(
            messages, run_dir, tag=tag, model=model, max_tokens=4000,
            temperature=0.1, role="policy_program", input_refs=input_refs)
    try:
        program = llm.parse_json_block(out)
    except ValueError as error:
        llm.record_result(run_dir, tag, parse_error=str(error))
        report["program_violations"] = [str(error)]
        artifacts.write_json(run_dir / "compile_report.json", report)
        print(f"[compile] {task}: FAIL invalid StageProgram JSON")
        return run_dir / "compile_report.json"

    artifacts.write_json(program_path, program)
    program_violations = validate_program(program, graph)
    llm.record_result(
        run_dir, tag, parsed=program, validation_errors=program_violations)
    report["program_violations"] = program_violations
    if not program_violations:
        report["unwired_holes"] = unwired_holes(program, graph)
        code = compile_program(program, graph)
        violations = static_check(code)
        report["static_violations"] = violations
        if not violations:
            try:
                report["dryrun"] = dry_run(code, graph)
            except Exception as e:
                report["dryrun_error"] = f"{type(e).__name__}: {e}"
            else:
                normal = report["dryrun"].get("normal", {}).get("ok") is True
                retry = report["dryrun"].get("retry_injection", {}).get("ok") is True
                if normal and retry:
                    try:
                        current_graph = artifacts.read_json(run_dir / "graph.json")
                        current_objects = artifacts.read_json(run_dir / "objects.json")
                        if current_graph != graph or current_objects != objects:
                            raise RuntimeError(
                                "graph or object registry changed during compilation"
                            )
                        artifacts.write_json(graph_snapshot_path, graph)
                        artifacts.write_json(objects_snapshot_path, objects)
                        policy_path.write_text(code)
                        report["compiled_program"] = program
                    except Exception as error:
                        report["publish_error"] = f"{type(error).__name__}: {error}"
                else:
                    report["dryrun_error"] = "normal or retry-injection dry-run failed"
    artifacts.write_json(run_dir / "compile_report.json", report)
    dr = report.get("dryrun", {})
    violations = report["program_violations"] or report["static_violations"]
    terminal_error = report.get("dryrun_error") or report.get("publish_error")
    if violations:
        print(f"[compile] {task}: FAIL {violations[:3]}")
    elif terminal_error:
        print(f"[compile] {task}: FAIL {terminal_error}")
    else:
        print(f"[compile] {task}: contracts PASS | "
              f"dryrun normal={dr.get('normal', {}).get('ok')} "
              f"retry={dr.get('retry_injection', {}).get('ok')} "
              f"holes={len(dr.get('holes_solved', []))} "
              f"gates={dr.get('gates_checked')}")
    return run_dir / "compile_report.json"
