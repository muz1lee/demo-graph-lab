from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ksm.feedback_attribution import (
    agent_safe_payload,
    analyze_episode_report,
    build_feedback_memory,
    build_task_analysis_state,
)
from ksm.io import write_json
from ksm.evaluation import summarize_episode_reports
from ksm.leaderboard import scan_episode_reports
from ksm.skill_library import build_skill_context_packet, distill_skill_library_entries
from ksm.visual_feedback import build_visual_evidence


class FeedbackAttributionTests(unittest.TestCase):
    def test_visual_feedback_sidecar_enters_agent_observable_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sidecar = root / "visual_feedback.json"
            write_json(
                sidecar,
                {
                    "analysis": {
                        "provider": "test-vlm",
                        "robot_motion_visible": True,
                        "grasp_attempt_visible": True,
                        "target_contact_visible": True,
                        "target_object_displaced": True,
                        "target_object_lifted": False,
                        "task_success": False,
                    }
                },
            )
            evidence = build_visual_evidence(
                task_metadata={},
                candidate_manifest={
                    "metadata": {
                        "visual_feedback": {
                            "provider": "test-vlm",
                            "analysis_path": str(sidecar),
                            "artifacts": {"video": "/tmp/run.mp4"},
                        }
                    }
                },
                episode_dir=root,
            )
            report = failed_episode_report("cand_visual")
            report["metadata"]["visual_evidence"] = evidence

            attribution = analyze_episode_report(report)

            visual = attribution["agent_feedback"]["visual_feedback"]
            self.assertTrue(visual["analysis_available"])
            self.assertTrue(visual["robot_motion_visible"])
            self.assertTrue(visual["grasp_attempt_visible"])
            self.assertTrue(visual["target_contact_visible"])
            self.assertFalse(visual["target_object_lifted"])
            self.assertNotIn("missing_structured_visual_feedback", attribution["evidence_gaps"])

    def test_generic_visual_feedback_enters_agent_observable_feedback(self) -> None:
        report = failed_episode_report("cand_generic_visual")
        report["metadata"]["visual_evidence"] = {
            "schema": "ksm.visual_feedback.v1",
            "status": "analyzed",
            "analysis_available": True,
            "analysis": {
                "temporal_observations": [
                    {"frame_range": "1-4", "observation": "gripper moves near a bottle"}
                ],
                "robot_object_interactions": [
                    {
                        "actor": "gripper",
                        "object": "blue bottle",
                        "interaction": "approach",
                        "evidence": "visible approach without stable lift",
                    }
                ],
                "visible_state_changes": [
                    {
                        "object": "blue bottle",
                        "changed": False,
                        "change": "none",
                        "summary": "Bottle did not visibly move.",
                    }
                ],
                "evidence_summary": "Robot moved but the bottle did not visibly change pose.",
                "suggested_next_probe": "capture before/after object pose around grasp close",
            },
        }

        attribution = analyze_episode_report(report)

        visual = attribution["agent_feedback"]["visual_feedback"]
        self.assertTrue(visual["analysis_available"])
        self.assertIn("visible_state_changes", visual)
        self.assertEqual(visual["evidence"], "Robot moved but the bottle did not visibly change pose.")
        self.assertNotIn("missing_structured_visual_feedback", attribution["evidence_gaps"])

    def test_analyzes_failed_pick_episode_with_runtime_arg_delta(self) -> None:
        report = failed_episode_report("cand_a")

        attribution = analyze_episode_report(report)

        self.assertEqual(attribution["first_failed_action"], "motion_planning/mp_head_to_target.yaml")
        self.assertIn("motion_planning_action_failed", attribution["inferred_failure_modes"])
        self.assertIn("semantic_pick_action_failed", attribution["inferred_failure_modes"])
        self.assertEqual(attribution["subgoal_failure_breakdown"]["pregrasp_or_approach"], 1)
        self.assertTrue(attribution["runtime_arg_delta"]["runtime_matches_candidate"])
        self.assertEqual(
            attribution["runtime_arg_delta"]["candidate_overrides"]["use_motion_planning"],
            {"task": False, "candidate": True},
        )
        self.assertIn("missing_structured_visual_feedback", attribution["evidence_gaps"])
        self.assertIn("First failed action: motion_planning/mp_head_to_target.yaml.", attribution["recurring_summary"])
        self.assertIn("motion_planning/mp_head_to_target.yaml", attribution["negative_evidence"]["failed_actions"])
        self.assertIn("First failed action: motion_planning/mp_head_to_target.yaml.", attribution["negative_evidence"]["reason"])

    def test_agent_visible_signature_prefers_concrete_failed_action(self) -> None:
        report = failed_episode_report("cand_ctrl")
        report["failure_signature"] = "vision_grounding_failed"
        report["failure_analysis"]["category"] = "perception"
        report["metadata"]["run_result"]["final_status"]["logs"][0]["logs"] = [
            {
                "status": "failed",
                "action_type": "ctrl",
                "error": "Control failed: 1",
                "step": {
                    "action": "/ctrl/xquat_move",
                    "description": "move grasped block above validated support pose",
                },
            }
        ]

        attribution = analyze_episode_report(report)

        self.assertEqual(attribution["failure_signature"], "vision_grounding_failed")
        self.assertEqual(
            attribution["agent_feedback"]["observable_failure_signature"],
            "action_failed:/ctrl/xquat_move",
        )

    def test_semantic_pick_failure_uses_generic_object_acquisition_for_non_bottle_tasks(self) -> None:
        report = failed_episode_report("cand_stack_pick")
        report["task_id"] = "robodojo_stack_blocks_000"
        report["metadata"]["run_result"]["final_status"]["logs"][0]["logs"] = [
            {
                "status": "failed",
                "action_type": "subskill",
                "step": {
                    "action": "pickplace/semantic_pick.yaml",
                    "description": "pick orange block",
                },
            }
        ]
        report["failure_analysis"]["failed_actions"] = ["pickplace/semantic_pick.yaml"]

        attribution = analyze_episode_report(report)

        self.assertIn("object_acquisition", attribution["subgoal_failure_breakdown"])
        self.assertNotIn("pick_bottle", attribution["subgoal_failure_breakdown"])

    def test_feedback_memory_rebuilds_stale_embedded_attribution(self) -> None:
        report = failed_episode_report("cand_stale_feedback")
        report["task_id"] = "robodojo_stack_blocks_000"
        report["metadata"]["run_result"]["final_status"]["logs"][0]["logs"] = [
            {
                "status": "failed",
                "action_type": "subskill",
                "step": {
                    "action": "pickplace/semantic_pick.yaml",
                    "description": "pick orange block",
                },
            }
        ]
        report["feedback_attribution"] = {
            "schema": "ksm.aspire_kw.feedback_attribution.v1",
            "agent_feedback": {
                "subgoal_failure_breakdown": {"pick_bottle": 1},
                "evidence_gaps": [],
                "recurring_summary": "stale feedback",
            },
        }

        memory = build_feedback_memory([report])

        self.assertIn("object_acquisition", memory["subgoal_failure_breakdown"])
        self.assertNotIn("pick_bottle", memory["subgoal_failure_breakdown"])

    def test_feedback_memory_surfaces_open_negative_evidence_without_static_strategy_family(self) -> None:
        report = failed_episode_report("cand_single_negative")
        report["feedback_attribution"] = analyze_episode_report(report)

        memory = build_feedback_memory([report])

        self.assertNotIn("failed_strategy_families", memory)
        negative = memory["candidate_states"][0]["negative_evidence"]
        self.assertEqual(negative["candidate_id"], "cand_single_negative")
        self.assertIn("motion_planning/mp_head_to_target.yaml", negative["failed_actions"])
        self.assertIn("First failed action: motion_planning/mp_head_to_target.yaml.", negative["reason"])
        self.assertNotIn("failure_key", negative)
        self.assertNotIn("strategy_features", negative)

    def test_leaderboard_rebuilds_stale_embedded_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            episode_dir = root / "episodes" / "001"
            episode_dir.mkdir(parents=True)
            report = stale_stack_pick_report("cand_stale_leaderboard")
            write_json(episode_dir / "episode_report.json", report)

            summary = scan_episode_reports(root)

            candidate = summary["candidates"][0]
            self.assertIn("object_acquisition", candidate["subgoal_failure_breakdown"])
            self.assertNotIn("pick_bottle", candidate["subgoal_failure_breakdown"])
            self.assertIn("object_acquisition", summary["feedback_memory"]["subgoal_failure_breakdown"])
            self.assertNotIn("pick_bottle", summary["feedback_memory"]["subgoal_failure_breakdown"])

    def test_evaluation_summary_rebuilds_stale_embedded_attribution(self) -> None:
        summary = summarize_episode_reports([stale_stack_pick_report("cand_stale_eval")])

        self.assertIn("object_acquisition", summary["subgoal_failure_breakdown"])
        self.assertNotIn("pick_bottle", summary["subgoal_failure_breakdown"])

    def test_agent_feedback_omits_evaluator_details_kept_in_report_logs(self) -> None:
        report = failed_episode_report("cand_predicate")
        report["failure_signature"] = "predicate_failed"
        report["predicate_success"] = False
        report["failure_analysis"]["category"] = "predicate"
        report["failure_analysis"]["stage"] = "verify_predicate"
        report["metadata"]["predicate_report"] = {
            "status": "failed",
            "predicate_success": False,
            "predicates": [{"name": "inside", "success": False}],
        }

        attribution = analyze_episode_report(report)

        agent = attribution["agent_feedback"]
        self.assertNotIn("evaluator_feedback", attribution)
        self.assertNotIn("debug_feedback", attribution)
        self.assertEqual(report["predicate_success"], False)
        self.assertEqual(report["metadata"]["predicate_report"]["status"], "failed")
        agent_text = str(agent).lower()
        self.assertNotIn("predicate_success", agent_text)
        self.assertNotIn("predicate_report", agent_text)
        self.assertNotIn("inside", agent_text)
        self.assertEqual(agent["observable_failure_signature"], "agent_visible_failure_unspecified")

        prompt_view = agent_safe_payload(attribution)
        prompt_text = str(prompt_view).lower()
        self.assertIn("agent_feedback", prompt_text)
        self.assertNotIn("predicate_success", prompt_text)
        self.assertNotIn("predicate_report", prompt_text)
        self.assertNotIn("inside", prompt_text)

    def test_builds_open_ended_feedback_memory_and_negative_cluster(self) -> None:
        reports = [failed_episode_report("cand_a"), failed_episode_report("cand_b")]
        for report in reports:
            report["feedback_attribution"] = analyze_episode_report(report)

        memory = build_feedback_memory(reports)

        self.assertEqual(memory["num_reports"], 2)
        self.assertEqual(memory["trace_failure_breakdown"]["motion_planning_action_failed"], 2)
        self.assertEqual(memory["subgoal_failure_breakdown"]["pregrasp_or_approach"], 2)
        self.assertNotIn("failed_strategy_families", memory)
        self.assertEqual(len(memory["candidate_states"]), 2)
        self.assertTrue(all(state["negative_evidence"]["reason"] for state in memory["candidate_states"]))
        self.assertIn("missing_structured_visual_feedback:2", memory["evidence_gaps"])
        self.assertTrue(any("structured visual feedback" in item for item in memory["open_questions"]))

    def test_leaderboard_scans_feedback_memory_from_episode_reports(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            episode_dir = root / "episodes" / "001"
            episode_dir.mkdir(parents=True)
            report = failed_episode_report("cand_a")
            write_json(episode_dir / "episode_report.json", report)

            summary = scan_episode_reports(root)

            self.assertEqual(summary["feedback_memory"]["num_reports"], 1)
            candidate = summary["candidates"][0]
            self.assertEqual(candidate["trace_failure_breakdown"]["motion_planning_action_failed"], 1)
            self.assertEqual(candidate["subgoal_failure_breakdown"]["pregrasp_or_approach"], 1)
            self.assertIn("missing_structured_visual_feedback", candidate["evidence_gaps"])
            self.assertEqual(summary["task_analysis_state"]["schema"], "ksm.aspire_kw.task_analysis_state.v1")
            self.assertEqual(summary["task_analysis_state"]["candidate_states"][0]["status"], "failed_on_scan")

    def test_builds_task_analysis_state_for_aspire_history(self) -> None:
        reports = [failed_episode_report("cand_a"), failed_episode_report("cand_b")]
        for report in reports:
            report["feedback_attribution"] = analyze_episode_report(report)
        memory = build_feedback_memory(reports)

        state = build_task_analysis_state(
            suite_id="robodojo_suite",
            task_ids=["robodojo_pick_bottle"],
            stage="debug",
            manifest_path="/tmp/suite.yaml",
            run_dir="/tmp/run",
            success_threshold=1.0,
            reports=reports,
            candidates=[
                {
                    "candidate_id": "cand_a",
                    "num_trials": 1,
                    "task_completed": 0,
                    "success_rate": 0.0,
                    "failure_breakdown": {"skill_execution_failed": 1},
                    "trace_failure_breakdown": {"motion_planning_action_failed": 1},
                    "subgoal_failure_breakdown": {"pregrasp_or_approach": 1},
                    "evidence_gaps": {"missing_structured_visual_feedback": 1},
                }
            ],
            feedback_memory=memory,
        )

        self.assertEqual(state["task_id"], "robodojo_pick_bottle")
        self.assertEqual(state["candidate_states"][0]["status"], "failed_on_debug")
        self.assertEqual(state["eliminated_directions"][0]["candidate_id"], "cand_a")
        self.assertNotIn("failed_strategy_families", state)
        self.assertTrue(state["retrieved_negative_evidence"])
        self.assertIn("missing_structured_visual_feedback:2", state["evidence_gaps"])

    def test_task_analysis_state_does_not_turn_success_into_negative_family(self) -> None:
        reports = [success_episode_report("cand_a"), success_episode_report("cand_b")]
        for report in reports:
            report["feedback_attribution"] = analyze_episode_report(report)
        memory = build_feedback_memory(reports)

        state = build_task_analysis_state(
            suite_id="robodojo_suite",
            task_ids=["robodojo_pick_bottle"],
            stage="suite_run",
            manifest_path="/tmp/suite.yaml",
            run_dir="/tmp/run",
            success_threshold=1.0,
            reports=reports,
            candidates=[
                {
                    "candidate_id": "cand_a",
                    "num_trials": 1,
                    "task_completed": 1,
                    "success_rate": 1.0,
                    "failure_breakdown": {"success": 1},
                    "trace_failure_breakdown": {"success": 1},
                },
                {
                    "candidate_id": "cand_b",
                    "num_trials": 1,
                    "task_completed": 1,
                    "success_rate": 1.0,
                    "failure_breakdown": {"success": 1},
                    "trace_failure_breakdown": {"success": 1},
                },
            ],
            feedback_memory=memory,
        )

        self.assertEqual(state["candidate_states"][0]["status"], "validated_on_suite_run")
        self.assertTrue(state["candidate_states"][0]["success"])
        self.assertEqual(state["eliminated_directions"], [])
        self.assertNotIn("failed_strategy_families", state)
        self.assertEqual(state["retrieved_negative_evidence"], [])

    def test_pipeline_only_reported_success_is_untrusted_not_validated(self) -> None:
        report = legacy_pipeline_only_success_report("cand_a")
        report["feedback_attribution"] = analyze_episode_report(report)

        self.assertFalse(report["feedback_attribution"]["success"])
        self.assertTrue(report["feedback_attribution"]["reported_success"])
        self.assertTrue(report["feedback_attribution"]["untrusted_success_evidence"])
        self.assertEqual(
            report["feedback_attribution"]["success_evidence_level"],
            "reported_success_without_effect_evidence",
        )
        self.assertIsNone(report["feedback_attribution"]["negative_evidence"])

        memory = build_feedback_memory([report])
        self.assertEqual(memory["candidate_states"][0]["status"], "untrusted_success_evidence")
        self.assertFalse(memory["candidate_states"][0]["success"])

        state = build_task_analysis_state(
            suite_id="robodojo_suite",
            task_ids=["robodojo_pick_bottle"],
            stage="suite_run",
            reports=[report],
            candidates=[
                {
                    "candidate_id": "cand_a",
                    "num_trials": 1,
                    "task_completed": 1,
                    "success_rate": 1.0,
                    "failure_breakdown": {"success": 1},
                    "trace_failure_breakdown": {"success": 1},
                }
            ],
            feedback_memory=memory,
        )

        self.assertEqual(state["candidate_states"][0]["status"], "untrusted_success_evidence")
        self.assertFalse(state["candidate_states"][0]["success"])
        self.assertEqual(state["candidate_states"][0]["success_rate"], 0.0)
        self.assertEqual(state["eliminated_directions"], [])
        self.assertNotIn("failed_strategy_families", state)
        self.assertEqual(state["retrieved_negative_evidence"], [])

    def test_skill_library_distill_downgrades_pipeline_only_success(self) -> None:
        report = legacy_pipeline_only_success_report("cand_a")
        with tempfile.TemporaryDirectory() as td:
            entries = distill_skill_library_entries(
                suite_run={"episodes": [{"report": report, "episode_id": "episode_cand_a"}]},
                output_root=Path(td),
                generation_index=1,
            )

            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertFalse(entry["success"])
            self.assertTrue(entry["reported_success"])
            self.assertTrue(entry["untrusted_success_evidence"])
            self.assertEqual(entry["success_evidence_level"], "reported_success_without_effect_evidence")

            saved = Path(entry["path"]).read_text(encoding="utf-8")
            self.assertIn('"success": false', saved)
            self.assertIn('"reported_success": true', saved)

    def test_skill_library_scan_sanitizes_legacy_success_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(
                root / "legacy_success.json",
                {
                    "schema": "ksm.skill_library.evidence_entry.v1",
                    "candidate_id": "legacy_success",
                    "success": True,
                    "pipeline_success": True,
                    "predicate_success": None,
                    "hypothesis": "Use stable direct pick settings.",
                },
            )

            packet = build_skill_context_packet(
                root=root,
                task={"task_id": "robodojo_pick_bottle"},
                history={},
                top_k=1,
                snippet_chars=1000,
                max_chars=2000,
            )

            snippet = packet["selected"][0]["snippet"]
            self.assertIn('"reported_success": true', snippet)
            self.assertIn('"untrusted_success_evidence": true', snippet)
            self.assertIn("excluded_from_success_memory", snippet)
            self.assertNotIn("predicate_success", snippet)
            self.assertNotIn("verifier_success", snippet)
            self.assertNotIn('"success":', snippet)


def failed_episode_report(candidate_id: str) -> dict:
    return {
        "schema": "ksm.aspire_kw.episode_report.v1",
        "suite_id": "suite",
        "episode_id": f"episode_{candidate_id}",
        "candidate_id": candidate_id,
        "task_id": "robodojo_pick_bottle",
        "success": False,
        "pipeline_success": False,
        "skill_success": False,
        "predicate_success": None,
        "policy_ok": True,
        "failure_signature": "skill_execution_failed",
        "failure_analysis": {
            "schema": "ksm.aspire_kw.failure_analysis.v1",
            "category": "skill_execution",
            "stage": "skill_runtime",
            "primary_signal": "skill_execution_failed",
            "failed_actions": [
                "motion_planning/mp_head_to_target.yaml",
                "pickplace/semantic_pick.yaml",
            ],
            "service_related": False,
            "action_related": True,
            "recommended_focus": "failed_action_mechanism",
        },
        "artifacts": {},
        "metadata": {
            "task_args": {
                "direct_pick": True,
                "use_motion_planning": False,
                "pick_check_offset": [0.0, 0.0, 0.07],
            },
            "candidate_skill_args": {
                "direct_pick": False,
                "use_motion_planning": True,
                "pick_check_offset": [0.0, 0.0, 0.12],
            },
            "runtime_args": {
                "direct_pick": False,
                "use_motion_planning": True,
                "pick_check_offset": [0.0, 0.0, 0.12],
            },
            "visual_evidence": {
                "status": "human_review_required",
                "note": "WebUI frames/video should be reviewed alongside predicate evidence.",
            },
            "candidate_manifest": {
                "hypothesis": "Enable motion planning and increase pick offset.",
                "change_summary": "Switch direct_pick to false and use mp_head_to_target.",
            },
            "run_result": {
                "final_status": {
                    "logs": [
                        {
                            "status": "failed",
                            "logs": [
                                {
                                    "status": "failed",
                                    "action_type": "subskill",
                                    "step": {
                                        "action": "motion_planning/mp_head_to_target.yaml",
                                        "description": "plan pre-pick approach",
                                    },
                                },
                                {
                                    "status": "failed",
                                    "action_type": "subskill",
                                    "step": {
                                        "action": "pickplace/semantic_pick.yaml",
                                        "description": "planned semantic pick",
                                    },
                                },
                            ],
                        }
                    ]
                }
            },
        },
    }


def success_episode_report(candidate_id: str) -> dict:
    report = failed_episode_report(candidate_id)
    report["success"] = True
    report["task_success"] = True
    report["effect_success"] = True
    report["verifier_success"] = True
    report["pipeline_success"] = True
    report["skill_success"] = True
    report["failure_signature"] = "success"
    report["failure_analysis"] = {
        "schema": "ksm.aspire_kw.failure_analysis.v1",
        "category": "success",
        "stage": "completed",
        "primary_signal": "success",
        "failed_actions": [],
        "service_related": False,
        "action_related": False,
        "recommended_focus": "runtime_evidence",
    }
    report["metadata"]["run_result"]["final_status"] = {"status": "success", "logs": []}
    report["metadata"]["candidate_manifest"] = {
        "hypothesis": "Use stable direct pick settings.",
        "change_summary": "Keep known successful settings.",
    }
    return report


def stale_stack_pick_report(candidate_id: str) -> dict:
    report = failed_episode_report(candidate_id)
    report["task_id"] = "robodojo_stack_blocks_000"
    report["metadata"]["run_result"]["final_status"]["logs"][0]["logs"] = [
        {
            "status": "failed",
            "action_type": "subskill",
            "step": {
                "action": "pickplace/semantic_pick.yaml",
                "description": "pick orange block",
            },
        }
    ]
    report["feedback_attribution"] = {
        "schema": "ksm.aspire_kw.feedback_attribution.v1",
        "agent_feedback": {
            "subgoal_failure_breakdown": {"pick_bottle": 1},
            "evidence_gaps": [],
            "recurring_summary": "stale feedback",
        },
        "subgoal_failure_breakdown": {"pick_bottle": 1},
        "recurring_summary": "stale feedback",
    }
    return report


def legacy_pipeline_only_success_report(candidate_id: str) -> dict:
    report = success_episode_report(candidate_id)
    report.pop("task_success", None)
    report.pop("effect_success", None)
    report.pop("verifier_success", None)
    report["predicate_success"] = None
    return report


if __name__ == "__main__":
    unittest.main()
