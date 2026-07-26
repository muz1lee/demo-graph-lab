from __future__ import annotations

from pathlib import Path

import pytest

from method.demo_graph.metric_scan import (
    MetricLiteralViolation,
    assert_frozen_policy_unchanged,
    freeze_policy,
    scan_paths,
)


def test_python_scanner_finds_world_pose_literal(tmp_path: Path):
    policy = tmp_path / "policy.py"
    policy.write_text(
        "def run(api):\n"
        "    target_pose = [0.41, -0.12, 0.83, 0.0, 0.0, 0.0, 1.0]\n"
        "    return api.move(target_pose)\n",
        encoding="utf-8",
    )

    report = scan_paths((policy,))

    assert report.clean is False
    assert {item.literal for item in report.findings} >= {"0.41", "-0.12", "0.83"}


def test_yaml_scanner_finds_b7_style_slot_offsets(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "args:\n"
        "  slot_offsets:\n"
        "  - [0.0, -0.30, 0.0]\n"
        "  - [0.0, 0.00, 0.0]\n"
        "  - [0.0, 0.30, 0.0]\n",
        encoding="utf-8",
    )

    report = scan_paths((policy,))

    assert report.clean is False
    assert {item.literal for item in report.findings} == {"-0.30", "0.30"}


def test_clean_runtime_bound_policy_has_no_false_positive(tmp_path: Path):
    policy = tmp_path / "clean.py"
    policy.write_text(
        "def run(api):\n"
        "    observation = api.observe()\n"
        "    open_slots = api.detect_open_slots(observation)\n"
        "    return api.insert(open_slots[0])\n",
        encoding="utf-8",
    )

    report = scan_paths((policy,))

    assert report.clean is True
    assert report.findings == ()


def test_freeze_gate_records_digest_and_detects_change(tmp_path: Path):
    policy = tmp_path / "clean.py"
    policy.write_text("def run(api):\n    return api.observe()\n", encoding="utf-8")
    frozen = freeze_policy(policy)
    assert frozen.code_digest.startswith("sha256:")
    assert_frozen_policy_unchanged(frozen)

    policy.write_text("def run(api):\n    return api.observe_fresh()\n", encoding="utf-8")
    with pytest.raises(MetricLiteralViolation):
        assert_frozen_policy_unchanged(frozen)
