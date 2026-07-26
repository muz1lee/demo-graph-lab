from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ksm.skill_candidates import build_skill_candidate_artifacts
from ksm.staged_state import build_stage_state_artifacts, write_stage_state_artifacts


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
            "primary_pick_label": "bottle:dof",
            "candidate_pick_labels": ["bottle:dof"],
            "primary_place_label": "dustbin",
            "candidate_place_labels": ["dustbin"],
        },
    }


def _suite_run(predicate_success: bool = False) -> dict:
    predicate_report = {
        "schema": "ksm.aspire_kw.predicate_evaluation.v1",
        "status": "failed" if not predicate_success else "ok",
        "predicate_success": predicate_success,
        "predicates": [
            {
                "type": "inside",
                "supported": True,
                "success": predicate_success,
                "object": "bottle3_prop",
                "container": "dustbin_prop",
            }
        ],
    }
    episode_report = {
        "pipeline_success": True,
        "skill_success": True,
        "predicate_success": predicate_success,
        "failure_signature": "predicate_failed" if not predicate_success else "success",
        "failure_analysis": {
            "category": "predicate" if not predicate_success else "success",
            "stage": "verify_predicate" if not predicate_success else "completed",
        },
        "artifacts": {
            "episode_dir": "/tmp/episode",
            "predicate_report": "/tmp/episode/predicate_report.json",
        },
        "metadata": {
            "execute": True,
            "predicate_report": predicate_report,
        },
    }
    return {
        "run_dir": "/tmp/suite_run",
        "episodes": [{"report": episode_report}],
    }


class StagedStateTests(unittest.TestCase):
    def test_projects_whole_workflow_result_onto_static_stages(self) -> None:
        candidates = build_skill_candidate_artifacts(
            selected_task=_selected_task(),
            registry=_registry(),
            generated_manifest={
                "candidate_id": "tier4_wrapper",
                "policy": {"actions": ["pickplace/semantic_pickplace.yaml", "/ctrl/go_home"]},
            },
        )
        artifacts = build_stage_state_artifacts(
            skill_candidate_artifacts=candidates,
            suite_run=_suite_run(predicate_success=False),
            before_frames={"frames": [{"path": "/tmp/before.jpg"}]},
            after_frames={"frames": [{"path": "/tmp/after.jpg"}]},
            generated_manifest={"candidate_id": "tier4_wrapper", "policy": {"actions": ["/ctrl/go_home"]}},
            package={"package_dir": "/tmp/package", "skill_path": "/tmp/package/skill.yaml"},
        )

        self.assertEqual(artifacts["summary"]["stage_count"], 6)
        self.assertEqual(artifacts["summary"]["first_observed_failed_stage"], "verify_inside")
        by_stage = {record["stage"]["stage_id"]: record for record in artifacts["records"]}
        self.assertEqual(by_stage["bind_selected_object_container"]["stage"]["outcome"]["execution_status"], "observed_passed")
        self.assertEqual(by_stage["verify_inside"]["stage"]["outcome"]["execution_status"], "observed_failed")
        self.assertTrue(by_stage["release_into_dustbin"]["stage"]["outcome"]["promotion_blocker"])
        self.assertFalse(by_stage["pick_bottle"]["replay"]["executable_now"])

    def test_writes_state_records_and_prefix_replay_files(self) -> None:
        candidates = build_skill_candidate_artifacts(selected_task=_selected_task(), registry=_registry())
        artifacts = build_stage_state_artifacts(skill_candidate_artifacts=candidates, suite_run=_suite_run())
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_stage_state_artifacts(Path(tmp), artifacts)
            self.assertTrue(Path(paths["stage_state_records"]).exists())
            self.assertTrue(Path(paths["state_summary"]).exists())
            verify_inside = paths["stage_records"]["verify_inside"]
            self.assertTrue(Path(verify_inside["state_record"]).exists())
            self.assertTrue(Path(verify_inside["prefix_replay"]).exists())


if __name__ == "__main__":
    unittest.main()
