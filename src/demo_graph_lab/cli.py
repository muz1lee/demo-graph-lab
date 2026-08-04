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
  dgl planning-replay --graph <graph.json> --replay <replay.json> --output <comparison.json>
  dgl planning-record --record-dir <dir> [--step plan|capture|predict]
"""

import argparse
import sys
from pathlib import Path

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
    replay = sub.add_parser(
        "planning-replay",
        help="compare demo/no-demo selection on one frozen, read-only replay",
    )
    replay.add_argument("--graph", required=True)
    replay.add_argument("--replay", required=True)
    replay.add_argument("--output", required=True)
    record = sub.add_parser(
        "planning-record",
        help="freeze a read-only head observation and raw GraspNet reply",
    )
    record.add_argument("--record-dir", required=True)
    record.add_argument(
        "--step",
        choices=("plan", "capture", "predict"),
        default="plan",
    )
    record.add_argument("--graph")
    record.add_argument("--stage", type=int, default=0)
    record.add_argument("--intrinsics")
    record.add_argument("--pipeline-url", default="http://127.0.0.1:8000")
    record.add_argument("--graspnet-url", default="http://127.0.0.1:8092")
    record.add_argument("--camera-socket", default="/tmp/knowin_sim_camera.sock")
    record.add_argument("--timeout", type=float, default=10.0)
    record.add_argument("--max-grasps", type=int, default=20)
    record.add_argument("--allow-live-read", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "planning-replay":
        from .execution.planning_replay import run_replay

        result = run_replay(args.graph, args.replay)
        artifacts.write_json(Path(args.output), result)
        return 0

    if args.cmd == "planning-record":
        from .execution.planning_record import (
            capture_record,
            plan_record,
            predict_record,
        )

        if args.step == "plan":
            if not args.graph or not args.intrinsics:
                parser.error(
                    "planning-record --step plan requires --graph and --intrinsics"
                )
            plan_record(
                graph_path=args.graph,
                stage_index=args.stage,
                record_dir=args.record_dir,
                intrinsics_path=args.intrinsics,
                pipeline_url=args.pipeline_url,
                graspnet_url=args.graspnet_url,
                camera_socket=args.camera_socket,
                timeout_s=args.timeout,
                max_grasps=args.max_grasps,
            )
        elif args.step == "capture":
            capture_record(
                args.record_dir,
                allow_live_read=args.allow_live_read,
            )
        else:
            predict_record(
                args.record_dir,
                allow_live_read=args.allow_live_read,
            )
        return 0

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
