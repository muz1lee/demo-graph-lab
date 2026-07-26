from __future__ import annotations

import json
from pathlib import Path

import pytest

from method.demo_graph.examples.m1_fake import run_example
from method.demo_graph.seed_harness import SeedProtocol, run_seed_protocol


def _protocol() -> SeedProtocol:
    return SeedProtocol(
        protocol_version="test",
        development_seeds=(1, 2, 3),
        held_out_seeds=tuple(range(100, 200)),
        initial_held_out_count=20,
    )


def test_fake_backend_dry_run_freezes_code_and_writes_23_manifests(tmp_path: Path):
    policy = tmp_path / "policy.py"
    policy.write_text(
        "def run(api):\n"
        "    observation = api.observe()\n"
        "    return api.insert(api.detect_open_slots(observation)[0])\n",
        encoding="utf-8",
    )
    output = tmp_path / "run"

    summary = run_seed_protocol(
        protocol=_protocol(),
        policy_path=policy,
        backend=run_example,
        backend_name="method.demo_graph.examples.m1_fake:run_example",
        output_dir=output,
        held_out_count=20,
    )

    assert summary["effect_claims_allowed"] is False
    assert summary["development"]["executed_count"] == 3
    assert summary["held_out"]["executed_count"] == 20
    assert summary["held_out"]["stage_pass_count"]["insert"] == 20
    manifests = sorted(output.glob("*/seed_*/run_manifest.json"))
    assert len(manifests) == 23
    digests = {
        json.loads(path.read_text(encoding="utf-8"))["code_digest"]
        for path in manifests
    }
    assert digests == {summary["code_digest"]}


def test_seed_protocol_rejects_development_held_out_overlap():
    with pytest.raises(ValueError, match="overlap"):
        SeedProtocol(
            protocol_version="test",
            development_seeds=(1, 2, 3),
            held_out_seeds=tuple(range(3, 103)),
            initial_held_out_count=20,
        )
