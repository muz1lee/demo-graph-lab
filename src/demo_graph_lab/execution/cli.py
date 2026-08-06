"""Oracle execution CLI:

  dgl-oracle smoke   --task-id robodojo_insert_tubes_000
  dgl-oracle episode --run-dir runs/insert_tubes/<timestamp> \\
      --task-id robodojo_insert_tubes_000 [--arm 1] [--program-dir <dir>]

smoke:health → reset → state 摘要 → get_xquat 回读(不动机器人)。
episode:加载编译产物，用 OracleRuntime 顺序执行并产出特权调试报告。
  --program-dir 显式指向要执行的那份编译产物(默认 --run-dir 自己;修订版在
  <run-dir>/repairs/r<N>),一致性门对它照样全跑。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..policy.compiler import load_handlers, report_ready
from ..policy.program import compile_program, validate_program
from .oracle_runtime import ORACLE_BANNER, OracleRuntime
from .runner import run_policy


def _load_artifacts(run_dir: Path, program_dir: Path | None = None):
    """Load one run's demo truth plus one published compile product, or refuse.

    ``program_dir`` 默认就是 ``run_dir``;显式给出时(例如 ``repairs/r1``)编译产物换成
    那个目录的,graph、validation 与 objects 仍然只从 run 目录读——修订版必须是**对同一份
    示范**的编译,所以下面的一致性门是拿修订目录的快照去比 run 目录的真值。
    """
    run_dir = run_dir.expanduser()
    program_dir = run_dir if program_dir is None else Path(program_dir).expanduser()
    graph = json.loads((run_dir / "graph.json").read_text())
    validation = json.loads((run_dir / "validation.json").read_text())
    if validation.get("passed") is not True:
        raise ValueError("refusing execution: graph validation did not pass")
    report = json.loads((program_dir / "compile_report.json").read_text())
    if not report_ready(report):
        raise ValueError("refusing execution: compile report is not ready")
    compiled_graph = json.loads((program_dir / "compiled_graph.json").read_text())
    if compiled_graph != graph:
        raise ValueError("refusing execution: graph changed after policy compilation")
    objects_path = run_dir / "objects.json"
    objects = json.loads(objects_path.read_text()) if objects_path.exists() else []
    compiled_objects = json.loads((program_dir / "compiled_objects.json").read_text())
    if compiled_objects != objects:
        raise ValueError("refusing execution: object registry changed after compilation")
    program = json.loads((program_dir / "stage_program.json").read_text())
    if report.get("compiled_program") != program:
        raise ValueError(
            "refusing execution: StageProgram changed after compile dry-run"
        )
    program_violations = validate_program(program, graph)
    if program_violations:
        raise ValueError(
            f"refusing execution: StageProgram is invalid: {program_violations[:3]}"
        )
    if any(
        action.get("op") == "retreat"
        for stage in program.get("stages", [])
        for action in stage.get("actions", [])
        if isinstance(action, dict)
    ):
        raise ValueError(
            "refusing execution: retreat solver is unavailable; no episode was started"
        )
    code = (program_dir / "policy.py").read_text()
    if code != compile_program(program, graph):
        raise ValueError("refusing execution: policy does not match StageProgram")
    handlers = load_handlers(code, graph)
    return graph, objects, handlers


def smoke(args):
    rt = OracleRuntime({"stages": []}, eval_url=args.eval_url, pipe_url=args.pipe_url,
                       arm_id=args.arm)
    print("health:", json.dumps(rt.eval.health(), ensure_ascii=False)[:300])
    print("reset:", json.dumps(rt.eval.reset(args.task_id), ensure_ascii=False)[:200])
    s = rt.eval.state()
    ents = s.get("entities", {})
    print(f"entities({len(ents)}):")
    for k, v in ents.items():
        print(f"  {k}: pos={[round(x,3) for x in v['pos']]}")
    print("probes:", [(p.get("label"), p.get("passed")) for p in s.get("probes", [])])
    xyz, quat = rt._cur_xquat()
    print(f"arm{args.arm} xquat: xyz={[round(v,3) for v in xyz]} quat={[round(v,3) for v in quat]}")
    return 0


def episode(args):
    run_dir = Path(args.run_dir).expanduser()
    program_dir = (Path(args.program_dir).expanduser()
                   if args.program_dir else run_dir)
    graph, objects, handlers = _load_artifacts(run_dir, program_dir)
    task = graph.get("task", run_dir.parent.name)
    rt = OracleRuntime(graph, objects, eval_url=args.eval_url, pipe_url=args.pipe_url,
                       arm_id=args.arm)
    print("reset:", rt.eval.reset(args.task_id).get("ok", "?"))
    probes_before = rt.probes()
    t0 = time.time()
    result = run_policy(handlers, graph, rt, max_attempts=args.max_attempts)
    try:
        program_ref = str(program_dir.relative_to(run_dir))
    except ValueError:
        program_ref = str(program_dir)
    report = {
        "banner": ORACLE_BANNER, "task": task, "task_id": args.task_id,
        "program_dir": program_ref,
        "result": result, "probes_before": probes_before, "probes_after": rt.probes(),
        "wall_sec": round(time.time() - t0, 1), "n_calls": len(rt.calls),
        "calls": rt.calls,
    }
    out = run_dir / f"episode_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"[{ORACLE_BANNER}] stages:",
          [(s.get("index"), s.get("status")) for s in result["stages"]])
    print("probes_after:", [(p.get("label"), p.get("passed")) for p in report["probes_after"]])
    print("report:", out)
    return 0 if result.get("ok") is True else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="dgl-oracle", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("smoke", "episode"):
        q = sub.add_parser(name)
        q.add_argument("--task-id", required=True)
        q.add_argument("--eval-url", default="http://127.0.0.1:7480")
        q.add_argument("--pipe-url", default="http://127.0.0.1:8000")
        q.add_argument("--arm", type=int, default=1)
        if name == "episode":
            q.add_argument("--run-dir", required=True)
            q.add_argument(
                "--program-dir", default=None,
                help="published compile product to execute; defaults to --run-dir, "
                     "e.g. <run-dir>/repairs/r1 for a repaired StageProgram",
            )
            q.add_argument("--max-attempts", type=int, default=2)
    args = p.parse_args(argv)
    return (smoke if args.cmd == "smoke" else episode)(args)


if __name__ == "__main__":
    raise SystemExit(main())
