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
from method.demo_graph import (
    ConstraintGraph,
    RestrictedCodeAgentCompiler,
    select_linear_action_cycle,
)
from method.demo_graph.metric_scan import freeze_policy


_M1_ACTION_SEQUENCE = ("pick", "reorient", "align", "insert", "verify")
_TRUSTED_CONTROLLERS = (
    "trusted.pick",
    "trusted.reorient",
    "trusted.align",
    "trusted.insert",
    "trusted.verify",
)
_MAX_PREFLIGHT_ATTEMPTS = 3
# Fail-closed perceptual gates only. GraspGen candidate count is intentionally
# NOT in this set: graspgen>0 is a record-only metric (fit-only chains may execute).
_GRASP_GATING_HOLES = frozenset({"grasp_pose", "tube_axis"})


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _run_preflight(
    runtime: M1Runtime,
    *,
    mode: str,
    max_attempts: int = _MAX_PREFLIGHT_ATTEMPTS,
) -> dict[str, Any]:
    if mode not in {"grasp", "full"}:
        raise ValueError(f"preflight is not defined for mode={mode!r}")
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    gating_holes: list[str] = []
    for attempt in range(1, max_attempts + 1):
        result = runtime.probe()
        perceptual_holes = list(result["perceptual_holes"])
        gating_holes = (
            [
                hole
                for hole in perceptual_holes
                if hole in _GRASP_GATING_HOLES
            ]
            if mode == "grasp"
            else perceptual_holes
        )
        attempts.append(
            {
                "attempt": attempt,
                "perceptual_holes": perceptual_holes,
                "gating_holes": list(gating_holes),
                "candidate_chain": result.get("candidate_chain"),
            }
        )
        if not gating_holes:
            break
    return {
        **result,
        "passed": not gating_holes,
        "gating_holes": gating_holes,
        "attempts": attempts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="非特权单试管 M1：probe / grasp / full（1022 本地仓）"
    )
    parser.add_argument("--mode", choices=("probe", "grasp", "full"), default="probe")
    parser.add_argument(
        "--graph",
        type=Path,
        default=Path(__file__).with_name("m1_graph.json"),
        help="ConstraintGraph JSON；M1.b 应显式指向 T1 提取图",
    )
    parser.add_argument(
        "--cycle-index",
        type=int,
        default=1,
        help="从多管提取图选择第几个单管五阶段周期（从 1 开始）",
    )
    parser.add_argument(
        "--compiled-policy-out",
        type=Path,
        help="可选：保存并通过 T3 冻结 gate 的生成 Python policy",
    )
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
    parser.add_argument(
        "--attachment-gate-m",
        type=float,
        default=0.04,
        help="测试提升的最小 z 增量；M1.a 验收要求不得低于 0.04 m",
    )
    args = parser.parse_args(argv)

    if args.cycle_index < 1:
        parser.error("--cycle-index must be >= 1")
    if args.attachment_gate_m < 0.04:
        parser.error("--attachment-gate-m must be >= 0.04 for M1.a")
    if args.lift_m < args.attachment_gate_m:
        parser.error("--lift-m must be >= --attachment-gate-m")
    input_graph = ConstraintGraph.load_json(args.graph)
    graph = select_linear_action_cycle(
        input_graph,
        _M1_ACTION_SEQUENCE,
        cycle_index=args.cycle_index - 1,
    )
    if args.mode == "grasp":
        graph = grasp_only_graph(graph)
    compiled = RestrictedCodeAgentCompiler(_TRUSTED_CONTROLLERS).compile(
        graph,
        input_graph_digest=input_graph.digest,
    )
    compiled_metadata = compiled.to_dict()
    compiled_metadata["input_graph_path"] = str(args.graph)
    if args.compiled_policy_out is not None:
        path = compiled.write(args.compiled_policy_out)
        frozen = freeze_policy(path)
        if frozen.code_digest != compiled.code_digest:
            raise RuntimeError("T3 freeze digest differs from Code Agent digest")
        compiled_metadata["path"] = str(path)
        compiled_metadata["metric_scan_findings"] = len(
            frozen.scan_report.findings
        )

    runtime = M1Runtime(
        PipelineClient(args.pipeline_url),
        arm_id=args.arm_id,
        pick_prompt=args.pick_prompt,
        place_prompt=args.place_prompt,
        lift_m=args.lift_m,
        lift_evidence_m=args.attachment_gate_m,
    )
    if args.mode == "probe":
        result = runtime.probe()
        _json_print({**result, "compiled_policy": compiled_metadata})
        return 0 if not result["perceptual_holes"] else 2
    preflight = _run_preflight(runtime, mode=args.mode)
    if not preflight["passed"]:
        _json_print(
            {
                "succeeded": False,
                "reason": f"{args.mode} M1 preflight failed closed",
                "compiled_policy": compiled_metadata,
                "preflight": preflight,
                "candidate_chain_policy": {
                    "graspgen_required_for_execution": False,
                    "note": "graspgen>0 is record-only; gating uses perceptual holes only",
                },
            }
        )
        return 2
    runtime.prime_execution_from_preflight(mode=args.mode)
    policy, broker = build_policy(runtime, graph, compiled=compiled)
    try:
        result = policy.run()
    except Exception as exc:
        _json_print(
            {
                "succeeded": False,
                "reason": "policy.run raised an exception",
                "exception": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                "preflight": preflight,
                "compiled_policy": compiled_metadata,
                "stage_evidence": runtime.stage_evidence(),
                "method_calls": [
                    asdict(record) for record in broker.audit_records
                ],
            }
        )
        raise
    _json_print(
        {
            "result": asdict(result),
            "preflight": preflight,
            "compiled_policy": compiled_metadata,
            "stage_evidence": runtime.stage_evidence(),
            "method_calls": [asdict(record) for record in broker.audit_records],
            "candidate_chain_policy": {
                "graspgen_required_for_execution": False,
                "note": "graspgen>0 is record-only; gating uses perceptual holes only",
            },
            "candidate_chain": preflight.get("candidate_chain"),
        }
    )
    return 0 if result.succeeded else 2


def cli(argv: list[str] | None = None) -> int:
    try:
        return main(argv)
    except Exception:
        return 3


if __name__ == "__main__":
    raise SystemExit(cli())
