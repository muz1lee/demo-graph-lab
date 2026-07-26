from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksm.config import ArtifactConfig, LLMConfig, ManagerConfig, PipelineConfig, SkillLibraryConfig
from ksm.runner import run_published_skill


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.calls: list[tuple[str, object]] = []
        _FakeClient.instances.append(self)

    def reset_layout(self, *, target: str, timeout_s: float) -> dict:
        self.calls.append(("reset_layout", {"target": target, "timeout_s": timeout_s}))
        return {"ok": True, "target": target}

    def reset_pipeline(self) -> str:
        self.calls.append(("reset_pipeline", None))
        return "Pipline reset"

    def run_control(self, name: str, kwargs: dict) -> dict:
        self.calls.append(("run_control", {"name": name, "kwargs": dict(kwargs)}))
        return {"ok": True, "result": "True"}

    def run_skill(self, skill_path: str, kwargs: dict | None = None) -> dict:
        self.calls.append(("run_skill", {"skill_path": skill_path, "kwargs": dict(kwargs or {})}))
        return {"ok": True}

    def wait_done(self, *, poll_interval_s: float, timeout_s: float) -> dict:
        self.calls.append(("wait_done", {"poll_interval_s": poll_interval_s, "timeout_s": timeout_s}))
        return {"running": False, "success": [{"success": True}]}


def _config() -> ManagerConfig:
    return ManagerConfig(
        root_dir=Path("/tmp/ksm"),
        kw_repo=Path("/tmp/kw"),
        k1_dir=Path("/tmp/k1"),
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        pipeline=PipelineConfig(mode="direct", base_url="http://127.0.0.1:8000", poll_interval_s=0.01, timeout_s=1.0),
        artifacts=ArtifactConfig(candidates_dir=Path("/tmp/candidates"), runs_dir=Path("/tmp/runs")),
        llm=LLMConfig(
            provider="openai",
            base_url="",
            base_url_env="",
            api_key_env="",
            model="",
            model_env="",
            auth_mode="bearer",
            env_file=None,
            temperature=1.0,
            max_tokens=1,
            timeout_s=1.0,
        ),
        skill_library=SkillLibraryConfig(root=Path("/tmp/lib")),
    )


class RunnerResetTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeClient.instances.clear()

    def test_reset_before_run_resets_and_homes_before_skill_submit(self) -> None:
        with patch("ksm.runner.PipelineDirectClient", _FakeClient):
            result = run_published_skill(
                config=_config(),
                skill_path="knowin_skills/example.yaml",
                kwargs={"pick_label": "yellow bottle:dof"},
                reset_before_run=True,
            )

        calls = _FakeClient.instances[0].calls
        self.assertEqual(
            [call[0] for call in calls[:5]],
            ["reset_layout", "reset_pipeline", "run_control", "run_control", "run_skill"],
        )
        self.assertEqual(calls[0][1], {"target": "manifest", "timeout_s": 8.0})
        self.assertEqual(calls[2][1], {"name": "go_home", "kwargs": {"arm_id": 0}})
        self.assertEqual(calls[3][1], {"name": "go_home", "kwargs": {"arm_id": 1}})
        self.assertEqual(result.pre_run["layout_reset"], {"ok": True, "target": "manifest"})  # type: ignore[index]
        self.assertEqual(result.pre_run["pipeline_reset"], "Pipline reset")  # type: ignore[index]

    def test_can_skip_reset_before_run(self) -> None:
        with patch("ksm.runner.PipelineDirectClient", _FakeClient):
            result = run_published_skill(
                config=_config(),
                skill_path="knowin_skills/example.yaml",
                kwargs={},
                reset_before_run=False,
            )

        calls = _FakeClient.instances[0].calls
        self.assertEqual([call[0] for call in calls[:2]], ["run_skill", "wait_done"])
        self.assertEqual(result.pre_run, {"reset_before_run": False})


if __name__ == "__main__":
    unittest.main()
