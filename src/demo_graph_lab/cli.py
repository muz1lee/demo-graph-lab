"""Demo understanding, graph extraction, and policy compilation CLI.

用法:
  dgl ingest    --task insert_tubes [--video <mp4>] [--trace <json>]
  dgl stages    --task insert_tubes
  dgl keyframes --task insert_tubes [--per-stage 5]
  dgl extract   --task insert_tubes [--k 5] [--max-stages N] [--model slug]
  dgl validate  --task insert_tubes
  dgl report    --task insert_tubes
  dgl all       --task insert_tubes [--k 5] [--max-stages N]
  dgl metrics   --task insert_tubes --gold benchmarks/goldsets/insert_tubes_gold.json
"""

import argparse
import sys

from .common import artifacts


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dgl", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "stages", "keyframes", "objects", "extract", "enrich",
                 "validate", "report", "metrics", "all", "compile"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        if name in ("compile", "objects"):
            p.add_argument("--model", default=None)
        if name in ("ingest", "all"):
            p.add_argument("--video")
            p.add_argument("--trace")
            p.add_argument("--n-frames", type=int, default=24)
        if name in ("keyframes", "all"):
            p.add_argument("--per-stage", type=int, default=5)
        if name in ("extract", "all", "stages"):
            p.add_argument("--model", default=None,
                           help="OpenRouter slug;默认读 .env 的 DGL_VLM_MODEL")
        if name in ("extract", "all"):
            p.add_argument("--k", type=int, default=5, help="自一致性采样数")
            p.add_argument("--max-stages", type=int, default=None)
        if name == "metrics":
            p.add_argument("--gold", required=True)
    args = parser.parse_args(argv)
    artifacts.load_env()

    from .demo import ingest, keyframes, registry, stages
    from .graph import enrich, extract, metrics, report, validate
    if args.cmd == "objects":
        registry.run(args.task, args.model)
    elif args.cmd == "enrich":
        enrich.run(args.task)
    elif args.cmd == "ingest":
        ingest.run(args.task, args.video, args.trace, args.n_frames)
    elif args.cmd == "stages":
        stages.run(args.task, args.model)
    elif args.cmd == "keyframes":
        keyframes.run(args.task, args.per_stage)
    elif args.cmd == "extract":
        extract.run(args.task, args.k, args.model, args.max_stages)
    elif args.cmd == "validate":
        return 0 if validate.run(args.task)["passed"] else 1
    elif args.cmd == "report":
        report.run(args.task)
    elif args.cmd == "metrics":
        metrics.run(args.task, args.gold)
    elif args.cmd == "compile":
        from .policy import compiler
        report_path = compiler.run(args.task, args.model)
        compile_report = artifacts.read_json(report_path)
        return 0 if (compiler.report_ready(compile_report)
                     and (report_path.parent / "policy.py").exists()) else 1
    elif args.cmd == "all":
        ingest.run(args.task, args.video, args.trace, args.n_frames)
        stages.run(args.task, args.model)
        keyframes.run(args.task, args.per_stage)
        registry.run(args.task, args.model)
        extract.run(args.task, args.k, args.model, args.max_stages)
        enrich.run(args.task)
        validation = validate.run(args.task)
        report.run(args.task)
        return 0 if validation["passed"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
