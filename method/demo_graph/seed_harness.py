"""Frozen-policy D/E seed protocol harness with five-stage funnel reports."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .manifest import RunManifest
from .metric_scan import (
    FrozenPolicy,
    assert_frozen_policy_unchanged,
    freeze_policy,
)
from .runner import NodeStatus, PolicyRunResult


_FUNNEL_STAGES = ("grasp", "lift", "reorient", "align", "insert")


@dataclass(frozen=True, slots=True)
class SeedProtocol:
    protocol_version: str
    development_seeds: tuple[int, ...]
    held_out_seeds: tuple[int, ...]
    initial_held_out_count: int

    def __post_init__(self) -> None:
        if not self.protocol_version.strip():
            raise ValueError("protocol_version must not be empty")
        if len(self.development_seeds) != 3:
            raise ValueError("development split must contain exactly 3 seeds")
        if len(self.held_out_seeds) != 100:
            raise ValueError("held-out split must contain exactly 100 seeds")
        if self.initial_held_out_count != 20:
            raise ValueError("initial held-out evaluation must contain exactly 20 seeds")
        if len(set(self.development_seeds)) != len(self.development_seeds):
            raise ValueError("development seeds must be unique")
        if len(set(self.held_out_seeds)) != len(self.held_out_seeds):
            raise ValueError("held-out seeds must be unique")
        overlap = set(self.development_seeds) & set(self.held_out_seeds)
        if overlap:
            raise ValueError(f"development and held-out seeds overlap: {sorted(overlap)}")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SeedProtocol":
        return cls(
            protocol_version=str(raw.get("protocol_version") or ""),
            development_seeds=_resolve_seed_spec(raw["development"]),
            held_out_seeds=_resolve_seed_spec(raw["held_out"]),
            initial_held_out_count=int(
                (raw.get("held_out") or {}).get("initial_count", 20)
                if isinstance(raw.get("held_out"), Mapping)
                else 20
            ),
        )

    @classmethod
    def load_json(cls, path: str | Path) -> "SeedProtocol":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise TypeError("seed protocol root must be an object")
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "development_seeds": list(self.development_seeds),
            "held_out_seeds": list(self.held_out_seeds),
            "initial_held_out_count": self.initial_held_out_count,
        }


def _resolve_seed_spec(value: Any) -> tuple[int, ...]:
    if isinstance(value, list):
        return tuple(int(item) for item in value)
    if isinstance(value, Mapping):
        start = int(value["start"])
        count = int(value["count"])
        if count < 1:
            raise ValueError("seed range count must be positive")
        return tuple(range(start, start + count))
    raise TypeError("seed split must be a list or {start, count} object")


def _load_backend(spec: str) -> Callable[..., PolicyRunResult]:
    module_name, separator, function_name = spec.partition(":")
    if not separator:
        raise ValueError("backend must be formatted as module:function")
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"backend is not callable: {spec}")
    return function


def _call_backend(
    backend: Callable[..., PolicyRunResult],
    seed: int,
) -> PolicyRunResult:
    parameters = inspect.signature(backend).parameters
    result = backend() if not parameters else backend(seed)
    if not isinstance(result, PolicyRunResult):
        raise TypeError("seed backend must return PolicyRunResult")
    return result


def _node_passed(result: PolicyRunResult, action: str) -> bool:
    nodes = [node for node in result.nodes if node.action == action]
    return bool(nodes) and all(
        node.status in {NodeStatus.SUCCEEDED, NodeStatus.SKIPPED}
        for node in nodes
    )


def _funnel(result: PolicyRunResult) -> dict[str, Any]:
    grasp = _node_passed(result, "pick")
    stages = {
        "grasp": grasp,
        "lift": grasp,
        "reorient": grasp and _node_passed(result, "reorient"),
        "align": grasp and _node_passed(result, "align"),
        "insert": grasp and _node_passed(result, "insert") and result.succeeded,
    }
    first_failed = next((name for name in _FUNNEL_STAGES if not stages[name]), None)
    return {"stages": stages, "first_failed_stage": first_failed}


def _result_payload(
    *,
    seed: int,
    split: str,
    result: PolicyRunResult,
) -> dict[str, Any]:
    return {
        "seed": seed,
        "split": split,
        "executed": True,
        "policy_succeeded": result.succeeded,
        "reason": result.reason,
        "graph_digest": result.graph_digest,
        "funnel": _funnel(result),
        "nodes": [
            {
                "node_id": node.node_id,
                "action": node.action,
                "status": node.status.value,
                "attempts": node.attempts,
                "failure_constraint_id": node.failure_constraint_id,
                "reason": node.reason,
            }
            for node in result.nodes
        ],
    }


def _split_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    stage_counts = {
        stage: sum(bool(record["funnel"]["stages"][stage]) for record in records)
        for stage in _FUNNEL_STAGES
    }
    first_failures = Counter(
        str(record["funnel"]["first_failed_stage"])
        for record in records
        if record["funnel"]["first_failed_stage"] is not None
    )
    return {
        "run_count": total,
        "executed_count": sum(bool(record["executed"]) for record in records),
        "policy_succeeded_count": sum(
            bool(record["policy_succeeded"]) for record in records
        ),
        "stage_pass_count": stage_counts,
        "stage_pass_rate": {
            stage: (stage_counts[stage] / total if total else 0.0)
            for stage in _FUNNEL_STAGES
        },
        "first_failure_count": dict(sorted(first_failures.items())),
    }


def _manifest(
    *,
    seed: int,
    split: str,
    frozen: FrozenPolicy,
    graph_digest: str,
    backend_name: str,
) -> RunManifest:
    return RunManifest.from_parts(
        ksm_commit="fake:not_applicable",
        knowin_world_commit="fake:not_applicable",
        knowin_world_dirty_hash=None,
        data_asset_lock="fake:no_external_data",
        config={
            "protocol": "D/E",
            "split": split,
            "backend": backend_name,
        },
        model_ids=("fake-backend",),
        seed=seed,
        graph_digest=graph_digest,
        code_digest=frozen.code_digest,
        api_audit_digests=(),
        golden=False,
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_seed_protocol(
    *,
    protocol: SeedProtocol,
    policy_path: str | Path,
    backend: Callable[..., PolicyRunResult],
    backend_name: str,
    output_dir: str | Path,
    held_out_count: int = 20,
) -> dict[str, Any]:
    """Freeze code, run D/E seeds, and persist manifests plus funnel evidence."""

    if held_out_count not in {
        protocol.initial_held_out_count,
        len(protocol.held_out_seeds),
    }:
        raise ValueError("held_out_count must be the configured initial 20 or full 100")
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty run directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    frozen = freeze_policy(policy_path)
    _write_json(output / "metric_scan.json", frozen.scan_report.to_dict())
    _write_json(
        output / "protocol_snapshot.json",
        {
            **protocol.to_dict(),
            "selected_held_out_count": held_out_count,
            "code_digest": frozen.code_digest,
            "backend": backend_name,
        },
    )

    records_by_split: dict[str, list[dict[str, Any]]] = {
        "development": [],
        "held_out": [],
    }
    selected = {
        "development": protocol.development_seeds,
        "held_out": protocol.held_out_seeds[:held_out_count],
    }
    for split, seeds in selected.items():
        for seed in seeds:
            assert_frozen_policy_unchanged(frozen)
            result = _call_backend(backend, seed)
            assert_frozen_policy_unchanged(frozen)
            record = _result_payload(seed=seed, split=split, result=result)
            manifest = _manifest(
                seed=seed,
                split=split,
                frozen=frozen,
                graph_digest=result.graph_digest,
                backend_name=backend_name,
            )
            seed_dir = output / split / f"seed_{seed}"
            _write_json(seed_dir / "result.json", record)
            _write_json(seed_dir / "run_manifest.json", manifest.to_dict())
            records_by_split[split].append(record)

    summary = {
        "schema": "demo_graph.seed_protocol_report.v1",
        "backend_kind": "fake",
        "effect_claims_allowed": False,
        "code_digest": frozen.code_digest,
        "development": _split_summary(records_by_split["development"]),
        "held_out": _split_summary(records_by_split["held_out"]),
    }
    _write_json(output / "funnel_report.json", summary)
    report = (
        "# D/E seed 协议 fake backend 干跑报告\n\n"
        "本报告只证明冻结、seed 隔离、批量调度、RunManifest 与五阶段漏斗链路可用；"
        "fake backend 结果不构成机器人任务效果。\n\n"
        f"- 冻结代码摘要：`{frozen.code_digest}`\n"
        f"- development：{summary['development']['executed_count']}/"
        f"{summary['development']['run_count']} 已执行，"
        f"{summary['development']['policy_succeeded_count']} 个 fake policy 成功\n"
        f"- held-out：{summary['held_out']['executed_count']}/"
        f"{summary['held_out']['run_count']} 已执行，"
        f"{summary['held_out']['policy_succeeded_count']} 个 fake policy 成功\n"
        "- 效果性声明：不允许（`effect_claims_allowed=false`）\n"
    )
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen D/E seed protocol on an allowlisted backend."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument(
        "--backend",
        default="method.demo_graph.examples.m1_fake:run_example",
    )
    parser.add_argument("--held-out-count", type=int, default=20)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    run_seed_protocol(
        protocol=SeedProtocol.load_json(args.config),
        policy_path=args.policy,
        backend=_load_backend(args.backend),
        backend_name=args.backend,
        output_dir=args.output,
        held_out_count=args.held_out_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
