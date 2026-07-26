from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ksm.agent_nodes import (
    default_node_catalog,
    plan_next_action_node,
    propose_new_skill_spec_node,
    summarize_experiment_history_node,
)
from ksm.agent_skill_spec import propose_new_skill_spec
from ksm.agent_state import (
    ROUTE_NEW_SKILL,
    ROUTE_REUSE,
    ROUTE_STOP_SUCCESS,
    STRATEGY_ITERATE_REUSE,
    STRATEGY_NEED_OBSERVATION,
    STRATEGY_NEW_SKILL,
    STRATEGY_REUSE,
    assert_agent_context_safe,
    decide_next_action,
    load_experiment_state_from_roots,
)
from ksm.io import write_json


class AgentControllerTests(unittest.TestCase):
    def test_reuse_seed_requests_existing_skill_execution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_episode(root, "001", reuse_seed_report("cand_seed"))

            state = load_experiment_state_from_roots(
                objective="run a simple mature pickplace task",
                history_roots=[root],
            )
            decision = decide_next_action(state)

            self.assertEqual(decision.route, ROUTE_REUSE)
            self.assertEqual(decision.strategy, STRATEGY_REUSE)
            self.assertEqual(decision.next_node, "run_suite")
            self.assertFalse(decision.should_call_aspire)
            self.assertEqual(decision.node_request["strategy"], STRATEGY_REUSE)

    def test_early_failed_reuse_requests_iterate_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_episode(root, "001", failed_pick_report("cand_failed"))

            state = load_experiment_state_from_roots(
                objective="repair a reuse candidate that fails inside pick",
                history_roots=[root],
            )
            decision = decide_next_action(state)

            self.assertEqual(decision.strategy, STRATEGY_ITERATE_REUSE)
            self.assertEqual(decision.next_node, "run_aspire_iteration")
            self.assertTrue(decision.should_call_aspire)

    def test_empty_history_requests_observation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = load_experiment_state_from_roots(
                objective="unknown task with no prior evidence",
                history_roots=[Path(td)],
            )
            decision = decide_next_action(state)

            self.assertEqual(decision.strategy, STRATEGY_NEED_OBSERVATION)
            self.assertEqual(decision.next_node, "summarize_experiment_history")

    def test_single_effect_failure_requests_visual_observation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_episode(root, "001", single_effect_failure_report("cand_effect"))

            state = load_experiment_state_from_roots(
                objective="understand a completed pipeline with unclear physical effect",
                history_roots=[root],
            )
            decision = decide_next_action(state)

            self.assertEqual(decision.strategy, STRATEGY_NEED_OBSERVATION)
            self.assertEqual(decision.next_node, "request_feedback_enrichment")
            self.assertFalse(decision.should_call_aspire)

    def test_repeated_reuse_runtime_success_switches_to_new_skill_route_without_predicate_leak(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_episode(root, "001", stack_report("cand_a"))
            write_episode(root, "002", stack_report("cand_b"))

            state = load_experiment_state_from_roots(
                objective="develop a reusable stack behavior skill",
                history_roots=[root],
            )
            decision = decide_next_action(state)

            self.assertEqual(decision.route, ROUTE_NEW_SKILL)
            self.assertEqual(decision.strategy, STRATEGY_NEW_SKILL)
            self.assertEqual(decision.next_node, "request_feedback_enrichment")
            self.assertFalse(decision.should_call_aspire)
            self.assertEqual(decision.node_request["strategy"], STRATEGY_NEW_SKILL)
            self.assertEqual(state.controller_summary["pipeline_success_count"], 2)
            self.assertEqual(
                state.controller_summary["skill_family_counts"]["semantic_pickplace_reuse"],
                2,
            )
            assert_agent_context_safe(decision.agent_prompt_context)
            prompt_text = json.dumps(decision.agent_prompt_context, ensure_ascii=False).lower()
            self.assertNotIn("predicate_report", prompt_text)
            self.assertNotIn("predicate_success", prompt_text)
            self.assertNotIn("stacked(", prompt_text)
            self.assertNotIn("min_xy_overlap", prompt_text)
            self.assertNotIn("z_gap", prompt_text)

    def test_successful_candidate_stops_search(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = stack_report("cand_success")
            report["task_success"] = True
            report["success"] = True
            write_episode(root, "001", report)

            state = load_experiment_state_from_roots(
                objective="preserve successful candidate",
                history_roots=[root],
            )
            decision = decide_next_action(state)

            self.assertEqual(decision.route, ROUTE_STOP_SUCCESS)
            self.assertEqual(decision.next_node, "stop_and_report")

    def test_node_catalog_and_offline_nodes_are_read_only_planning_surface(self) -> None:
        names = {node.name for node in default_node_catalog()}
        self.assertIn("summarize_experiment_history", names)
        self.assertIn("plan_next_action", names)
        self.assertIn("run_aspire_iteration", names)
        self.assertIn("request_feedback_enrichment", names)
        self.assertIn("propose_new_skill_spec", names)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_episode(root, "001", stack_report("cand_a"))
            write_episode(root, "002", stack_report("cand_b"))

            history = summarize_experiment_history_node(
                objective="develop a reusable stack behavior skill",
                history_roots=[root],
            )
            state = load_experiment_state_from_roots(
                objective="develop a reusable stack behavior skill",
                history_roots=[root],
            )
            decision = plan_next_action_node(state)

            self.assertEqual(history.status, "ok")
            self.assertEqual(decision.status, "ok")
            self.assertEqual(decision.output["decision"]["route"], ROUTE_NEW_SKILL)

    def test_new_skill_spec_waits_for_visual_feedback_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_episode(root, "001", stack_report("cand_a"))
            write_episode(root, "002", stack_report("cand_b"))
            state = load_experiment_state_from_roots(
                objective="develop a reusable stack behavior skill",
                history_roots=[root],
            )
            decision = decide_next_action(state)

            spec = propose_new_skill_spec(state=state, decision=decision)

            self.assertEqual(spec["status"], "needs_observation")
            self.assertEqual(spec["next_node"]["node"], "request_feedback_enrichment")
            assert_agent_context_safe(spec["agent_prompt_context"])

    def test_new_skill_spec_proposes_stack_step_when_visual_feedback_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_episode(root, "001", visual_stack_report("cand_a"))
            write_episode(root, "002", visual_stack_report("cand_b"))
            state = load_experiment_state_from_roots(
                objective="develop a reusable stack behavior skill",
                history_roots=[root],
            )
            decision = decide_next_action(state)

            self.assertEqual(decision.strategy, STRATEGY_NEW_SKILL)
            self.assertEqual(decision.next_node, "propose_new_skill_spec")
            node = propose_new_skill_spec_node(state=state, decision=decision)
            spec = node.output["new_skill_spec"]

            self.assertEqual(node.status, "proposed")
            self.assertEqual(spec["target_skill_family"], "stacking")
            self.assertEqual(spec["reusable_interface"]["name"], "stack_step")
            self.assertIn("pickplace/semantic_pickplace.yaml", spec["reuse_policy"]["selected_existing_skills"])
            spec_text = json.dumps(spec, ensure_ascii=False).lower()
            self.assertNotIn("predicate_report", spec_text)
            self.assertNotIn("min_xy_overlap", spec_text)


def write_episode(root: Path, name: str, report: dict) -> None:
    episode_dir = root / "episodes" / name
    episode_dir.mkdir(parents=True)
    write_json(episode_dir / "episode_report.json", report)


def stack_report(candidate_id: str) -> dict:
    return {
        "schema": "ksm.aspire_kw.episode_report.v1",
        "candidate_id": candidate_id,
        "task_id": "robodojo_stack_blocks_000_stateful_stack_3_objects",
        "success": False,
        "task_success": False,
        "pipeline_success": True,
        "execution_success": True,
        "skill_success": True,
        "predicate_success": False,
        "metadata": {
            "predicate_report": {
                "predicate_success": False,
                "predicates": [
                    {
                        "native": {
                            "label": "stacked(block_0_prop, block_1_prop)",
                            "detail": {
                                "measurements": {
                                    "min_xy_overlap_ratio": 0.1,
                                    "z_gap_m": -0.03,
                                }
                            },
                        }
                    }
                ],
            },
            "candidate_manifest": {
                "metadata": {
                    "llm_skill_reuse_decision": {
                        "decision": "reuse_existing_skill",
                        "candidate_role": "pure_wrapper_reuse",
                        "selected_existing_skills": ["pickplace/semantic_pickplace.yaml"],
                    }
                }
            },
        },
        "feedback_attribution": {
            "agent_feedback": {
                "schema": "ksm.aspire_kw.agent_feedback.v1",
                "source_policy": "agent_observable_only",
                "observable_status": "pipeline_completed_effect_unconfirmed_by_agent_feedback",
                "pipeline_success": True,
                "skill_success": True,
                "observable_failure_signature": "pipeline_completed_effect_unconfirmed_by_agent_feedback",
                "action_timeline": [
                    {
                        "action": "pickplace/semantic_pickplace.yaml",
                        "status": "success",
                        "description": "completed semantic pickplace",
                    }
                ],
                "failed_actions": [],
                "first_failed_action": None,
                "evidence_gaps": ["missing_structured_visual_feedback"],
                "recurring_summary": "Pipeline completed; task effect is not confirmed by agent-observable feedback.",
            }
        },
    }


def reuse_seed_report(candidate_id: str) -> dict:
    report = stack_report(candidate_id)
    report.pop("success", None)
    report.pop("task_success", None)
    report["pipeline_success"] = False
    report["execution_success"] = False
    report["skill_success"] = False
    report["predicate_success"] = None
    report["feedback_attribution"]["agent_feedback"]["observable_status"] = "reuse_seed_not_executed"
    report["feedback_attribution"]["agent_feedback"]["pipeline_success"] = False
    report["feedback_attribution"]["agent_feedback"]["skill_success"] = False
    report["feedback_attribution"]["agent_feedback"]["action_timeline"] = []
    report["feedback_attribution"]["agent_feedback"]["evidence_gaps"] = []
    return report


def failed_pick_report(candidate_id: str) -> dict:
    report = stack_report(candidate_id)
    report["pipeline_success"] = False
    report["execution_success"] = False
    report["skill_success"] = False
    report["feedback_attribution"]["agent_feedback"]["observable_status"] = "pipeline_failed"
    report["feedback_attribution"]["agent_feedback"]["pipeline_success"] = False
    report["feedback_attribution"]["agent_feedback"]["skill_success"] = False
    report["feedback_attribution"]["agent_feedback"]["failed_actions"] = [
        "pickplace/semantic_pick.yaml",
    ]
    report["feedback_attribution"]["agent_feedback"]["first_failed_action"] = "pickplace/semantic_pick.yaml"
    report["feedback_attribution"]["agent_feedback"]["action_timeline"] = [
        {
            "action": "pickplace/semantic_pick.yaml",
            "status": "failed",
            "description": "pick failed before stable object motion",
        }
    ]
    return report


def single_effect_failure_report(candidate_id: str) -> dict:
    report = stack_report(candidate_id)
    report["pipeline_success"] = True
    report["execution_success"] = True
    report["skill_success"] = True
    report["feedback_attribution"]["agent_feedback"]["observable_status"] = (
        "pipeline_completed_effect_unconfirmed_by_agent_feedback"
    )
    report["feedback_attribution"]["agent_feedback"]["pipeline_success"] = True
    report["feedback_attribution"]["agent_feedback"]["skill_success"] = True
    report["feedback_attribution"]["agent_feedback"]["failed_actions"] = []
    report["feedback_attribution"]["agent_feedback"]["first_failed_action"] = None
    report["feedback_attribution"]["agent_feedback"]["action_timeline"] = [
        {
            "action": "pickplace/semantic_pickplace.yaml",
            "status": "success",
            "description": "completed semantic pickplace",
        }
    ]
    report["feedback_attribution"]["agent_feedback"]["evidence_gaps"] = [
        "missing_failed_step_attribution",
    ]
    return report


def visual_stack_report(candidate_id: str) -> dict:
    report = stack_report(candidate_id)
    feedback = report["feedback_attribution"]["agent_feedback"]
    feedback["evidence_gaps"] = ["missing_failed_step_attribution"]
    feedback["visual_feedback"] = {
        "status": "analyzed",
        "analysis_available": True,
        "evidence": "The object was released near the support but visible placement stability is uncertain.",
        "visible_state_changes": [
            {
                "object": "top block",
                "changed": True,
                "summary": "Object moved during placement attempt.",
            }
        ],
    }
    return report
