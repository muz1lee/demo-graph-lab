from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ksm.skill_candidates import build_skill_candidate_artifacts, write_skill_candidate_artifacts


class _Registry(SimpleNamespace):
    @property
    def skill_paths(self) -> set[str]:
        return set(self.skills)


def _registry() -> _Registry:
    return _Registry(
        skills={
            "pickplace/semantic_pick.yaml",
            "pickplace/semantic_pickplace.yaml",
            "pickplace/pick_verifier.yaml",
            "pickplace/semantic_place.yaml",
        },
        ctrl=["go_home"],
        info=["is_gripping_sth"],
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
            "candidate_pick_labels": ["蓝白瓶子:dof", "瓶子:dof"],
            "primary_place_label": "垃圾桶",
            "candidate_place_labels": ["垃圾桶", "dustbin"],
        },
    }


class SkillCandidateTests(unittest.TestCase):
    def test_builds_staged_plan_candidates_and_gap_report(self) -> None:
        artifacts = build_skill_candidate_artifacts(
            selected_task=_selected_task(),
            registry=_registry(),
            generated_manifest={
                "candidate_id": "tier4_wrapper",
                "policy": {"actions": ["pickplace/semantic_pickplace.yaml", "/ctrl/go_home"]},
            },
        )

        self.assertEqual(artifacts["task_family"], "put_single_object_into_container")
        self.assertEqual(len(artifacts["staged_plan"]), 6)
        self.assertEqual(len(artifacts["stage_skill_decisions"]), 6)
        self.assertEqual(len(artifacts["subskill_candidates"]), 2)
        self.assertEqual(
            [stage["stage_id"] for stage in artifacts["staged_plan"]],
            [
                "bind_selected_object_container",
                "pick_bottle",
                "move_above_dustbin",
                "release_into_dustbin",
                "verify_inside",
                "go_home",
            ],
        )
        self.assertEqual(artifacts["final_workflow_candidate"]["candidate_type"], "family_skill_candidate")
        self.assertEqual(
            artifacts["final_workflow_candidate"]["current_executable_yaml"]["classification"],
            "workflow_candidate",
        )

        statuses = {
            record["stage_id"]: record["coverage"]["status"]
            for record in artifacts["staged_plan"]
        }
        self.assertEqual(statuses["bind_selected_object_container"], "covered_by_ksm_metadata")
        self.assertEqual(statuses["pick_bottle"], "covered_by_verifier_gated_skill")
        self.assertEqual(statuses["move_above_dustbin"], "missing_native_skill")
        self.assertEqual(statuses["release_into_dustbin"], "partially_covered_no_container_semantics")
        self.assertEqual(statuses["verify_inside"], "covered_by_predicate")

        decisions = {
            record["stage_id"]: record["skill_reuse_decision"]
            for record in artifacts["staged_plan"]
        }
        self.assertEqual(decisions["pick_bottle"]["decision"], "reuse_existing_skill")
        self.assertFalse(decisions["pick_bottle"]["candidate_lifecycle"]["maintain_candidate"])
        self.assertEqual(decisions["pick_bottle"]["selected_existing_skills"], ["pickplace/semantic_pick.yaml"])
        self.assertEqual(decisions["move_above_dustbin"]["decision"], "new_subskill_needed")
        self.assertTrue(decisions["move_above_dustbin"]["candidate_lifecycle"]["maintain_candidate"])
        self.assertEqual(
            {candidate["stage_id"] for candidate in artifacts["subskill_candidates"]},
            {"move_above_dustbin", "release_into_dustbin"},
        )

        gaps = {gap["stage_id"] for gap in artifacts["gap_report"]["gaps"]}
        self.assertIn("move_above_dustbin", gaps)
        self.assertIn("release_into_dustbin", gaps)

    def test_writes_artifact_files(self) -> None:
        artifacts = build_skill_candidate_artifacts(
            selected_task=_selected_task(),
            registry=_registry(),
            generated_manifest={"candidate_id": "tier4_wrapper"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_skill_candidate_artifacts(Path(tmp), artifacts)
            for key in ("staged_plan", "skill_candidates", "gap_report", "report"):
                self.assertTrue(Path(paths[key]).exists(), key)


if __name__ == "__main__":
    unittest.main()
