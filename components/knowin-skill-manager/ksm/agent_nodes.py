from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent_state import (
    AgentDecision,
    ExperimentState,
    assert_agent_context_safe,
    decide_next_action,
    load_experiment_state_from_roots,
)
from .agent_skill_spec import propose_new_skill_spec


NODE_RESULT_SCHEMA = "ksm.agent_controller.node_result.v1"


@dataclass(frozen=True)
class NodeSpec:
    name: str
    purpose: str
    side_effects: str
    inputs: list[str]
    outputs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NodeResult:
    node: str
    status: str
    output: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": NODE_RESULT_SCHEMA,
            **asdict(self),
        }


def default_node_catalog() -> list[NodeSpec]:
    return [
        NodeSpec(
            name="inspect_registry",
            purpose="Scan KW/KSM skill registry and expose reusable capability summaries.",
            side_effects="read_only",
            inputs=["ManagerConfig"],
            outputs=["registry_summary"],
        ),
        NodeSpec(
            name="summarize_experiment_history",
            purpose="Build ExperimentState from prior episode/suite reports.",
            side_effects="read_only",
            inputs=["history_roots", "objective"],
            outputs=["ExperimentState"],
        ),
        NodeSpec(
            name="plan_next_action",
            purpose="Choose the next skill route from ExperimentState.",
            side_effects="read_only",
            inputs=["ExperimentState"],
            outputs=["AgentDecision"],
        ),
        NodeSpec(
            name="generate_candidate",
            purpose="Generate one YAML candidate from task, registry, and agent-safe context.",
            side_effects="writes_candidate_artifacts",
            inputs=["task_spec", "registry_summary", "agent_prompt_context"],
            outputs=["candidate_package"],
        ),
        NodeSpec(
            name="propose_new_skill_spec",
            purpose="Propose a reusable new-skill boundary before generating YAML.",
            side_effects="read_only",
            inputs=["ExperimentState", "AgentDecision"],
            outputs=["new_skill_spec"],
        ),
        NodeSpec(
            name="run_suite",
            purpose="Publish and execute candidate packages through the existing suite runner.",
            side_effects="publishes_and_runs_kw_skill",
            inputs=["suite_spec", "candidate_packages"],
            outputs=["suite_report", "episode_reports"],
        ),
        NodeSpec(
            name="run_aspire_iteration",
            purpose="Run ASPIRE as an internal optimizer node, not as the outer controller.",
            side_effects="writes_candidates_and_optional_execution_artifacts",
            inputs=["suite_spec", "agent_prompt_context", "candidate_seed"],
            outputs=["generation_manifest", "suite_report"],
        ),
        NodeSpec(
            name="request_feedback_enrichment",
            purpose="Ask the ASPIRE/evaluator feedback layer to enrich agent-observable trace or visual feedback.",
            side_effects="delegates_to_feedback_pipeline",
            inputs=["episode_dir", "task_goal", "candidate_id", "requested_feedback_types"],
            outputs=["feedback_enrichment_request", "agent_feedback_sidecar"],
        ),
        NodeSpec(
            name="stop_and_report",
            purpose="Stop search and preserve the successful or blocked state.",
            side_effects="writes_report",
            inputs=["ExperimentState", "AgentDecision"],
            outputs=["final_report"],
        ),
    ]


def node_catalog_payload() -> list[dict[str, Any]]:
    return [node.to_dict() for node in default_node_catalog()]


def summarize_experiment_history_node(
    *,
    objective: str,
    history_roots: list[str | Path],
) -> NodeResult:
    state = load_experiment_state_from_roots(objective=objective, history_roots=history_roots)
    return NodeResult(
        node="summarize_experiment_history",
        status="ok",
        output={"state": state.to_controller_dict()},
    )


def plan_next_action_node(state: ExperimentState) -> NodeResult:
    decision: AgentDecision = decide_next_action(state)
    assert_agent_context_safe(decision.agent_prompt_context)
    return NodeResult(
        node="plan_next_action",
        status="ok",
        output={"decision": decision.to_dict()},
    )


def propose_new_skill_spec_node(*, state: ExperimentState, decision: AgentDecision) -> NodeResult:
    assert_agent_context_safe(decision.agent_prompt_context)
    spec = propose_new_skill_spec(state=state, decision=decision)
    return NodeResult(
        node="propose_new_skill_spec",
        status=str(spec.get("status") or "unknown"),
        output={"new_skill_spec": spec},
    )
