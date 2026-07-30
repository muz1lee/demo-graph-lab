"""[compile] compile:graph.json + API 契约 → policy.py(Opus 一次性)→ AST 静态双检 → fake 干跑。"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from . import util


def _contract_methods() -> set[str]:
    from . import contract
    return {n for n, _ in inspect.getmembers(contract.Runtime, inspect.isfunction)
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
    """规则:禁 import;禁数字字面量(STAGES 字典内除外);调用只准 rt.* 且方法在契约内。"""
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
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            errs.append(f"L{node.lineno}: import 禁止")
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool) and id(node) not in exempt:
            errs.append(f"L{node.lineno}: 数字字面量 {node.value!r}(必须走 rt.solve)")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "rt":
                if f.attr not in allowed:
                    errs.append(f"L{node.lineno}: 契约外 API rt.{f.attr}")
            else:
                errs.append(f"L{node.lineno}: 只准调用 rt.* "
                            f"(发现 {ast.dump(f)[:40]})")
    return errs


def dry_run(code: str, graph: dict) -> dict:
    from .fakerun import FakeRuntime, run_policy
    ns: dict = {"__builtins__": {}}
    exec(code, ns)  # noqa: S102 — 已过静态检查的受限模块
    handlers = ns.get("STAGES", {})
    rt = FakeRuntime(graph)
    normal = run_policy(handlers, graph, rt)
    first = graph["stages"][0]["index"] if graph["stages"] else None
    rt2 = FakeRuntime(graph, fail_once_at=first)
    retry = run_policy(handlers, graph, rt2)
    return {"normal": normal, "retry_injection": retry,
            "n_calls": len(rt.calls),
            "holes_solved": sorted({c["hole"] for c in rt.calls if c["op"] == "solve"}),
            "gates_checked": sum(1 for c in rt.calls if c["op"] == "verify"),
            "calls": rt.calls}


def run(task: str, model: str | None = None) -> Path:
    from . import contract, llm
    run_dir = util.latest_run_dir(task)
    graph = util.read_json(run_dir / "graph.json")
    prompt = (util.HARNESS_ROOT / "prompts/compile_policy.md").read_text().split("---", 1)[1]
    msg = (prompt
           + "\n\n## CONTRACT SOURCE\n```python\n" + inspect.getsource(contract) + "```"
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
    util.write_json(run_dir / "compile_report.json", report)
    dr = report.get("dryrun", {})
    print(f"[compile] {task}: static {'PASS' if not violations else violations} | "
          f"dryrun normal={dr.get('normal', {}).get('ok')} "
          f"retry={dr.get('retry_injection', {}).get('ok')} "
          f"holes={len(dr.get('holes_solved', []))} gates={dr.get('gates_checked')}"
          if not violations or dr else f"[compile] {task}: FAIL {violations[:3]}")
    return run_dir / "compile_report.json"
