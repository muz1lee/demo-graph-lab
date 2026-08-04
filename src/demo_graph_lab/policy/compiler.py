"""Compile a graph into a constrained policy, then check and dry-run it."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from ..common import artifacts


def _contract_methods() -> set[str]:
    from . import api
    return {n for n, _ in inspect.getmembers(api.RuntimeAPI, inspect.isfunction)
            if not n.startswith("_")}


def extract_code(text: str) -> str:
    t = text.strip()
    if "```" in t:
        seg = t.split("```", 2)[1]
        if seg.startswith("python"):
            seg = seg[len("python"):]
        return seg.strip()
    return t


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
    graph = artifacts.read_json(run_dir / "graph.json")
    prompt = (artifacts.PROMPT_ROOT / "compile_policy.md").read_text().split("---", 1)[1]
    msg = (prompt
           + "\n\n## CONTRACT SOURCE\n```python\n" + inspect.getsource(api) + "```"
           + "\n\n## GRAPH JSON\n```json\n"
           + json.dumps(graph, ensure_ascii=False, indent=1) + "\n```")
    out = llm.chat([{"role": "user", "content": msg}], run_dir, tag="compile",
                   model=model, max_tokens=4000, temperature=0.1)
    code = extract_code(out)
    (run_dir / "policy.py").write_text(code)
    violations = static_check(code)
    report = {"task": task, "static_violations": violations}
    if not violations:
        try:
            report["dryrun"] = dry_run(code, graph)
        except Exception as e:
            report["dryrun_error"] = f"{type(e).__name__}: {e}"
    artifacts.write_json(run_dir / "compile_report.json", report)
    dr = report.get("dryrun", {})
    print(f"[compile] {task}: static {'PASS' if not violations else violations} | "
          f"dryrun normal={dr.get('normal', {}).get('ok')} "
          f"retry={dr.get('retry_injection', {}).get('ok')} "
          f"holes={len(dr.get('holes_solved', []))} gates={dr.get('gates_checked')}"
          if not violations or dr else f"[compile] {task}: FAIL {violations[:3]}")
    return run_dir / "compile_report.json"
