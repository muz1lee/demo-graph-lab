"""Oracle execution CLI:

  dgl-oracle smoke   --task-id robodojo_insert_tubes_000
  dgl-oracle episode --run-dir runs/insert_tubes/<timestamp> \\
      --task-id robodojo_insert_tubes_000 [--arm 1]

smoke:health → reset → state 摘要 → get_xquat 回读(不动机器人)。
episode:加载编译产物，用 OracleRuntime 顺序执行并产出特权调试报告。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..policy.compiler import load_handlers
from .oracle_runtime import ORACLE_BANNER, OracleRuntime
from .runner import run_policy


def _load_artifacts(run_dir: Path):
    run_dir = run_dir.expanduser()
    graph = json.loads((run_dir / "graph.json").read_text())
    objects_path = run_dir / "objects.json"
    objects = json.loads(objects_path.read_text()) if objects_path.exists() else []
    code = (run_dir / "policy.py").read_text()
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


def episode(args):
    run_dir = Path(args.run_dir).expanduser()
    graph, objects, handlers = _load_artifacts(run_dir)
    task = graph.get("task", run_dir.parent.name)
    rt = OracleRuntime(graph, objects, eval_url=args.eval_url, pipe_url=args.pipe_url,
                       arm_id=args.arm)
    print("reset:", rt.eval.reset(args.task_id).get("ok", "?"))
    probes_before = rt.probes()
    t0 = time.time()
    result = run_policy(handlers, graph, rt, max_attempts=args.max_attempts)
    report = {
        "banner": ORACLE_BANNER, "task": task, "task_id": args.task_id,
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
            q.add_argument("--max-attempts", type=int, default=2)
    args = p.parse_args(argv)
    (smoke if args.cmd == "smoke" else episode)(args)


if __name__ == "__main__":
    main()
