"""Demo 理解 harness CLI(Phase 0,无仿真)。

状态: 脚手架(2026-07-29)。子命令与数据流已定型,实现按 RESEARCH_PROPOSAL_V2.md §5 落地;
未实现处显式 NotImplementedError,不做假功能。

用法(目标形态):
  python -m harness.cli ingest   --task insert_tubes --video <mp4> [--trace <dir>]
  python -m harness.cli extract  --task insert_tubes --model claude-opus-4-8 --k 5
  python -m harness.cli validate --task insert_tubes
  python -m harness.cli report   --task insert_tubes            # 产出 report.html(含金标标注)
  python -m harness.cli metrics  --task insert_tubes --gold harness/goldset/insert_tubes.json
"""

import argparse
import sys

SUBCOMMANDS = ("ingest", "extract", "validate", "report", "metrics")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in SUBCOMMANDS:
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        if name == "ingest":
            p.add_argument("--video")
            p.add_argument("--trace", help="robot-subtask-seg refined trace 目录(只读)")
        if name == "extract":
            p.add_argument("--model", default="anthropic/claude-opus-4.8",
                           help="OpenRouter slug; key/base_url/proxy 读 .env")
            p.add_argument("--k", type=int, default=5, help="自一致性采样数")
        if name == "metrics":
            p.add_argument("--gold", required=True)
    args = parser.parse_args(argv)
    raise NotImplementedError(
        f"harness.{args.cmd}: 脚手架阶段,实现进行中(见 RESEARCH_PROPOSAL_V2.md §8 TODO-1)"
    )


if __name__ == "__main__":
    sys.exit(main())
