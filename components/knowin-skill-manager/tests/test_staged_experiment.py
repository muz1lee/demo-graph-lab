from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ksm.config import ArtifactConfig, LLMConfig, ManagerConfig, PipelineConfig
from ksm.io import read_json, write_yaml
from ksm.registry import build_registry
from ksm.skill_candidates import build_skill_candidate_artifacts
from ksm.staged_experiment import run_staged_experiment


def _config(root: Path) -> ManagerConfig:
    kw_repo = root / "knowin-world"
    k1_dir = kw_repo / "sim" / "sys" / "k1-sys-v0"
    pickplace = k1_dir / "knowin_skills" / "pickplace"
    pickplace.mkdir(parents=True)
    write_yaml(
        pickplace / "semantic_pick.yaml",
        {
            "schema_version": "1.0.0",
            "description": "fake semantic pick",
            "args": {"arm_id": 0, "pick_label": "bottle:dof"},
            "workflow": [{"assert": "= True", "message": "fake pick"}],
        },
    )
    write_yaml(
        pickplace / "pick_verifier.yaml",
        {
            "schema_version": "1.0.0",
            "description": "fake pick verifier",
            "args": {"arm_id": 0},
            "workflow": [{"assert": "= True", "message": "fake verify"}],
        },
    )
    return ManagerConfig(
        root_dir=root,
        kw_repo=kw_repo,
        k1_dir=k1_dir,
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        pipeline=PipelineConfig("direct", "http://127.0.0.1:8000", 0.1, 1.0),
        artifacts=ArtifactConfig(root / "candidates", root / "runs"),
        llm=LLMConfig(
            provider="openai",
            base_url="http://example.invalid/v1",
            base_url_env="",
            api_key_env="",
            model="test",
            model_env="",
            auth_mode="bearer",
            env_file=None,
            temperature=0.0,
            max_tokens=1000,
            timeout_s=1.0,
        ),
    )


def _selected_task() -> dict:
    return {
        "task_id": "robodojo_put_bottles_into_dustbin_029_bottle3_prop_to_dustbin_prop",
        "task_class": "put_bottles_into_dustbin",
        "prompt": "put bottle3_prop into dustbin_prop",
        "scene_path": "/scene/put_bottles_into_dustbin_029.yaml",
        "suite_path": "/tasks/put_bottles_into_dustbin_029.suite.yaml",
        "target_asset": {"id": "bottle3_prop", "category": "bottle"},
        "place_asset": {"id": "dustbin_prop", "category": "dustbin"},
        "binding": {
            "primary_pick_label": "蓝白瓶子:dof",
            "candidate_pick_labels": ["蓝白瓶子:dof", "bottle:dof"],
            "primary_place_label": "垃圾桶",
            "candidate_place_labels": ["垃圾桶", "dustbin"],
        },
    }


def _successful_suite_result(stage_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        to_dict=lambda: {
            "suite_id": f"suite_{stage_name}",
            "suite_path": "/tmp/suite.yaml",
            "run_dir": f"/tmp/{stage_name}",
            "execute": True,
            "publish": True,
            "success": True,
            "structural_ok": True,
            "success_rate": 1.0,
            "policy_ok_rate": 1.0,
            "pipeline_success_rate": 1.0,
            "predicate_success_rate": None,
            "episodes": [
                {
                    "episode_id": f"episode_{stage_name}",
                    "task_id": f"task_{stage_name}",
                    "candidate_id": f"candidate_{stage_name}",
                    "episode_dir": f"/tmp/{stage_name}",
                    "report": {
                        "success": True,
                        "pipeline_success": True,
                        "skill_success": True,
                        "predicate_success": None,
                        "failure_signature": "success",
                        "failure_analysis": {"category": "success", "stage": "completed"},
                        "metadata": {"execute": True},
                    },
                }
            ],
            "leaderboard": {},
            "evaluation_summary": {},
        }
    )


