"""M1 入口（仅在 1022 ``/mnt/data/wenqian/demo-graph-lab`` 体系内运行）。

默认只做只读 ``--mode probe``。``grasp`` / ``full`` 会发控制指令，须用户明确允许。
禁止写入或依赖 1024 ``/mnt/nas/knowin_sim/sim_workspace/``。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.knowin_world import PipelineClient
from experiments.insert_tubes.runtime import M1Runtime, build_policy, grasp_only_graph
from method.demo_graph import ConstraintGraph


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="非特权单试管 M1：probe / grasp / full（1022 本地仓）"
    )
    parser.add_argument("--mode", choices=("probe", "grasp", "full"), default="probe")
    parser.add_argument(
        "--pipeline-url",
        default="http://127.0.0.1:8000",
        help="1022 本机或本仓对接的 pipeline，勿指向 1024 基础仓服务",
    )
    parser.add_argument("--arm-id", type=int, default=0)
    parser.add_argument(
        "--pick-prompt", default="a test tube lying on the table:dof"
    )
    parser.add_argument(
        "--place-prompt", default="an empty circular hole in the test tube rack"
    )
    parser.add_argument("--lift-m", type=float, default=0.08)
    args = parser.parse_args(argv)

    graph = ConstraintGraph.load_json(Path(__file__).with_name("m1_graph.json"))
    runtime = M1Runtime(
        PipelineClient(args.pipeline_url),
        arm_id=args.arm_id,
        pick_prompt=args.pick_prompt,
        place_prompt=args.place_prompt,
        lift_m=args.lift_m,
    )
    if args.mode == "probe":
        result = runtime.probe()
        _json_print(result)
        return 0 if not result["perceptual_holes"] else 2
    if args.mode == "full":
        preflight = runtime.probe()
        if preflight["perceptual_holes"]:
            _json_print(
                {
                    "succeeded": False,
                    "reason": "full M1 preflight failed closed",
                    **preflight,
                }
            )
            return 2
    if args.mode == "grasp":
        graph = grasp_only_graph(graph)
    policy, broker = build_policy(runtime, graph)
    result = policy.run()
    _json_print(
        {
            "result": asdict(result),
            "method_calls": [asdict(record) for record in broker.audit_records],
        }
    )
    return 0 if result.succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
