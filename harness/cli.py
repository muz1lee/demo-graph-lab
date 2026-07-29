"""Demo 理解 harness CLI(Phase 0,无仿真)。设计见 RESEARCH_PROPOSAL_V2.md §5。

用法:
  python -m harness.cli ingest    --task insert_tubes [--video <mp4>] [--trace <json>]
  python -m harness.cli stages    --task insert_tubes
  python -m harness.cli keyframes --task insert_tubes [--per-stage 5]
  python -m harness.cli extract   --task insert_tubes [--k 5] [--max-stages N] [--model slug]
  python -m harness.cli validate  --task insert_tubes
  python -m harness.cli report    --task insert_tubes
  python -m harness.cli all       --task insert_tubes [--k 5] [--max-stages N]
  python -m harness.cli metrics   --task insert_tubes --gold harness/goldset/insert_tubes_gold.json
"""

import argparse
import sys

from . import util


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "stages", "keyframes", "extract", "validate",
                 "report", "metrics", "all", "compile"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        if name == "compile":
            p.add_argument("--model", default=None)
        if name in ("ingest", "all"):
            p.add_argument("--video")
            p.add_argument("--trace")
            p.add_argument("--n-frames", type=int, default=24)
        if name in ("keyframes", "all"):
            p.add_argument("--per-stage", type=int, default=5)
        if name in ("extract", "all", "stages"):
            p.add_argument("--model", default=None,
                           help="OpenRouter slug;默认读 .env 的 HARNESS_VLM_MODEL")
        if name in ("extract", "all"):
            p.add_argument("--k", type=int, default=5, help="自一致性采样数")
            p.add_argument("--max-stages", type=int, default=None)
        if name == "metrics":
            p.add_argument("--gold", required=True)
    args = parser.parse_args(argv)
    util.load_env()

    from . import extract, ingest, keyframes, metrics, report, stages, validate
    if args.cmd == "ingest":
        ingest.run(args.task, args.video, args.trace, args.n_frames)
    elif args.cmd == "stages":
        stages.run(args.task, args.model)
    elif args.cmd == "keyframes":
        keyframes.run(args.task, args.per_stage)
    elif args.cmd == "extract":
        extract.run(args.task, args.k, args.model, args.max_stages)
    elif args.cmd == "validate":
        validate.run(args.task)
    elif args.cmd == "report":
        report.run(args.task)
    elif args.cmd == "metrics":
        metrics.run(args.task, args.gold)
    elif args.cmd == "compile":
        from . import compilepolicy
        compilepolicy.run(args.task, args.model)
    elif args.cmd == "all":
        ingest.run(args.task, args.video, args.trace, args.n_frames)
        stages.run(args.task, args.model)
        keyframes.run(args.task, args.per_stage)
        extract.run(args.task, args.k, args.model, args.max_stages)
        validate.run(args.task)
        report.run(args.task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