class StagedExperimentTests(unittest.TestCase):
    def test_runs_verifier_gated_pick_as_stage_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            registry = build_registry(config)
            candidates = build_skill_candidate_artifacts(selected_task=_selected_task(), registry=registry)
            calls: list[dict] = []

            def fake_runner(**kwargs):
                calls.append(kwargs)
                return _successful_suite_result(Path(str(kwargs["suite_path"])).parent.name)

            result = run_staged_experiment(
                config=config,
                skill_candidate_artifacts=candidates,
                output_root=root / "staged",
                execute=True,
                publish=True,
                capture_artifacts=False,
                registry=registry,
                suite_runner_fn=fake_runner,
            )
            payload = result.to_dict()
            self.assertTrue(payload["success"])
            self.assertEqual(payload["selected_stage_ids"], ["bind_selected_object_container", "pick_bottle"])
            self.assertEqual([item["stage_status"] for item in payload["stage_results"]], ["passed", "passed"])
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0]["reset_before_execute"])
            self.assertEqual(payload["final_state"]["state_id"], "after_pick_bottle")
            self.assertTrue(payload["final_state"]["predicates"]["holding"])

            pick = payload["stage_results"][1]
            self.assertEqual(pick["skill_reuse_decision"]["decision"], "reuse_existing_skill")
            self.assertFalse(pick["skill_reuse_decision"]["candidate_lifecycle"]["maintain_candidate"])
            self.assertTrue(Path(pick["stage_candidate"]["skill_yaml"]).exists())
            generated_pick = Path(pick["stage_candidate"]["skill_yaml"]).read_text(encoding="utf-8")
            self.assertIn("pickplace/semantic_pick.yaml", generated_pick)
            self.assertIn("Pick stage failed", generated_pick)
            package_manifest = read_json(Path(pick["stage_candidate"]["package"]["manifest_path"]))
            self.assertTrue(package_manifest["metadata"]["runtime_wrapper_only"])
            self.assertFalse(package_manifest["metadata"]["candidate_lifecycle"]["maintain_candidate"])
            feedback = read_json(Path(result.run_dir) / "stages" / "02_pick_bottle" / "aspire_feedback_packet.json")
            self.assertEqual(feedback["candidate_lifecycle"]["output_contract"], "do_not_create_new_skill_candidate")
            self.assertEqual(feedback["aspire_next_action"], "reuse_skill_confirmed")
            self.assertTrue(pick["output_state"]["predicates"]["holding"])
            self.assertTrue(Path(pick["prefix_replay"]["path"]).exists())
            self.assertTrue(pick["prefix_replay"]["executable_now"])

    def test_stops_after_failed_pick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            registry = build_registry(config)
            candidates = build_skill_candidate_artifacts(selected_task=_selected_task(), registry=registry)
            calls = 0

            def fake_runner(**kwargs):
                nonlocal calls
                calls += 1
                result = _successful_suite_result("pick").to_dict()
                result["success"] = False
                result["success_rate"] = 0.0
                result["pipeline_success_rate"] = 0.0
                report = result["episodes"][0]["report"]
                report["success"] = False
                report["pipeline_success"] = False
                report["skill_success"] = False
                report["failure_signature"] = "skill_execution_failed"
                report["failure_analysis"] = {"category": "skill_execution", "stage": "skill_runtime"}
                return SimpleNamespace(to_dict=lambda: result)

            result = run_staged_experiment(
                config=config,
                skill_candidate_artifacts=candidates,
                output_root=root / "staged",
                execute=True,
                publish=True,
                capture_artifacts=False,
                registry=registry,
                suite_runner_fn=fake_runner,
                stop_after_stage="move_above_dustbin",
            )
            payload = result.to_dict()
            self.assertFalse(payload["success"])
            self.assertEqual(calls, 1)
            self.assertEqual(payload["stage_results"][1]["stage_status"], "failed")
            pick_feedback = payload["stage_results"][1]["aspire_feedback_packet"]
            self.assertEqual(pick_feedback["aspire_next_action"], "iterate_reuse_binding_or_parameters")
            self.assertEqual(pick_feedback["candidate_lifecycle"]["output_contract"], "do_not_create_new_skill_candidate")
            self.assertEqual(payload["stage_results"][2]["stage_status"], "skipped")
            self.assertEqual(payload["stage_results"][2]["failure_signature"], "previous_stage_not_successful")


if __name__ == "__main__":
    unittest.main()
