from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from ksm.aspire import _package_generated_candidate, build_aspire_prompt
from ksm.aspire_loop import run_aspire_iteration
from ksm.candidate import load_candidate_package, package_skill_candidate
from ksm.config import ArtifactConfig, LLMConfig, ManagerConfig, PipelineConfig
from ksm.generator import generate_skill_from_task
from ksm.io import write_yaml
from ksm.llm import GPTChatClient, LLMResponse, ResolvedLLMConfig
from ksm.llm_generator import (
    generate_skill_from_task_llm,
    validate_task_contract,
)
from ksm.policy import check_skill
from ksm.registry import SkillSummary, ToolRegistry
from ksm.robodojo_auto import RobodojoPoolItem, build_robodojo_prompt
from ksm.suite import SuiteCandidateRef, SuiteSpec, SuiteTask
from ksm.suite_runner import runtime_skill_args


class PolicyGeneratorTests(unittest.TestCase):
    def test_no_output_identity_action_is_rejected_as_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skill_path = Path(td) / "bad_identity.yaml"
            write_yaml(
                skill_path,
                {
                    "schema_version": "1.0.0",
                    "name": "bad_identity",
                    "description": "bad",
                    "args": {},
                    "workflow": [
                        {
                            "action": "/head/reasoning/identity",
                            "args": {"text": "Verify if the effect happened"},
                        }
                    ],
                },
            )

            result = check_skill(skill_path, fake_registry())

        self.assertFalse(result.ok)
        self.assertTrue(any("no-output identity" in item for item in result.violations))

    def test_stateful_plan_contract_rejects_single_step_wrapper(self) -> None:
        task = {
            "task_id": "stack_all",
            "robodojo": {
                "subtask": {
                    "stateful_plan": {
                        "schema": "ksm.robodojo.stateful_plan.v1",
                        "plan_type": "repeated_binary_relation",
                        "relation": "stacked",
                        "steps": [
                            {"arg_bindings": {"pick_label": "pick_label_1", "place_label": "place_label_1"}},
                            {"arg_bindings": {"pick_label": "pick_label_2", "place_label": "place_label_2"}},
                        ],
                    }
                }
            },
        }
        skill = {
            "schema_version": "1.0.0",
            "name": "bad_stack",
            "description": "bad",
            "args": {"arm_id": 0, "pick_label_1": "a", "place_label_1": "b"},
            "workflow": [
                {
                    "action": "pickplace/semantic_pickplace.yaml",
                    "args": {"arm_id": "= args.arm_id", "pick_label": "= args.pick_label_1", "place_label": "= args.place_label_1"},
                }
            ],
        }
        payload = {"skill_reuse_decision": {"candidate_role": "pure_wrapper_reuse"}}

        violations = validate_task_contract(skill=skill, task=task, payload=payload)

        self.assertTrue(any("requires at least 2" in item for item in violations))
        self.assertTrue(any("pick_label_2" in item for item in violations))
        self.assertTrue(any("reuse_existing_skill" in item for item in violations))

    def test_pickplace_template_validates_against_registry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "smoke",
                    "template": "pickplace_wrapper",
                    "args": {"arm_id": 0, "pick_label": "obj", "place_label": "table"},
                },
            )
            registry = fake_registry()
            generated = generate_skill_from_task(
                task_path=task,
                candidate_id="cand",
                output_dir=root / "candidates",
                registry=registry,
            )
            result = check_skill(generated.local_path, registry)
            self.assertTrue(result.ok, result.violations)

    def test_set_gripper_template_validates_against_registry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "gripper",
                    "template": "set_gripper",
                    "args": {"arm_id": 0, "angle": 60.0, "check": False},
                },
            )
            registry = fake_registry()
            generated = generate_skill_from_task(
                task_path=task,
                candidate_id="gripper",
                output_dir=root / "candidates",
                registry=registry,
            )
            result = check_skill(generated.local_path, registry)
            self.assertTrue(result.ok, result.violations)

    def test_package_candidate_writes_aspire_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = root / "skill.yaml"
            write_yaml(
                skill,
                {
                    "schema_version": "1.0.0",
                    "description": "candidate",
                    "args": {"arm_id": 0},
                    "workflow": [{"action": "/ctrl/go_home", "args": {"arm_id": "= args.arm_id"}}],
                },
            )
            package = package_skill_candidate(
                candidate_id="candidate one",
                skill_yaml=skill,
                output_root=root / "packages",
                registry=fake_registry(),
                hypothesis="test candidate package",
            )
            package_dir = Path(package.package_dir)
            self.assertEqual(package.candidate_id, "candidate_one")
            self.assertTrue((package_dir / "code.py").exists())
            self.assertTrue((package_dir / "static_report.json").exists())
            self.assertTrue(package.policy_ok)
            loaded = load_candidate_package(package_dir)
            self.assertEqual(loaded["candidate_id"], "candidate_one")

    def test_aspire_iteration_dry_run_writes_report_and_publish_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = fake_config_with_kw_tree(root)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "dry",
                    "template": "go_home",
                    "description": "dry run go home",
                    "args": {"arm_id": 0},
                },
            )
            result = run_aspire_iteration(
                config=config,
                task_path=task,
                candidate_id="dry_candidate",
                generator_mode="template",
                execute=False,
                publish=True,
            )
            run_dir = Path(result.run_dir)
            self.assertEqual(result.episode_report["failure_signature"], "dry_run")
            self.assertTrue((run_dir / "episode_report.json").exists())
            self.assertTrue((run_dir / "feedback_prompt.txt").exists())
            self.assertTrue((config.test_skill_abs_dir / "dry_candidate.yaml").exists())
            self.assertTrue((config.artifacts.runs_dir / "leaderboard.json").exists())

    def test_llm_generator_accepts_mock_response(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "llm_task",
                    "description": "use llm for go home",
                    "args": {"arm_id": 0},
                    "robodojo": {
                        "stage": {
                            "stage_id": "pick_bottle",
                            "skill_reuse_decision": {
                                "decision": "reuse_existing_skill",
                                "candidate_lifecycle": {"maintain_candidate": False},
                            },
                        },
                        "stage_feedback": {
                            "aspire_next_action": "iterate_reuse_binding_or_parameters",
                            "execution_feedback": {
                                "stage_status": "failed",
                                "success": False,
                                "failure_signature": "skill_execution_failed",
                                "failure_category": "skill_execution",
                            },
                        },
                    },
                },
            )
            response = {
                "candidate_id": "llm_candidate",
                "hypothesis": "go_home can be represented as direct KW control",
                "change_summary": "use /ctrl/go_home",
                "expected_failure_modes": ["controller failure"],
                "skill_reuse_decision": {
                    "decision": "reuse_existing_skill",
                    "selected_existing_skills": ["/ctrl/go_home"],
                    "rationale": "The registry has a direct home control action.",
                },
                "skill_args": {"arm_id": 0},
                "skill_yaml": """
schema_version: 1.0.0
name: llm_candidate
description: LLM generated go home candidate.
args:
  arm_id: 0
workflow:
  - action: /ctrl/go_home
    description: move selected arm home
    args:
      arm_id: = args.arm_id
""".strip(),
            }
            generated = generate_skill_from_task_llm(
                task_path=task,
                candidate_id="llm_candidate",
                output_dir=root / "generated",
                registry=fake_registry(),
                llm_config=fake_llm_config(),
                client=FakeChatClient(response),
            )
            result = check_skill(generated.local_path, fake_registry())
            self.assertEqual(generated.template, "llm")
            self.assertTrue(result.ok, result.violations)
            self.assertTrue(Path(generated.metadata["prompt_path"]).exists())
            self.assertEqual(generated.metadata["hypothesis"], response["hypothesis"])
            self.assertEqual(generated.metadata["skill_reuse_decision"]["decision"], "reuse_existing_skill")

    def test_llm_generator_wraps_workflow_list_response(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(task, {"task_id": "llm_list", "description": "go home"})
            response = {
                "candidate_id": "llm_list_candidate",
                "hypothesis": "workflow-list responses can still be normalized",
                "change_summary": "wrap list into a complete KW skill",
                "expected_failure_modes": [],
                "skill_args": {"arm_id": 0},
                "skill_yaml": """
- action: /ctrl/go_home
  args:
    arm_id: = args.arm_id
""".strip(),
            }

            generated = generate_skill_from_task_llm(
                task_path=task,
                candidate_id="llm_list_candidate",
                output_dir=root / "generated",
                registry=fake_registry(),
                llm_config=fake_llm_config(),
                client=FakeChatClient(response),
            )

            skill = Path(generated.local_path).read_text(encoding="utf-8")
            self.assertIn("name: llm_list_candidate", skill)
            self.assertIn("workflow:", skill)
            self.assertIn("arm_id: 0", skill)
            self.assertTrue(check_skill(generated.local_path, fake_registry()).ok)

    def test_llm_generator_accepts_prompt_override_for_aspire_suite_loop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "llm_task",
                    "description": "use llm for go home",
                    "args": {"arm_id": 0},
                    "robodojo": {
                        "stage": {
                            "stage_id": "pick_bottle",
                            "skill_reuse_decision": {
                                "decision": "reuse_existing_skill",
                                "candidate_lifecycle": {"maintain_candidate": False},
                            },
                        }
                    },
                },
            )
            response = {
                "candidate_id": "llm_candidate",
                "hypothesis": "go_home can be represented as direct KW control",
                "change_summary": "use /ctrl/go_home",
                "expected_failure_modes": ["controller failure"],
                "skill_args": {"arm_id": 0},
                "skill_yaml": """
schema_version: 1.0.0
name: llm_candidate
description: LLM generated go home candidate.
args:
  arm_id: 0
workflow:
  - action: /ctrl/go_home
    description: move selected arm home
    args:
      arm_id: = args.arm_id
""".strip(),
            }
            client = FakeChatClient(response)
            generated = generate_skill_from_task_llm(
                task_path=task,
                candidate_id="llm_candidate",
                output_dir=root / "generated",
                registry=fake_registry(),
                llm_config=fake_llm_config(),
                client=client,
                prompt_override="CUSTOM ASPIRE PROMPT WITH HISTORY",
            )
            self.assertTrue(generated.metadata["prompt_override"])
            self.assertEqual(generated.metadata["task_context"]["stage_id"], "pick_bottle")
            self.assertEqual(generated.metadata["task_context"]["skill_reuse_decision"]["decision"], "reuse_existing_skill")
            prompt = Path(generated.metadata["prompt_path"]).read_text(encoding="utf-8")
            self.assertEqual(prompt, "CUSTOM ASPIRE PROMPT WITH HISTORY")
            self.assertIn("CUSTOM ASPIRE PROMPT WITH HISTORY", client.messages[0][1]["content"])
            package = _package_generated_candidate(
                generated=generated,
                output_root=root / "packages",
                registry=fake_registry(),
                parent_id="seed",
            )
            manifest = load_candidate_package(package.package_dir)["manifest"]
            self.assertEqual(manifest["metadata"]["stage_id"], "pick_bottle")
            self.assertEqual(manifest["metadata"]["skill_reuse_decision"]["decision"], "reuse_existing_skill")
            self.assertTrue(manifest["metadata"]["runtime_wrapper_only"])

    def test_default_llm_prompt_includes_stage_reuse_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "pick_stage",
                    "args": {"arm_id": 0, "pick_label": "bottle:dof"},
                    "robodojo": {
                        "stage": {
                            "stage_id": "pick_bottle",
                            "skill_reuse_decision": {
                                "decision": "reuse_existing_skill",
                                "selected_existing_skills": ["pickplace/semantic_pick.yaml"],
                                "related_existing_skills": ["pickplace/pick_verifier.yaml"],
                                "candidate_lifecycle": {"maintain_candidate": False},
                            },
                        },
                        "stage_feedback": {
                            "aspire_next_action": "iterate_reuse_binding_or_parameters",
                            "execution_feedback": {
                                "stage_status": "failed",
                                "success": False,
                                "failure_signature": "skill_execution_failed",
                                "failure_category": "skill_execution",
                            },
                        },
                    },
                },
            )
            response = {
                "candidate_id": "llm_candidate",
                "hypothesis": "reuse pick",
                "change_summary": "reuse pick",
                "expected_failure_modes": ["pick verifier failed"],
                "skill_args": {"arm_id": 0, "pick_label": "bottle:dof"},
                "skill_yaml": """
schema_version: 1.0.0
name: llm_candidate
description: Reuse semantic pick.
args:
  arm_id: 0
  pick_label: bottle:dof
workflow:
  - action: pickplace/semantic_pick.yaml
    output: pick_success
    args:
      arm_id: = args.arm_id
      pick_label: = args.pick_label
  - assert: = pick_success
    message: Pick failed
""".strip(),
            }
            generated = generate_skill_from_task_llm(
                task_path=task,
                candidate_id="llm_candidate",
                output_dir=root / "generated",
                registry=fake_registry_with_semantic_pick(),
                llm_config=fake_llm_config(),
                client=FakeChatClient(response),
            )
            prompt = Path(generated.metadata["prompt_path"]).read_text(encoding="utf-8")
            self.assertIn("Stage-level ASPIRE contract", prompt)
            self.assertIn("Skill decision: reuse_existing_skill", prompt)
            self.assertIn("Output contract: do_not_create_new_skill_candidate", prompt)
            self.assertIn("Do not propose, name, or imply a new reusable robot skill", prompt)
            self.assertIn("Keep verifier-gated semantics intact", prompt)
            self.assertIn("Previous stage feedback", prompt)
            self.assertIn("Failure signature: skill_execution_failed", prompt)
            self.assertIn("ASPIRE next action: iterate_reuse_binding_or_parameters", prompt)

    def test_policy_rejects_undeclared_subskill_arg(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = root / "skill.yaml"
            write_yaml(
                skill,
                {
                    "schema_version": "1.0.0",
                    "description": "bad undeclared arg",
                    "args": {"arm_id": 0},
                    "workflow": [
                        {
                            "action": "pickplace/semantic_pickplace.yaml",
                            "args": {"arm_id": "= args.arm_id", "planner_config": []},
                        }
                    ],
                },
            )
            registry = ToolRegistry(
                k1_dir="/tmp/k1",
                test_skill_dir="knowin_skills/knowin_skill_manager_tests",
                ctrl=["go_home"],
                info=[],
                reasoning=[],
                namespaces=["head", "left_hand", "right_hand"],
                skills=[
                    SkillSummary(
                        path="pickplace/semantic_pickplace.yaml",
                        description="pickplace",
                        args={"arm_id": 0, "pick_label": "", "place_label": ""},
                        actions=[],
                    )
                ],
            )
            result = check_skill(skill, registry)
            self.assertFalse(result.ok)
            self.assertIn("action arg 'planner_config' is not declared by subskill 'pickplace/semantic_pickplace.yaml'", result.violations)

    def test_policy_rejects_structured_arg_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = root / "skill.yaml"
            write_yaml(
                skill,
                {
                    "schema_version": "1.0.0",
                    "description": "bad structured arg",
                    "args": {"gripper": {"open_angle": 80, "close_angle": 0}},
                    "workflow": [
                        {
                            "action": "pickplace/semantic_pickplace.yaml",
                            "args": {"gripper": "= args.gripper"},
                        }
                    ],
                },
            )
            result = check_skill(skill, fake_registry())
            self.assertFalse(result.ok)
            self.assertIn("action arg 'gripper' must not pass structured top-level arg 'gripper'", result.violations)

    def test_openai_compatible_client_accepts_non_gpt_model_name(self) -> None:
        client = GPTChatClient(
            ResolvedLLMConfig(
                provider="openai",
                base_url="http://example.invalid/v1",
                api_key="test-key",
                model="Qwen3.5-397B-A17B",
                auth_mode="bearer",
                temperature=1.0,
                max_tokens=128,
                timeout_s=1.0,
            )
        )
        self.assertEqual(client.settings.model, "Qwen3.5-397B-A17B")

    def test_runtime_args_use_candidate_iteration_args_over_task_defaults(self) -> None:
        task = SuiteTask(
            task_id="task",
            task_path="/tmp/task.yaml",
            description="task",
            skill_args={"arm_id": 0, "pick_check_offset": [0.0, 0.0, 0.07], "delay_sec": 0.8},
            predicates=[],
            reset_layout=True,
            metadata={},
        )
        candidate = SuiteCandidateRef(
            candidate_id="candidate",
            package_dir="/tmp/pkg",
            skill_path="/tmp/pkg/skill.yaml",
            manifest_path="/tmp/pkg/candidate_manifest.json",
            manifest={"skill_args": {"pick_check_offset": [0.0, 0.0, 0.1], "delay_sec": 1.0}},
        )
        self.assertEqual(
            runtime_skill_args(task=task, candidate=candidate),
            {"arm_id": 0, "pick_check_offset": [0.0, 0.0, 0.1], "delay_sec": 1.0},
        )

    def test_aspire_suite_prompt_reads_registry_capability_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_path = root / "task.yaml"
            write_yaml(task_path, {"task_id": "task", "description": "go home", "args": {"arm_id": 0}})
            suite = SuiteSpec(
                suite_id="suite",
                description="suite",
                manifest_path=str(root / "suite.yaml"),
                output_root=str(root / "runs"),
                publish_subdir="knowin_skills/knowin_skill_manager_tests",
                success_threshold=1.0,
                tasks=[
                    SuiteTask(
                        task_id="task",
                        task_path=str(task_path),
                        description="go home",
                        skill_args={"arm_id": 0},
                        predicates=[],
                        reset_layout=True,
                        metadata={},
                    )
                ],
                candidates=[],
            )
            prompt = build_aspire_prompt(
                suite=suite,
                target_task_path=task_path,
                candidate_id="cand",
                registry=fake_registry(),
                history={},
                skill_context={},
                generation_index=1,
                candidate_index=1,
                population_size=1,
            )
            self.assertIn('"capabilities"', prompt)
            self.assertIn('"is_composite"', prompt)
            self.assertIn("Stage-level ASPIRE contract", prompt)
            self.assertIn("reuse_existing_skill", prompt)
            self.assertIn("skill_specialization", prompt)
            self.assertIn("new_behavior_skill", prompt)

    def test_aspire_prompt_omits_evaluator_feedback_from_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_path = root / "task.yaml"
            write_yaml(task_path, {"task_id": "task", "description": "pick and place", "args": {"arm_id": 0}})
            suite = SuiteSpec(
                suite_id="suite",
                description="suite",
                manifest_path=str(root / "suite.yaml"),
                output_root=str(root / "runs"),
                publish_subdir="knowin_skills/knowin_skill_manager_tests",
                success_threshold=1.0,
                tasks=[
                    SuiteTask(
                        task_id="task",
                        task_path=str(task_path),
                        description="pick and place",
                        skill_args={"arm_id": 0},
                        predicates=[],
                        reset_layout=True,
                        metadata={},
                    )
                ],
                candidates=[],
            )
            history = {
                "leaderboard": {
                    "success_rate": 0.0,
                    "candidates": [
                        {
                            "candidate_id": "cand_prev",
                            "predicate_success": False,
                            "predicate_report": {"predicates": [{"name": "inside", "success": False}]},
                            "agent_feedback": {
                                "observable_status": "pipeline_failed",
                                "first_failed_action": "pickplace/semantic_pick.yaml",
                            },
                        }
                    ],
                },
                "evaluation_summary": {"predicate_success_rate": 0.0},
                "feedback_memory": {
                    "candidate_states": [
                        {
                            "candidate_id": "cand_prev",
                            "predicate_success": False,
                            "agent_feedback": {
                                "observable_status": "pipeline_failed",
                                "first_failed_action": "pickplace/semantic_pick.yaml",
                            },
                        }
                    ]
                },
            }
            skill_context = {
                "selected": [
                    {
                        "path": "/tmp/evidence.json",
                        "snippet": '{"predicate_success": false, "predicate_report": {"predicates": [{"name": "inside"}]}}',
                    }
                ]
            }

            prompt = build_aspire_prompt(
                suite=suite,
                target_task_path=task_path,
                candidate_id="cand",
                registry=fake_registry(),
                history=history,
                skill_context=skill_context,
                generation_index=1,
                candidate_index=1,
                population_size=1,
            )

            self.assertIn("ASPIRE agent-observable history", prompt)
            self.assertIn("pickplace/semantic_pick.yaml", prompt)
            self.assertNotIn("predicate_success", prompt)
            self.assertNotIn("predicate_report", prompt)
            self.assertNotIn('"inside"', prompt)

    def test_aspire_prompt_includes_agent_new_skill_spec_without_evaluator_leak(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_path = root / "task.yaml"
            write_yaml(
                task_path,
                {
                    "task_id": "stack_task",
                    "description": "stack blocks",
                    "args": {"arm_id": 0},
                    "agent_controller": {"new_skill_spec": stack_new_skill_spec()},
                },
            )
            suite = SuiteSpec(
                suite_id="suite",
                description="suite",
                manifest_path=str(root / "suite.yaml"),
                output_root=str(root / "runs"),
                publish_subdir="knowin_skills/knowin_skill_manager_tests",
                success_threshold=1.0,
                tasks=[
                    SuiteTask(
                        task_id="stack_task",
                        task_path=str(task_path),
                        description="stack blocks",
                        skill_args={"arm_id": 0},
                        predicates=[],
                        reset_layout=True,
                        metadata={},
                    )
                ],
                candidates=[],
            )
            prompt = build_aspire_prompt(
                suite=suite,
                target_task_path=task_path,
                candidate_id="stack_step_candidate",
                registry=fake_registry_with_stack_tools(),
                history={},
                skill_context={},
                generation_index=1,
                candidate_index=1,
                population_size=1,
            )

            self.assertIn("Agent controller new-skill spec", prompt)
            self.assertIn("stack_step", prompt)
            self.assertIn("candidate_intent", prompt)
            self.assertNotIn("predicate_report", prompt)
            self.assertNotIn("predicate_success", prompt)
            self.assertNotIn("min_xy_overlap", prompt)
            self.assertNotIn("z_gap", prompt)

    def test_new_skill_spec_rejects_pure_semantic_pickplace_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "stack_task",
                    "description": "stack blocks",
                    "args": {"arm_id": 0, "source_label": "orange block:dof", "support_label": "white block:dof"},
                    "agent_controller": {"new_skill_spec": stack_new_skill_spec()},
                },
            )
            response = {
                "candidate_id": "bad_stack_wrapper",
                "hypothesis": "bad wrapper",
                "change_summary": "only calls semantic_pickplace",
                "expected_failure_modes": ["same wrapper failure"],
                "skill_reuse_decision": {
                    "decision": "reuse_existing_skill",
                    "candidate_role": "reuse_existing_skill",
                    "selected_existing_skills": ["pickplace/semantic_pickplace.yaml"],
                    "rationale": "generic wrapper",
                },
                "skill_args": {"arm_id": 0, "source_label": "orange block:dof", "support_label": "white block:dof"},
                "skill_yaml": """
schema_version: 1.0.0
name: bad_stack_wrapper
description: Bad wrapper.
args:
  arm_id: 0
  source_label: orange block:dof
  support_label: white block:dof
workflow:
  - action: pickplace/semantic_pickplace.yaml
    args:
      arm_id: = args.arm_id
      pick_label: = args.source_label
      place_label: = args.support_label
""".strip(),
            }

            with self.assertRaisesRegex(RuntimeError, "reuse_existing_skill|skill_specialization|new_behavior_skill"):
                generate_skill_from_task_llm(
                    task_path=task,
                    candidate_id="bad_stack_wrapper",
                    output_dir=root / "generated",
                    registry=fake_registry_with_stack_tools(),
                    llm_config=fake_llm_config(),
                    client=FakeChatClient(response),
                    max_attempts=1,
                )

    def test_global_role_contract_rejects_new_behavior_that_only_wraps_selected_skills(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "stack_task",
                    "description": "stack blocks",
                    "args": {"arm_id": 0, "pick_label_1": "orange block:dof", "place_label_1": "white block:dof"},
                },
            )
            response = {
                "candidate_id": "misclassified_new_behavior",
                "hypothesis": "bad role classification",
                "change_summary": "only calls selected existing skill",
                "expected_failure_modes": ["same wrapper failure"],
                "skill_reuse_decision": {
                    "decision": "composition_skill_candidate",
                    "candidate_role": "new_behavior_skill",
                    "reusable_interface": {
                        "name": "bad_stack_step",
                        "args": {"arm_id": "int", "pick_label_1": "string", "place_label_1": "string"},
                        "expected_effects": ["object moved"],
                        "observable_success": ["action completes"],
                        "failure_modes": ["wrapper failure"],
                    },
                    "added_behavior_contract": {
                        "constraints": ["sequence one selected skill"],
                    },
                    "selected_existing_skills": ["pickplace/semantic_pickplace.yaml"],
                    "rationale": "incorrectly claims new behavior",
                },
                "skill_args": {"arm_id": 0, "pick_label_1": "orange block:dof", "place_label_1": "white block:dof"},
                "skill_yaml": """
schema_version: 1.0.0
name: misclassified_new_behavior
description: Badly classified wrapper.
args:
  arm_id: 0
  pick_label_1: orange block:dof
  place_label_1: white block:dof
workflow:
  - action: pickplace/semantic_pickplace.yaml
    args:
      arm_id: = args.arm_id
      pick_label: = args.pick_label_1
      place_label: = args.place_label_1
""".strip(),
            }

            with self.assertRaisesRegex(RuntimeError, "new_behavior_skill must introduce a new mechanism"):
                generate_skill_from_task_llm(
                    task_path=task,
                    candidate_id="misclassified_new_behavior",
                    output_dir=root / "generated",
                    registry=fake_registry_with_stack_tools(),
                    llm_config=fake_llm_config(),
                    client=FakeChatClient(response),
                    max_attempts=1,
                )

    def test_new_skill_spec_accepts_stack_step_style_candidate_and_packages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            write_yaml(
                task,
                {
                    "task_id": "stack_task",
                    "description": "stack blocks",
                    "args": {"arm_id": 0, "source_label": "orange block:dof", "support_label": "white block:dof"},
                    "agent_controller": {"new_skill_spec": stack_new_skill_spec()},
                },
            )
            response = {
                "candidate_id": "stack_step_candidate",
                "hypothesis": "stack step needs explicit pick/place boundary and retreat",
                "change_summary": "propose stack_step rather than pure pickplace wrapper",
                "expected_failure_modes": ["support relocalization fails", "release alignment unstable"],
                "skill_reuse_decision": {
                    "decision": "composition_skill_candidate",
                    "candidate_role": "skill_specialization",
                    "reusable_interface": {
                        "name": "stack_step",
                        "args": {"source_label": "string", "support_label": "string", "arm_id": "int"},
                        "expected_effects": ["source released on support"],
                        "observable_success": ["source remains on support after retreat"],
                        "failure_modes": ["alignment or release instability"],
                    },
                    "added_behavior_contract": {
                        "constraints": ["re-localize support before place", "retreat after release"],
                        "observable_success": ["source remains on support after retreat"],
                    },
                    "selected_existing_skills": ["pickplace/semantic_pick.yaml", "pickplace/semantic_place.yaml"],
                    "rationale": "reuses acquisition but exposes stack behavior boundary",
                },
                "skill_args": {"arm_id": 0, "source_label": "orange block:dof", "support_label": "white block:dof"},
                "skill_yaml": """
schema_version: 1.0.0
name: stack_step_candidate
description: Reusable stack step candidate.
args:
  arm_id: 0
  source_label: orange block:dof
  support_label: white block:dof
workflow:
  - action: pickplace/semantic_pick.yaml
    args:
      arm_id: = args.arm_id
      pick_label: = args.source_label
  - action: pickplace/semantic_place.yaml
    args:
      arm_id: = args.arm_id
      place_label: = args.support_label
  - action: /ctrl/go_home
    args:
      arm_id: = args.arm_id
""".strip(),
            }
            generated = generate_skill_from_task_llm(
                task_path=task,
                candidate_id="stack_step_candidate",
                output_dir=root / "generated",
                registry=fake_registry_with_stack_tools(),
                llm_config=fake_llm_config(),
                client=FakeChatClient(response),
            )
            package = _package_generated_candidate(
                generated=generated,
                output_root=root / "packages",
                registry=fake_registry_with_stack_tools(),
                parent_id=None,
            )
            manifest = load_candidate_package(package.package_dir)["manifest"]

            self.assertTrue(package.policy_ok)
            self.assertEqual(manifest["metadata"]["llm_skill_reuse_decision"]["candidate_role"], "skill_specialization")
            self.assertFalse(manifest["metadata"].get("runtime_wrapper_only"))

    def test_new_skill_spec_allows_semantic_context_args(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = root / "task.yaml"
            spec = {
                "schema": "ksm.agent_controller.new_skill_spec.v1",
                "status": "proposed",
                "candidate_role": "skill_specialization",
                "target_skill_family": "solid_object_pouring",
                "candidate_intent": "Specialize existing pouring for solid contents.",
                "reusable_interface": {
                    "name": "pour_solid_objects_from_container",
                    "args": [
                        "arm_id",
                        "source_container_label",
                        "target_container_label",
                        "content_label",
                        "task_prompt",
                    ],
                    "semantic_context_args": ["content_label"],
                    "expected_effects": ["contents leave source and enter target"],
                    "observable_success": ["contents visible in target"],
                    "failure_modes": ["source grasp failed", "pour misses target"],
                },
                "reuse_policy": {
                    "selected_existing_skills": ["pour_water/pour_water.yaml"],
                    "must_add_behavior_contract": ["solid-content pouring semantics"],
                },
            }
            write_yaml(
                task,
                {
                    "task_id": "pour_balls",
                    "description": "pour balls from cup into vase",
                    "args": {
                        "arm_id": 0,
                        "source_container_label": "cup:dof",
                        "target_container_label": "vase:dof",
                        "content_label": "balls",
                        "task_prompt": "Pour all the balls from the cup into the vase.",
                    },
                    "agent_controller": {"new_skill_spec": spec},
                },
            )
            response = {
                "candidate_id": "pour_balls_candidate",
                "hypothesis": "existing pour skill can be specialized for solid objects",
                "change_summary": "call pour_water with non-liquid tilt and task-family interface",
                "expected_failure_modes": ["solid objects remain in source"],
                "skill_reuse_decision": {
                    "decision": "composition_skill_candidate",
                    "candidate_role": "skill_specialization",
                    "reusable_interface": spec["reusable_interface"],
                    "added_behavior_contract": {
                        "constraints": ["use non_liquid_local_y_pour_rad for solid contents"],
                    },
                    "selected_existing_skills": ["pour_water/pour_water.yaml"],
                },
                "skill_args": {
                    "arm_id": 0,
                    "source_container_label": "cup:dof",
                    "target_container_label": "vase:dof",
                    "content_label": "balls",
                    "task_prompt": "Pour all the balls from the cup into the vase.",
                },
                "skill_yaml": """
schema_version: 1.0.0
name: pour_balls_candidate
description: Solid-object pouring specialization.
args:
  arm_id: 0
  source_container_label: cup:dof
  target_container_label: vase:dof
  content_label: balls
  task_prompt: Pour all the balls from the cup into the vase.
workflow:
  - action: pour_water/pour_water.yaml
    args:
      arm_id: = args.arm_id
      prompt: = args.task_prompt
      bottle_prompt: = args.source_container_label
      cup_prompt: = args.target_container_label
      non_liquid_local_y_pour_rad: 2.1
""".strip(),
            }
            generated = generate_skill_from_task_llm(
                task_path=task,
                candidate_id="pour_balls_candidate",
                output_dir=root / "generated",
                registry=fake_registry_with_pour_tools(),
                llm_config=fake_llm_config(),
                client=FakeChatClient(response),
                max_attempts=1,
            )

            self.assertEqual(generated.candidate_id, "pour_balls_candidate")

    def test_robodojo_full_task_prompt_does_not_force_tier4_static_parameters(self) -> None:
        selected = RobodojoPoolItem(
            task_id="robodojo_put_bottles_into_dustbin_029_bottle3_prop_to_dustbin_prop",
            task_class="put_bottles_into_dustbin",
            prompt="Pick up the bottle and throw it into the dustbin.",
            tags=[],
            suite_path="/tmp/suite.yaml",
            scene_path="/tmp/scene.yaml",
            target_asset={"id": "bottle3_prop", "category": "bottle", "qualified": True},
            target_import={},
            success={"all_of": [{"type": "inside", "object": "bottle3_prop", "container": "dustbin_prop"}]},
            admission={"accepted": True, "risk_notes": []},
            binding={
                "primary_pick_label": "蓝白瓶子:dof",
                "primary_place_label": "垃圾桶",
                "candidate_pick_labels": ["蓝白瓶子:dof", "瓶子:dof"],
                "candidate_place_labels": ["垃圾桶", "dustbin"],
            },
            score=1.0,
            tier=4,
            place_asset={"id": "dustbin_prop", "category": "dustbin", "qualified": True},
            place_import={},
            subtask={"source_object": "bottle3_prop", "target_container": "dustbin_prop"},
        )

        prompt = build_robodojo_prompt(selected=selected, candidate_id="cand", registry=fake_registry())

        self.assertIn("skill_reuse_decision", prompt)
        self.assertIn("Full-task object-to-container context", prompt)
        self.assertIn("Do not decompose into a fixed staged guide", prompt)
        self.assertNotIn("set direct_pick: true", prompt)
        self.assertNotIn("adjust_arm_id: false", prompt)
        self.assertNotIn("pass direct_pick", prompt)
        self.assertNotIn("pass adjust_arm_id", prompt)

    def test_robodojo_full_task_prompt_isolates_experiment_history(self) -> None:
        selected = RobodojoPoolItem(
            task_id="robodojo_put_bottles_into_dustbin_029_bottle3_prop_to_dustbin_prop",
            task_class="put_bottles_into_dustbin",
            prompt="put the selected bottle into the dustbin",
            tags=["robodojo", "pickplace"],
            suite_path="/tmp/suite.yaml",
            scene_path="/tmp/scene.yaml",
            target_asset={"id": "bottle3_prop", "category": "bottle", "qualified": True},
            target_import={},
            success={"all_of": [{"type": "inside", "object": "bottle3_prop", "container": "dustbin_prop"}]},
            admission={"accepted": True, "risk_notes": []},
            binding={
                "primary_pick_label": "蓝白瓶子:dof",
                "primary_place_label": "垃圾桶",
                "candidate_pick_labels": ["蓝白瓶子:dof", "瓶子:dof"],
                "candidate_place_labels": ["垃圾桶", "dustbin"],
            },
            score=1.0,
            tier=4,
            place_asset={"id": "dustbin_prop", "category": "dustbin", "qualified": True},
            place_import={},
            subtask={"source_object": "bottle3_prop", "target_container": "dustbin_prop"},
        )
        registry = ToolRegistry(
            k1_dir="/tmp/k1",
            test_skill_dir="knowin_skills/knowin_skill_manager_tests",
            ctrl=["go_home", "set_gripper"],
            info=["get_qpos"],
            reasoning=["identity", "semantic_xquat"],
            namespaces=["head", "left_hand", "right_hand"],
            skills=[
                SkillSummary(
                    path="pickplace/semantic_pickplace.yaml",
                    description="stable pickplace",
                    args={"arm_id": 0, "pick_label": "", "place_label": "", "direct_pick": True},
                    actions=[],
                ),
                SkillSummary(
                    path="knowin_skill_manager_tests/old_aspire/candidate.yaml",
                    description="old contaminated candidate with direct_pick and arm_id",
                    args={"arm_id": 0, "direct_pick": True, "adjust_arm_id": False},
                    actions=["pickplace/semantic_pickplace.yaml"],
                ),
            ],
        )

        prompt = build_robodojo_prompt(selected=selected, candidate_id="cand", registry=registry)

        self.assertIn("history_isolation", prompt)
        self.assertIn("pickplace/semantic_pickplace.yaml", prompt)
        self.assertNotIn("old contaminated candidate", prompt)
        self.assertNotIn("knowin_skill_manager_tests/old_aspire/candidate.yaml", prompt)
        self.assertNotIn("direct_pick", prompt)
        self.assertNotIn("adjust_arm_id", prompt)
        self.assertNotIn('"direct_pick"', prompt)
        self.assertNotIn('"adjust_arm_id"', prompt)
        self.assertIn('"arm_id": 0', prompt)


def fake_registry() -> ToolRegistry:
    return ToolRegistry(
        k1_dir="/tmp/k1",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        ctrl=["go_home", "set_gripper"],
        info=["get_qpos"],
        reasoning=["identity", "semantic_xquat"],
        namespaces=["head", "left_hand", "right_hand"],
        skills=[
            SkillSummary(
                path="pickplace/semantic_pickplace.yaml",
                description="pickplace",
                args={},
                actions=[],
            )
        ],
    )


def fake_registry_with_stack_tools() -> ToolRegistry:
    return ToolRegistry(
        k1_dir="/tmp/k1",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        ctrl=["go_home", "set_gripper"],
        info=["get_qpos"],
        reasoning=["identity", "semantic_xquat"],
        namespaces=["head", "left_hand", "right_hand"],
        skills=[
            SkillSummary(
                path="pickplace/semantic_pickplace.yaml",
                description="generic semantic pick and place",
                args={"arm_id": 0, "pick_label": "", "place_label": ""},
                actions=["pickplace/semantic_pick.yaml", "pickplace/semantic_place.yaml"],
            ),
            SkillSummary(
                path="pickplace/semantic_pick.yaml",
                description="semantic pick",
                args={"arm_id": 0, "pick_label": ""},
                actions=[],
            ),
            SkillSummary(
                path="pickplace/semantic_place.yaml",
                description="semantic place",
                args={"arm_id": 0, "place_label": ""},
                actions=[],
            ),
        ],
    )


def fake_registry_with_pour_tools() -> ToolRegistry:
    return ToolRegistry(
        k1_dir="/tmp/k1",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        ctrl=["go_home", "set_gripper"],
        info=["get_qpos"],
        reasoning=["identity", "semantic_xquat"],
        namespaces=["head", "left_hand", "right_hand"],
        skills=[
            SkillSummary(
                path="pour_water/pour_water.yaml",
                description="generic pouring skill",
                args={
                    "arm_id": 0,
                    "prompt": "",
                    "bottle_prompt": "",
                    "cup_prompt": "",
                    "non_liquid_local_y_pour_rad": 2.1,
                },
                actions=[],
            )
        ],
    )


def stack_new_skill_spec() -> dict[str, Any]:
    return {
        "schema": "ksm.agent_controller.new_skill_spec.v1",
        "status": "proposed",
        "strategy_judgment": {
            "strategy": "new_skill",
            "confidence": "medium",
            "reason": "agent-observable history requests a reusable behavior boundary",
        },
        "target_skill_family": "stacking",
        "candidate_role": "skill_specialization",
        "candidate_intent": [
            "reuse stable acquisition when useful",
            "re-localize support before placement",
            "place onto the support top surface with alignment-aware release",
            "retreat without disturbing the object",
        ],
        "reusable_interface": {
            "name": "stack_step",
            "args": {
                "source_label": "object to place",
                "support_label": "support object",
                "arm_id": "optional arm selector",
            },
            "expected_effects": ["source object is released on the support object"],
            "observable_success": ["source remains near/on support after retreat"],
            "failure_modes": ["support not re-localized", "release alignment poor"],
        },
        "reuse_policy": {
            "selected_existing_skills": ["pickplace/semantic_pickplace.yaml"],
            "must_add_behavior_contract": ["alignment-aware placement", "observable stack stability"],
        },
        "not_allowed": [
            "Do not pass evaluator-only predicate reports or geometry measurements into the generator.",
            "Do not report skill_specialization unless it adds a named behavior contract beyond forwarding args.",
        ],
        "agent_prompt_context": {
            "schema": "ksm.agent_controller.agent_prompt_context.v1",
            "agent_observable_summary": {"observable_status_counts": {"pipeline_completed_with_visual_progress": 2}},
            "predicate_report": {"this": "must be stripped before prompt"},
        },
    }


def fake_registry_with_semantic_pick() -> ToolRegistry:
    registry = fake_registry()
    return ToolRegistry(
        k1_dir=registry.k1_dir,
        test_skill_dir=registry.test_skill_dir,
        ctrl=registry.ctrl,
        info=registry.info,
        reasoning=registry.reasoning,
        namespaces=registry.namespaces,
        skills=[
            *registry.skills,
            SkillSummary(
                path="pickplace/semantic_pick.yaml",
                description="semantic pick",
                args={},
                actions=["pickplace/pick_verifier.yaml"],
            ),
            SkillSummary(
                path="pickplace/pick_verifier.yaml",
                description="pick verifier",
                args={},
                actions=[],
            ),
        ],
    )


def fake_config(root: Path) -> ManagerConfig:
    return ManagerConfig(
        root_dir=root,
        kw_repo=root / "knowin-world",
        k1_dir=root / "knowin-world" / "sim" / "sys" / "k1-sys-v0",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        pipeline=PipelineConfig("direct", "http://127.0.0.1:8000", 0.5, 1.0),
        artifacts=ArtifactConfig(root / "candidates", root / "runs"),
        llm=fake_llm_config(),
    )


def fake_config_with_kw_tree(root: Path) -> ManagerConfig:
    kw_repo = root / "knowin-world"
    k1_dir = kw_repo / "sim" / "sys" / "k1-sys-v0"
    skill_dir = k1_dir / "knowin_skills" / "pickplace"
    skill_dir.mkdir(parents=True)
    write_yaml(
        skill_dir / "semantic_pickplace.yaml",
        {
            "schema_version": "1.0.0",
            "description": "fake semantic pickplace",
            "args": {},
            "workflow": [{"assert": "= True", "message": "fake"}],
        },
    )
    return ManagerConfig(
        root_dir=root,
        kw_repo=kw_repo,
        k1_dir=k1_dir,
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        pipeline=PipelineConfig("direct", "http://127.0.0.1:8000", 0.5, 1.0),
        artifacts=ArtifactConfig(root / "candidates", root / "runs"),
        llm=fake_llm_config(),
    )


def fake_llm_config() -> LLMConfig:
    return LLMConfig(
        provider="openai",
        base_url="http://example.invalid/v1/chat/completions",
        base_url_env="",
        api_key_env="",
        model="gpt-5.5",
        model_env="",
        auth_mode="bearer",
        env_file=None,
        temperature=1.0,
        max_tokens=1024,
        timeout_s=1.0,
    )


class FakeChatClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.messages: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]]) -> LLMResponse:
        self.messages.append(messages)
        return LLMResponse(provider="fake", model="fake", text=json_dumps(self.payload), raw={"fake": True})


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
