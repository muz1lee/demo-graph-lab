"""demo_bundle 适配器测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.demo_bundle import DemoBundleError, load_demo_bundle


def test_load_method_safe_bundle(tmp_path: Path):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "insert_tubes_000",
                "segments": [{"name": "pick", "actor_arm": 0}],
                "keyframes": [{"event": "grasp_close", "frame": 12}],
                "evidence_gaps": ["no_metric_depth"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bundle = load_demo_bundle(path)
    assert bundle.task_id == "insert_tubes_000"
    assert len(bundle.segments) == 1
    assert bundle.digest.startswith("sha256:")


def test_reject_privileged_marker(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({"task_id": "x", "note": "uses privileged_oracle pose"}),
        encoding="utf-8",
    )
    with pytest.raises(DemoBundleError):
        load_demo_bundle(path)
