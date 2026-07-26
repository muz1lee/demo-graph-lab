from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ksm.config import ArtifactConfig, LLMConfig, ManagerConfig, PipelineConfig, SkillLibraryConfig
from ksm.predicate import (
    evaluate_predicate,
    kw_predicate_capabilities,
    predicate_to_dict,
    predicates_from_task_payload,
    source_center_inside_container_aabb,
)


def _config(root: Path) -> ManagerConfig:
    return ManagerConfig(
        root_dir=root,
        kw_repo=root / "missing_kw_repo",
        k1_dir=root / "k1",
        test_skill_dir="knowin_skills/tests",
        pipeline=PipelineConfig(mode="direct", base_url="http://127.0.0.1:8000", poll_interval_s=0.1, timeout_s=1.0),
        artifacts=ArtifactConfig(candidates_dir=root / "candidates", runs_dir=root / "runs"),
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
        skill_library=SkillLibraryConfig(root=root / "library", top_k=1, snippet_chars=100, max_chars=100),
    )


class PredicateTests(unittest.TestCase):
    def test_extracts_robodojo_success_predicates(self) -> None:
        payload = {
            "robodojo": {
                "success": [
                    {"type": "inside", "object": "bottle3_prop", "container": "dustbin_prop"},
                    {"type": "robot_home", "object": "robot"},
                ]
            }
        }
        predicates = predicates_from_task_payload(payload)
        self.assertEqual(predicates[0]["type"], "inside")
        self.assertEqual(predicates[0]["object"], "bottle3_prop")

    def test_preserves_kw_predicate_strings(self) -> None:
        payload = {"success": {"all_of": ["inside(bottle3_prop, dustbin_prop)", "robot_home(robot)"]}}
        predicates = predicates_from_task_payload(payload)

        self.assertEqual(predicates[0], "inside(bottle3_prop, dustbin_prop)")
        self.assertEqual(predicate_to_dict(predicates[0])["container"], "dustbin_prop")

    def test_fallback_registry_exposes_kw_predicate_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            caps = kw_predicate_capabilities(_config(Path(tmp)))

        self.assertFalse(caps["available"])
        self.assertIn("inside", caps["names"])
        self.assertIn("stacked", caps["names"])
        self.assertIn("inserted", caps["names"])

    def test_aabb_inside_fallback_detects_inside_and_outside(self) -> None:
        container = {"aabb": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}}
        inside = {"aabb": {"min": [0.4, 0.4, 0.2], "max": [0.5, 0.5, 0.3]}}
        outside = {"aabb": {"min": [1.2, 0.4, 0.2], "max": [1.3, 0.5, 0.3]}}
        self.assertTrue(source_center_inside_container_aabb(source=inside, container=container)["success"])
        self.assertFalse(source_center_inside_container_aabb(source=outside, container=container)["success"])

    def test_evaluate_inside_uses_fallback_when_kw_native_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = {
                "bottle3_prop": {"id": "bottle3_prop", "aabb": {"min": [1.2, 0.4, 0.2], "max": [1.3, 0.5, 0.3]}},
                "dustbin_prop": {"id": "dustbin_prop", "aabb": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}},
            }
            result = evaluate_predicate(
                {"type": "inside", "object": "bottle3_prop", "container": "dustbin_prop"},
                assets=assets,
                config=_config(root),
            )
            self.assertTrue(result["supported"])
            self.assertFalse(result["success"])
            self.assertFalse(result["native"]["available"])

    def test_registered_non_fallback_predicate_is_not_faked_when_native_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = {
                "cube_a": {"id": "cube_a", "aabb": {"min": [0.0, 0.0, 0.0], "max": [0.1, 0.1, 0.1]}},
                "cube_b": {"id": "cube_b", "aabb": {"min": [0.0, 0.0, 0.1], "max": [0.1, 0.1, 0.2]}},
            }
            result = evaluate_predicate("stacked(cube_b, cube_a)", assets=assets, config=_config(root))

        self.assertFalse(result["supported"])
        self.assertTrue(result["kw_registered"])
        self.assertIn("cannot evaluate", result["reason"])


if __name__ == "__main__":
    unittest.main()
