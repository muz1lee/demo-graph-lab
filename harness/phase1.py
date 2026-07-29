"""Phase 1 CLI(在 5090 上运行,repo 根目录):

  python -m harness.phase1 smoke   --task-id robodojo_insert_tubes_000
  python -m harness.phase1 episode --task insert_tubes --task-id robodojo_insert_tubes_000 \\
      [--artifacts ~/phase1/artifacts] [--arm 1]

smoke:health → reset → state 摘要 → get_xquat 回读(不动机器人)。
episode:加载编译产物,KWRuntime(ORACLE-M1A) + 可信 runner 跑全链,产出 episode_report.json。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .fakerun import run_policy
from .kwadapter import ORACLE_BANNER, KWRuntime


def _load_artifacts(root: Path, task: str):
    d = root.expanduser() / task
    graph = json.loads((d / "graph.json").read_text())
    objects = json.loads((d / "objects.json").read_text()) if (d / "objects.json").exists() else []
    code = (d / "policy.py").read_text()
    ns: dict = {"__builtins__": {}}
    exec(code, ns)  # 已过编译期 AST 静态检查的受限模块
    return graph, objects, ns["STAGES"]


def smoke(args):
    rt = KWRuntime({"stages": []}, eval_url=args.eval_url, pipe_url=args.pipe_url,
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
    graph, objects, handlers = _load_artifacts(Path(args.artifacts), args.task)
    rt = KWRuntime(graph, objects, eval_url=args.eval_url, pipe_url=args.pipe_url,
                   arm_id=args.arm)
    print("reset:", rt.eval.reset(args.task_id).get("ok", "?"))
    probes_before = rt.probes()
    t0 = time.time()
    result = run_policy(handlers, graph, rt, max_attempts=args.max_attempts)
    report = {
        "banner": ORACLE_BANNER, "task": args.task, "task_id": args.task_id,
        "result": result, "probes_before": probes_before, "probes_after": rt.probes(),
        "wall_sec": round(time.time() - t0, 1), "n_calls": len(rt.calls),
        "calls": rt.calls,
    }
    out = Path(args.artifacts).expanduser() / args.task / f"episode_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"[{ORACLE_BANNER}] stages:",
          [(s.get("index"), s.get("status")) for s in result["stages"]])
    print("probes_after:", [(p.get("label"), p.get("passed")) for p in report["probes_after"]])
    print("report:", out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="phase1", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("smoke", "episode"):
        q = sub.add_parser(name)
        q.add_argument("--task-id", required=True)
        q.add_argument("--eval-url", default="http://127.0.0.1:7480")
        q.add_argument("--pipe-url", default="http://127.0.0.1:8000")
        q.add_argument("--arm", type=int, default=1)
        if name == "episode":
            q.add_argument("--task", required=True)
            q.add_argument("--artifacts", default="~/phase1/artifacts")
            q.add_argument("--max-attempts", type=int, default=2)
    args = p.parse_args(argv)
    (smoke if args.cmd == "smoke" else episode)(args)


if __name__ == "__main__":
    main()
