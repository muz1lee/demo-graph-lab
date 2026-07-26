from __future__ import annotations

import unittest
from types import SimpleNamespace

from ksm.grounding import choose_grounded_label, preflight_runtime_skill_args
from ksm.runner import reset_runtime_state
from ksm.suite import SuiteCandidateRef, SuiteTask
from ksm.suite_runner import build_evaluator_report, episode_outcome, kw_verifier_evidence


class SuiteRunnerOutcomeTests(unittest.TestCase):

    def test_runtime_arg_preflight_overrides_bad_robodojo_place_label(self) -> None:
        class FakeClient:
            def run_reasoning(self, name, kwargs):  # noqa: ANN001, ANN201
                label = kwargs["text"][0]
                xyz = {
                    "orange block:dof": [0.60, -0.32, 0.86],
                    "blue white block:dof": [0.80, -0.10, 0.86],
                    "white block:dof": [0.48, -0.38, 0.86],
                }[label]
                return {"response": {"result": {"status": ["Success"], "xquats": [[xyz]]}}}

        config = SimpleNamespace(pipeline=SimpleNamespace(mode="direct", base_url="http://unused"))
        metadata = {
            "raw": {
                "robodojo": {
                    "target_object": "block_2_prop",
                    "target_import": {"pose": {"position": [0.595, -0.323, 0.782]}},
                    "place_import": {"pose": {"position": [0.481, -0.378, 0.782]}},
                    "binding": {
                        "target_ref": "block_2_prop",
                        "place_ref": "block_1_prop",
                        "primary_pick_label": "orange block:dof",
                        "candidate_pick_labels": ["orange block:dof"],
                        "primary_place_label": "blue white block:dof",
                        "candidate_place_labels": ["blue white block:dof", "white block:dof"],
                    },
                }
            }
        }

        report = preflight_runtime_skill_args(
            config=config,  # type: ignore[arg-type]
            task_metadata=metadata,
            runtime_args={"pick_label": "orange block:dof", "place_label": "blue white block:dof"},
            enabled=True,
            client=FakeClient(),  # type: ignore[arg-type]
        )

        self.assertEqual(report["status"], "overrode_unverified_runtime_args")
        self.assertEqual(report["skill_args"]["place_label"], "white block:dof")
        self.assertEqual(report["overrides"]["place_label"]["from"], "blue white block:dof")

    def test_grounding_prefers_specific_visual_label_over_generic_when_distances_are_equivalent(self) -> None:
        selected = choose_grounded_label(
            [
                {"label": "orange block:dof", "success": True, "xy_distance_m": 0.00953},
                {"label": "积木:dof", "success": True, "xy_distance_m": 0.00952},
                {"label": "block:dof", "success": True, "xy_distance_m": 0.13},
            ]
        )

        self.assertEqual(selected["label"], "orange block:dof")  # type: ignore[index]

    def test_runtime_arg_preflight_reports_failed_label_when_no_grounded_candidate_exists(self) -> None:
        class FakeClient:
            def run_reasoning(self, name, kwargs):  # noqa: ANN001, ANN201
                return {"response": {"result": {"status": ["Success"], "xquats": [[[1.0, 1.0, 0.8]]]}}}

        config = SimpleNamespace(pipeline=SimpleNamespace(mode="direct", base_url="http://unused"))
        metadata = {
            "raw": {
                "robodojo": {
                    "target_object": "block_2_prop",
                    "target_import": {"pose": {"position": [0.0, 0.0, 0.78]}},
                    "binding": {
                        "target_ref": "block_2_prop",
                        "primary_pick_label": "orange block:dof",
                        "candidate_pick_labels": ["orange block:dof"],
                    },
                }
            }
        }

        report = preflight_runtime_skill_args(
            config=config,  # type: ignore[arg-type]
            task_metadata=metadata,
            runtime_args={"pick_label": "orange block:dof"},
            enabled=True,
            client=FakeClient(),  # type: ignore[arg-type]
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_checks"], ["pick_label"])

    def test_reset_runtime_state_resets_manifest_layout_before_pipeline(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def reset_layout(self, *, target, timeout_s):  # noqa: ANN001, ANN201
                self.calls.append(("reset_layout", target, timeout_s))
                return {"ok": True, "target": target}

            def reset_pipeline(self):  # noqa: ANN201
                self.calls.append(("reset_pipeline",))
                return "reset ok"

            def run_control(self, name, kwargs):  # noqa: ANN001, ANN201
                self.calls.append(("run_control", name, kwargs))
                return {"ok": True}

        client = FakeClient()
        report = reset_runtime_state(client)  # type: ignore[arg-type]

        self.assertEqual(client.calls[0][0], "reset_layout")
        self.assertEqual(client.calls[0][1], "manifest")
        self.assertEqual(client.calls[1], ("reset_pipeline",))
        self.assertEqual(report["layout_reset"]["target"], "manifest")

    def test_runtime_arg_preflight_handles_stateful_repeated_stack_args(self) -> None:
        class FakeClient:
            def run_reasoning(self, name, kwargs):  # noqa: ANN001, ANN201
                label = kwargs["text"][0]
                xyz = {
                    "orange block:dof": [0.60, -0.32, 0.86],
                    "white block:dof": [0.48, -0.38, 0.86],
                    "purple block:dof": [0.42, -0.24, 0.86],
                    "bad support:dof": [1.0, 1.0, 0.86],
                }[label]
                return {"response": {"result": {"status": ["Success"], "xquats": [[xyz]]}}}

        config = SimpleNamespace(pipeline=SimpleNamespace(mode="direct", base_url="http://unused"))
        metadata = {
            "raw": {
                "robodojo": {
                    "subtask": {
                        "stateful_plan": {
                            "steps": [
                                {
                                    "source_object": "block_2_prop",
                                    "support_object": "block_1_prop",
                                    "source_import": {"pose": {"position": [0.595, -0.323, 0.782]}},
                                    "support_import": {"pose": {"position": [0.481, -0.378, 0.782]}},
                                    "arg_bindings": {"pick_label": "pick_label_1", "place_label": "place_label_1"},
                                    "primary_pick_label": "orange block:dof",
                                    "candidate_pick_labels": ["orange block:dof"],
                                    "primary_place_label": "white block:dof",
                                    "candidate_place_labels": ["white block:dof"],
                                },
                                {
                                    "source_object": "block_0_prop",
                                    "support_object": "block_2_prop",
                                    "source_import": {"pose": {"position": [0.422, -0.244, 0.782]}},
                                    "support_import": {"pose": {"position": [0.595, -0.323, 0.782]}},
                                    "arg_bindings": {"pick_label": "pick_label_2", "place_label": "place_label_2"},
                                    "primary_pick_label": "purple block:dof",
                                    "candidate_pick_labels": ["purple block:dof"],
                                    "primary_place_label": "orange block:dof",
                                    "candidate_place_labels": ["orange block:dof"],
                                },
                            ]
                        }
                    }
                }
            }
        }

        report = preflight_runtime_skill_args(
            config=config,  # type: ignore[arg-type]
            task_metadata=metadata,
            runtime_args={
                "pick_label_1": "orange block:dof",
                "place_label_1": "bad support:dof",
                "pick_label_2": "purple block:dof",
                "place_label_2": "orange block:dof",
            },
            enabled=True,
            client=FakeClient(),  # type: ignore[arg-type]
        )

        self.assertEqual(report["status"], "overrode_unverified_runtime_args")
        self.assertEqual(report["skill_args"]["place_label_1"], "white block:dof")
        self.assertEqual(report["checks"]["place_label_2"]["status"], "ok")
        self.assertEqual(report["stateful_plan"]["step_count"], 2)

    def test_evaluator_report_keeps_task_effect_evidence_separate(self) -> None:
        task = SuiteTask(
            task_id="put_bottle",
            task_path="/tmp/task.yaml",
            description="put bottle",
            skill_args={},
            predicates=[],
            reset_layout=True,
            metadata={},
        )
        candidate = SuiteCandidateRef(
            candidate_id="cand",
            package_dir="/tmp/cand",
            skill_path="/tmp/cand/skill.yaml",
            manifest_path="/tmp/cand/candidate_manifest.json",
            manifest={},
        )
        report = build_evaluator_report(
            task=task,
            candidate=candidate,
            predicate_report={"predicate_success": False},
            verifier_evidence={"success": None},
            outcome={
                "task_success": False,
                "effect_success": False,
                "verification_source": "kw_predicate",
            },
            policy_ok=True,
            pipeline_success=True,
            skill_success=True,
            failure_signature="predicate_failed",
        )

        self.assertEqual(report["schema"], "ksm.aspire_kw.evaluator_report.v1")
        self.assertEqual(report["source_policy"], "evaluator_only_not_for_agent_prompt")
        self.assertFalse(report["effect_success"])

    def test_pipeline_success_without_effect_feedback_is_not_task_success(self) -> None:
        outcome = episode_outcome(
            pipeline_success=True,
            predicate_success=None,
            verifier_success=None,
        )

        self.assertFalse(outcome["task_success"])
        self.assertIsNone(outcome["effect_success"])
        self.assertEqual(outcome["verification_source"], "missing_effect_feedback")
        self.assertEqual(outcome["failure_signature"], "effect_feedback_missing")

    def test_kw_predicate_success_controls_task_success(self) -> None:
        outcome = episode_outcome(
            pipeline_success=True,
            predicate_success=False,
            verifier_success=True,
        )

        self.assertFalse(outcome["task_success"])
        self.assertFalse(outcome["effect_success"])
        self.assertEqual(outcome["verification_source"], "kw_predicate")
        self.assertEqual(outcome["failure_signature"], "predicate_failed")

    def test_declared_kw_verifier_contract_can_validate_stage_effect(self) -> None:
        task = SuiteTask(
            task_id="pick_stage",
            task_path="/tmp/task.yaml",
            description="pick stage",
            skill_args={},
            predicates=[],
            reset_layout=True,
            metadata={"raw": {"verification": {"type": "kw_verifier"}}},
        )
        candidate = SuiteCandidateRef(
            candidate_id="cand",
            package_dir="/tmp/cand",
            skill_path="/tmp/cand/skill.yaml",
            manifest_path="/tmp/cand/candidate_manifest.json",
            manifest={},
        )
        run_result = SimpleNamespace(
            final_status={
                "logs": [
                    {
                        "status": "success",
                        "logs": [
                            {
                                "status": "success",
                                "action_type": "subskill",
                                "step": {"action": "pickplace/pick_verifier.yaml"},
                            },
                            {
                                "status": "success",
                                "action_type": "subskill",
                                "step": {"action": "pickplace/semantic_pick.yaml"},
                            },
                        ],
                    }
                ]
            }
        )

        verifier = kw_verifier_evidence(
            task=task,
            candidate=candidate,
            run_result=run_result,  # type: ignore[arg-type]
            pipeline_success=True,
        )
        outcome = episode_outcome(
            pipeline_success=True,
            predicate_success=None,
            verifier_success=verifier["success"],
            verifier_status=verifier["status"],
        )

        self.assertTrue(verifier["success"])
        self.assertEqual(verifier["status"], "verified")
        self.assertEqual(verifier["source"], "kw_verifier_gated_skill")
        self.assertTrue(verifier["verifier_actions"])
        self.assertTrue(outcome["task_success"])
        self.assertTrue(outcome["effect_success"])
        self.assertEqual(outcome["verification_source"], "kw_verifier")

    def test_submitted_kw_verifier_action_is_not_effect_success(self) -> None:
        task = SuiteTask(
            task_id="pick_stage",
            task_path="/tmp/task.yaml",
            description="pick stage",
            skill_args={},
            predicates=[],
            reset_layout=True,
            metadata={
                "raw": {
                    "verification": {
                        "type": "kw_verifier",
                        "verifier_actions": ["pickplace/pick_verifier.yaml"],
                    }
                }
            },
        )
        candidate = SuiteCandidateRef(
            candidate_id="cand",
            package_dir="/tmp/cand",
            skill_path="/tmp/cand/skill.yaml",
            manifest_path="/tmp/cand/candidate_manifest.json",
            manifest={},
        )
        run_result = SimpleNamespace(
            final_status={
                "logs": [
                    {
                        "status": "success",
                        "logs": [
                            {
                                "status": "submitted",
                                "action_type": "subskill",
                                "step": {"action": "pickplace/pick_verifier.yaml"},
                            },
                            {
                                "status": "success",
                                "action_type": "subskill",
                                "step": {"action": "pickplace/semantic_pick.yaml"},
                            },
                        ],
                    }
                ]
            }
        )

        verifier = kw_verifier_evidence(
            task=task,
            candidate=candidate,
            run_result=run_result,  # type: ignore[arg-type]
            pipeline_success=True,
        )
        outcome = episode_outcome(
            pipeline_success=True,
            predicate_success=None,
            verifier_success=verifier["success"],
            verifier_status=verifier["status"],
        )

        self.assertFalse(verifier["success"])
        self.assertEqual(verifier["status"], "inconclusive")
        self.assertFalse(outcome["task_success"])
        self.assertFalse(outcome["effect_success"])
        self.assertEqual(outcome["failure_signature"], "verifier_inconclusive")


if __name__ == "__main__":
    unittest.main()
