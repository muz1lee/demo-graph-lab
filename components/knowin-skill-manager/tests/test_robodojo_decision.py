from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ksm.config import ArtifactConfig, LLMConfig, ManagerConfig, PipelineConfig, SkillLibraryConfig
from ksm.io import write_yaml
from ksm.llm import LLMResponse
from ksm.registry import SkillSummary, ToolRegistry
from ksm.robodojo_decision import (
    DecisionTaskSample,
    build_task_decision_prompt,
    normalize_decision_payload,
    run_robodojo_decision,
)


class _FakeClient:
    def complete(self, messages: list[dict[str, Any]]) -> LLMResponse:
        return LLMResponse(
            provider="fake",
            model="fake",
            text=json.dumps(
                {
                    "schema": "ksm.robodojo.task_skill_decision.v1",
                    "task_id": "robodojo_general_pickup_000",
                    "decision": "reuse_existing_skill",
                    "confidence": 0.91,
                    "selected_existing_skills": ["pickplace/semantic_pick.yaml"],
                    "proposed_candidate_name": None,
                    "missing_capabilities": [],
                    "rationale": "semantic pick covers lifting a target object",
                    "next_action": "run_reuse_baseline",
                }
            ),
        )


def _sample() -> DecisionTaskSample:
    return DecisionTaskSample(
        task_id="robodojo_general_pickup_000",
        task_class="general_pickup",
        prompt="Pick up the target object by 10 cm.",
        tags=["robodojo", "general_pickup", "pickup"],
        suite_path="/tmp/general_pickup_000.suite.yaml",
        scene_path="/tmp/general_pickup_000.yaml",
        success={"all_of": [{"type": "lift", "object": "target_prop"}]},
        subtasks=[],
        scene_summary={"available": True, "categories": {"can": 1}},
        reference_decision="reuse_existing_skill",
        reference_reason="covered by semantic pick",
    )


def _registry() -> ToolRegistry:
    return ToolRegistry(
        k1_dir="/tmp/k1",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        ctrl=["go_home"],
        info=[],
        reasoning=[],
        namespaces=["head"],
        skills=[
            SkillSummary(
                path="pickplace/semantic_pick.yaml",
                description="Pick a semantic object.",
                args={"arm_id": 0, "pick_label": ""},
                actions=["/head/reasoning/qwen_xquat", "/ctrl/xquat_move"],
            )
        ],
    )


def _config(root: Path) -> ManagerConfig:
    return ManagerConfig(
        root_dir=root,
        kw_repo=root / "kw",
        k1_dir=root / "k1",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        pipeline=PipelineConfig(mode="direct", base_url="http://127.0.0.1:8000", poll_interval_s=0.01, timeout_s=1.0),
        artifacts=ArtifactConfig(candidates_dir=root / "candidates", runs_dir=root / "runs"),
        llm=LLMConfig(
            provider="openai",
            base_url="http://fake/v1",
            base_url_env="",
            api_key_env="FAKE_API_KEY",
            model="fake-model",
            model_env="",
            auth_mode="bearer",
            env_file=None,
            temperature=0.0,
            max_tokens=1000,
            timeout_s=1.0,
        ),
        skill_library=SkillLibraryConfig(root=root / "lib"),
    )


class RobodojoDecisionTests(unittest.TestCase):
    def test_prompt_does_not_include_reference_answer(self) -> None:
        prompt = build_task_decision_prompt(sample=_sample(), registry=_registry())

        self.assertIn("reuse_existing_skill", prompt)
        self.assertNotIn("reference_decision", prompt)
        self.assertNotIn("covered by semantic pick", prompt)

    def test_normalize_invalid_decision_to_gap(self) -> None:
        normalized = normalize_decision_payload(
            {"decision": "invent_custom_controller", "confidence": 2.0},
            sample=_sample(),
            prompt_path=Path("/tmp/prompt.txt"),
            response=LLMResponse(provider="fake", model="fake", text="{}"),
            settings={},
        )

        self.assertEqual(normalized["decision"], "blocked_by_missing_low_level_primitive")
        self.assertEqual(normalized["confidence"], 1.0)
        self.assertFalse(normalized["reference"]["matches"])

    def test_run_decision_writes_report_with_fake_llm(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = _config(root)
            suite_dir = config.kw_repo / "tasks" / "robodojo" / "general_pickup"
            scene_dir = config.kw_repo / "scene_library" / "robodojo_rigid" / "general_pickup"
            suite_dir.mkdir(parents=True)
            scene_dir.mkdir(parents=True)
            write_yaml(
                suite_dir / "general_pickup_000.suite.yaml",
                {
                    "version": 2,
                    "scene": "scene_library/robodojo_rigid/general_pickup/general_pickup_000.yaml",
                    "tasks": [
                        {
                            "task_id": "robodojo_general_pickup_000",
                            "prompt": "Pick up the target object by 10 cm.",
                            "tags": ["robodojo", "general_pickup", "pickup"],
                            "success": {"all_of": [{"type": "lift", "object": "target_prop"}]},
                        }
                    ],
                },
            )
            write_yaml(
                scene_dir / "general_pickup_000.yaml",
                {
                    "metadata": {
                        "robodojo_asset_refs": [
                            {"id": "target_prop", "category": "can", "qualified": True, "has_collision_prims": True}
                        ]
                    },
                    "imports": [{"id": "target_prop"}],
                },
            )
            skill_path = config.k1_dir / "knowin_skills" / "pickplace" / "semantic_pick.yaml"
            write_yaml(
                skill_path,
                {
                    "description": "Pick a semantic object.",
                    "args": {"arm_id": 0, "pick_label": ""},
                    "workflow": [{"action": "/ctrl/xquat_move", "args": {"arm_id": "= args.arm_id"}}],
                },
            )

            result = run_robodojo_decision(
                config=config,
                task_classes=["general_pickup"],
                candidate_prefix="decision_test",
                client=_FakeClient(),
            )

            self.assertTrue(Path(result.report_path).exists())
            self.assertEqual(result.summary["reference_match_rate"], 1.0)
            self.assertEqual(result.decisions[0]["next_action"], "run_reuse_baseline")


if __name__ == "__main__":
    unittest.main()
