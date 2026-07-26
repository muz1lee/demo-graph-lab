from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from adapters.runtime_doctor import (
    EndpointSpec,
    GitRepositorySpec,
    RuntimeDoctor,
    RuntimeDoctorConfig,
)


def _fake_fetch(url: str, timeout_s: float) -> dict[str, Any]:
    assert timeout_s == 1.0
    if url.endswith("/api/pipeline_status"):
        return {
            "ok": True,
            "cmd": "pipeline_status",
            "response": {"running": False, "results": [], "queued_skills": []},
        }
    if url.endswith(":8000/pipeline_status"):
        return {"running": False, "results": [], "queued_skills": []}
    if url.endswith(":7480/health"):
        return {
            "scene": "loaded",
            "scene_id": "opaque",
            "task_ids": ["insert_tubes_000"],
            "busy": False,
            "session": None,
        }
    raise AssertionError(url)


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_doctor_checks_three_services_and_clean_git(tmp_path: Path) -> None:
    repository = tmp_path / "runtime"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "README").write_text("runtime\n", encoding="utf-8")
    _git(repository, "add", "README")
    _git(repository, "commit", "-m", "initial")
    config = RuntimeDoctorConfig(
        endpoints=(
            EndpointSpec("webui", "http://127.0.0.1:5049", "/api/pipeline_status", "webui_pipeline_status.v1"),
            EndpointSpec("pipeline", "http://127.0.0.1:8000", "/pipeline_status", "pipeline_status.v1"),
            EndpointSpec("eval", "http://127.0.0.1:7480", "/health", "eval_health.v1"),
        ),
        repositories=(GitRepositorySpec("knowin-world", str(repository)),),
        timeout_s=1.0,
    )

    manifest = RuntimeDoctor(
        config,
        fetch_json=_fake_fetch,
        clock_ns=lambda: 123,
        monotonic=lambda: 1.0,
    ).run()

    assert manifest.golden_eligible is True
    assert [check.ok for check in manifest.endpoints] == [True, True, True]
    assert manifest.repositories[0].dirty is False
    assert manifest.manifest_digest.startswith("sha256:")
    assert "insert_tubes_000" not in json.dumps(manifest.to_dict())

    (repository / "dirty").write_text("x", encoding="utf-8")
    dirty = RuntimeDoctor(config, fetch_json=_fake_fetch).run()
    assert dirty.golden_eligible is False
    assert dirty.repositories[0].dirty is True
    assert dirty.repositories[0].dirty_entry_count == 1


def test_example_config_contains_only_local_placeholders() -> None:
    path = Path(__file__).parents[2] / "configs" / "examples" / "runtime.example.json"
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)

    assert "192.168." not in text
    assert "101.132." not in text
    assert "/mnt/" not in text
    assert "secret" not in text.lower()
    assert {item["base_url"] for item in payload["endpoints"]} == {
        "http://127.0.0.1:5049",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:7480",
    }
