from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .feedback_attribution import agent_safe_payload
from .feedback_attribution import ATTRIBUTION_VERSION, analyze_episode_report
from .io import read_json


AGENT_STATE_SCHEMA = "ksm.agent_controller.experiment_state.v1"
AGENT_DECISION_SCHEMA = "ksm.agent_controller.decision.v1"

ROUTE_REUSE = "reuse_existing_skill"
ROUTE_ADJUST = "adjust_binding_or_parameters"
ROUTE_WRAP = "wrap_existing_skill"
ROUTE_COMPOSE = "compose_existing_skills"
ROUTE_NEW_SKILL = "new_skill_candidate"
ROUTE_GAP = "primitive_gap"
ROUTE_OBSERVE = "need_more_observation"
ROUTE_STOP_SUCCESS = "stop_success"

STRATEGY_REUSE = "reuse"
STRATEGY_ITERATE_REUSE = "iterate_reuse"
STRATEGY_NEW_SKILL = "new_skill"
STRATEGY_NEED_OBSERVATION = "need_observation"
STRATEGY_STOP = "stop"


@dataclass(frozen=True)
class CandidateObservation:
    candidate_id: str
    task_id: str
    episode_dir: str
    skill_family: str
    candidate_role: str
    selected_existing_skills: list[str]
    pipeline_success: bool
    skill_success: bool
    evaluation_passed: bool | None
    agent_feedback: dict[str, Any]

    def to_controller_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_agent_dict(self) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "task_id": self.task_id,
            "skill_family": self.skill_family,
            "candidate_role": self.candidate_role,
            "selected_existing_skills": self.selected_existing_skills,
            "pipeline_success": self.pipeline_success,
            "skill_success": self.skill_success,
            "agent_feedback": self.agent_feedback,
        }
        return agent_safe_payload(payload)


@dataclass(frozen=True)
class ExperimentState:
    objective: str
    history_roots: list[str]
    observations: list[CandidateObservation]
    controller_summary: dict[str, Any]

    def to_controller_dict(self) -> dict[str, Any]:
        return {
            "schema": AGENT_STATE_SCHEMA,
            "objective": self.objective,
            "history_roots": self.history_roots,
            "controller_summary": self.controller_summary,
            "observations": [item.to_controller_dict() for item in self.observations],
        }

    def to_agent_prompt_dict(self) -> dict[str, Any]:
        return {
            "schema": "ksm.agent_controller.agent_prompt_context.v1",
            "objective": self.objective,
            "observations": [item.to_agent_dict() for item in self.observations],
            "agent_observable_summary": agent_observable_summary(self.observations),
        }


@dataclass(frozen=True)
class AgentDecision:
    route: str
    next_node: str
    rationale: str
    should_call_aspire: bool
    required_inputs: list[str]
    agent_prompt_context: dict[str, Any]
    controller_evidence: dict[str, Any]
    safety_notes: list[str]
    strategy: str = ""
    node_request: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AGENT_DECISION_SCHEMA,
            **asdict(self),
        }


def load_experiment_state_from_roots(
    *,
    objective: str,
    history_roots: list[str | Path],
) -> ExperimentState:
    roots = [Path(root).expanduser().resolve() for root in history_roots]
    reports = []
    for root in roots:
        reports.extend(_episode_reports_under(root))
    observations = [observation_from_episode_report(report, path) for path, report in reports]
    observations.sort(key=lambda item: (item.task_id, item.candidate_id, item.episode_dir))
    return ExperimentState(
        objective=objective,
        history_roots=[str(root) for root in roots],
        observations=observations,
        controller_summary=controller_summary(observations),
    )


def observation_from_episode_report(report: dict[str, Any], path: Path) -> CandidateObservation:
    feedback = _agent_feedback(report)
    manifest = _candidate_manifest(report)
    decision = _skill_reuse_decision(manifest)
    role = str(decision.get("candidate_role") or _candidate_role_from_manifest(manifest) or "")
    selected = decision.get("selected_existing_skills")
    selected_existing = [str(item) for item in selected] if isinstance(selected, list) else []
    return CandidateObservation(
        candidate_id=str(report.get("candidate_id") or "unknown_candidate"),
        task_id=str(report.get("task_id") or "unknown_task"),
        episode_dir=str(path.parent),
        skill_family=_skill_family(feedback=feedback, manifest=manifest, selected_existing=selected_existing),
        candidate_role=role or "unknown",
        selected_existing_skills=selected_existing,
        pipeline_success=bool(report.get("pipeline_success", report.get("execution_success"))),
        skill_success=bool(report.get("skill_success")),
        evaluation_passed=_evaluation_gate(report),
        agent_feedback=agent_safe_payload(feedback),
    )


def decide_next_action(state: ExperimentState) -> AgentDecision:
    observations = state.observations
    agent_context = state.to_agent_prompt_dict()
    summary = state.controller_summary
    safety_notes = [
        "Do not include predicate reports, predicate names, geometry thresholds, or ground-truth scoring details in skill-generation prompts.",
        "Use controller evaluation only for route management and stopping decisions.",
    ]
    if not observations:
        return _decision(
            route=ROUTE_OBSERVE,
            strategy=STRATEGY_NEED_OBSERVATION,
            next_node="summarize_experiment_history",
            rationale="No prior episodes are available, so the controller cannot choose a skill route yet.",
            should_call_aspire=False,
            required_inputs=["episode_report.json or suite_report.json history"],
            agent_prompt_context=agent_context,
            controller_evidence=summary,
            safety_notes=safety_notes,
        )
    if int(summary.get("evaluation_passed_count") or 0) > 0:
        return _decision(
            route=ROUTE_STOP_SUCCESS,
            strategy=STRATEGY_STOP,
            next_node="stop_and_report",
            rationale="At least one candidate passed the controller evaluation gate; preserve it before further search.",
            should_call_aspire=False,
            required_inputs=["successful_candidate_package", "execution_artifacts"],
            agent_prompt_context=agent_context,
            controller_evidence=summary,
            safety_notes=safety_notes,
        )

    repeated_family = _dominant_repeated_family(observations)
    pipeline_successes = int(summary.get("pipeline_success_count") or 0)
    missing_visual = bool(summary.get("agent_evidence_gap_counts", {}).get("missing_structured_visual_feedback"))
    if repeated_family and pipeline_successes > 0:
        next_node = "request_feedback_enrichment" if missing_visual else "propose_new_skill_spec"
        required = ["ASPIRE/evaluator visual feedback enrichment"] if missing_visual else []
        return _decision(
            route=ROUTE_NEW_SKILL,
            strategy=STRATEGY_NEW_SKILL,
            next_node=next_node,
            rationale=(
                "Controller evidence indicates that another local edit of the same existing-skill route is "
                "unlikely to explain the remaining task gap. Ask the agent to judge whether a new reusable "
                "behavior boundary is needed, using only agent-observable history."
            ),
            should_call_aspire=False,
            required_inputs=required + ["skill_registry", "agent_safe_history"],
            agent_prompt_context=agent_context,
            controller_evidence={
                "evaluation_gate": "all_failed",
                "dominant_repeated_family": repeated_family,
                "pipeline_success_count": pipeline_successes,
                "agent_evidence_gap_counts": summary.get("agent_evidence_gap_counts") or {},
                "requested_feedback_types": ["visual"] if missing_visual else [],
            },
            safety_notes=safety_notes,
        )

    failed_actions = summary.get("agent_failed_action_counts") or {}
    if failed_actions:
        return _decision(
            route=ROUTE_ADJUST,
            strategy=STRATEGY_ITERATE_REUSE,
            next_node="run_aspire_iteration",
            rationale="Execution failed before a stable task effect could be assessed; iterate binding, parameters, or the failed reusable skill path first.",
            should_call_aspire=True,
            required_inputs=["agent_safe_history", "failed_action_trace", "skill_registry"],
            agent_prompt_context=agent_context,
            controller_evidence={
                "evaluation_gate": "all_failed",
                "agent_failed_action_counts": failed_actions,
            },
            safety_notes=safety_notes,
        )

    if _has_reuse_seed(observations) and int(summary.get("evaluation_failed_count") or 0) == 0:
        return _decision(
            route=ROUTE_REUSE,
            strategy=STRATEGY_REUSE,
            next_node="run_suite",
            rationale="The available candidate evidence is a reusable existing-skill seed and there is no failed execution evidence yet.",
            should_call_aspire=False,
            required_inputs=["suite_spec", "candidate_package", "skill_registry"],
            agent_prompt_context=agent_context,
            controller_evidence={
                "evaluation_gate": "not_yet_failed",
                "skill_family_counts": summary.get("skill_family_counts") or {},
                "candidate_role_counts": summary.get("candidate_role_counts") or {},
            },
            safety_notes=safety_notes,
        )

    return _decision(
        route=ROUTE_OBSERVE,
        strategy=STRATEGY_NEED_OBSERVATION,
        next_node="request_feedback_enrichment",
        rationale="The controller cannot attribute the failure to a concrete agent-observable mechanism yet.",
        should_call_aspire=False,
        required_inputs=["ASPIRE/evaluator feedback enrichment", "agent_safe_history"],
        agent_prompt_context=agent_context,
        controller_evidence={
            "evaluation_gate": "all_failed",
            "agent_evidence_gap_counts": summary.get("agent_evidence_gap_counts") or {},
            "requested_feedback_types": ["visual"] if pipeline_successes > 0 else ["trace", "visual"],
        },
        safety_notes=safety_notes,
    )


def _decision(
    *,
    route: str,
    strategy: str,
    next_node: str,
    rationale: str,
    should_call_aspire: bool,
    required_inputs: list[str],
    agent_prompt_context: dict[str, Any],
    controller_evidence: dict[str, Any],
    safety_notes: list[str],
) -> AgentDecision:
    return AgentDecision(
        route=route,
        strategy=strategy,
        next_node=next_node,
        rationale=rationale,
        should_call_aspire=should_call_aspire,
        required_inputs=required_inputs,
        agent_prompt_context=agent_prompt_context,
        controller_evidence=controller_evidence,
        safety_notes=safety_notes,
        node_request={
            "node": next_node,
            "strategy": strategy,
            "dry_request": True,
            "required_inputs": required_inputs,
            "reason": rationale,
        },
    )


def controller_summary(observations: list[CandidateObservation]) -> dict[str, Any]:
    family_counts = Counter(item.skill_family for item in observations)
    role_counts = Counter(item.candidate_role for item in observations)
    gap_counts: Counter[str] = Counter()
    failed_actions: Counter[str] = Counter()
    for item in observations:
        feedback = item.agent_feedback if isinstance(item.agent_feedback, dict) else {}
        gap_counts.update(str(gap) for gap in feedback.get("evidence_gaps") or [])
        failed_actions.update(str(action) for action in feedback.get("failed_actions") or [])
    return {
        "total_observations": len(observations),
        "evaluation_passed_count": sum(1 for item in observations if item.evaluation_passed is True),
        "evaluation_failed_count": sum(1 for item in observations if item.evaluation_passed is False),
        "pipeline_success_count": sum(1 for item in observations if item.pipeline_success),
        "skill_success_count": sum(1 for item in observations if item.skill_success),
        "skill_family_counts": dict(sorted(family_counts.items())),
        "candidate_role_counts": dict(sorted(role_counts.items())),
        "agent_evidence_gap_counts": dict(sorted(gap_counts.items())),
        "agent_failed_action_counts": dict(sorted(failed_actions.items())),
    }


def agent_observable_summary(observations: list[CandidateObservation]) -> dict[str, Any]:
    families = Counter(item.skill_family for item in observations)
    statuses = Counter(
        str((item.agent_feedback or {}).get("observable_status") or "unknown")
        for item in observations
    )
    gaps: Counter[str] = Counter()
    for item in observations:
        feedback = item.agent_feedback if isinstance(item.agent_feedback, dict) else {}
        gaps.update(str(gap) for gap in feedback.get("evidence_gaps") or [])
    return {
        "num_observations": len(observations),
        "skill_family_counts": dict(sorted(families.items())),
        "observable_status_counts": dict(sorted(statuses.items())),
        "evidence_gap_counts": dict(sorted(gaps.items())),
    }


def assert_agent_context_safe(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = (
        "predicate_report",
        "predicate_success",
        "ground_truth",
        "groundtruth",
        "min_xy_overlap",
        "z_gap",
    )
    leaked = [token for token in forbidden if token in text]
    if leaked:
        raise ValueError(f"agent prompt context contains evaluator-only fields: {leaked}")


def _episode_reports_under(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if root.is_file() and root.name == "episode_report.json":
        return [(root, read_json(root))]
    if root.is_file() and root.name == "suite_report.json":
        suite = read_json(root)
        out: list[tuple[Path, dict[str, Any]]] = []
        for episode in suite.get("episodes") or []:
            if not isinstance(episode, dict):
                continue
            report = episode.get("report")
            if isinstance(report, dict):
                episode_dir = Path(str(episode.get("episode_dir") or root.parent))
                out.append((episode_dir / "episode_report.json", report))
        return out
    if not root.exists():
        raise FileNotFoundError(root)
    return [
        (path, read_json(path))
        for path in sorted(root.rglob("episode_report.json"))
        if path.is_file()
    ]


def _agent_feedback(report: dict[str, Any]) -> dict[str, Any]:
    feedback = report.get("feedback_attribution")
    if isinstance(feedback, dict) and isinstance(feedback.get("agent_feedback"), dict):
        if feedback.get("version") != ATTRIBUTION_VERSION and _has_rebuildable_raw_evidence(report):
            rebuilt = analyze_episode_report(report)
            if isinstance(rebuilt.get("agent_feedback"), dict):
                return dict(rebuilt["agent_feedback"])
        return dict(feedback["agent_feedback"])
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), dict) else {}
    agent_path = artifacts.get("agent_feedback")
    if isinstance(agent_path, str) and agent_path:
        path = Path(agent_path)
        if path.exists():
            data = read_json(path)
            if isinstance(data, dict):
                return data
    return {}


def _has_rebuildable_raw_evidence(report: dict[str, Any]) -> bool:
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    return any(
        key in metadata
        for key in (
            "run_result",
            "visual_evidence",
            "trace_analysis",
            "task_args",
            "candidate_skill_args",
            "runtime_args",
        )
    )


def _candidate_manifest(report: dict[str, Any]) -> dict[str, Any]:
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    manifest = metadata.get("candidate_manifest")
    return dict(manifest) if isinstance(manifest, dict) else {}


def _skill_reuse_decision(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    for key in ("llm_skill_reuse_decision", "skill_reuse_decision"):
        value = metadata.get(key)
        if isinstance(value, dict):
            return dict(value)
    value = manifest.get("skill_reuse_decision")
    return dict(value) if isinstance(value, dict) else {}


def _candidate_role_from_manifest(manifest: dict[str, Any]) -> str:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    if metadata.get("runtime_wrapper_only") is True:
        return "pure_wrapper_reuse"
    return ""


def _skill_family(
    *,
    feedback: dict[str, Any],
    manifest: dict[str, Any],
    selected_existing: list[str],
) -> str:
    actions: list[str] = list(selected_existing)
    for item in feedback.get("action_timeline") or []:
        if isinstance(item, dict) and item.get("action"):
            actions.append(str(item["action"]))
    manifest_text = json.dumps(manifest, ensure_ascii=False).lower()
    joined = " ".join(actions).lower() + " " + manifest_text
    if "semantic_pickplace.yaml" in joined:
        return "semantic_pickplace_reuse"
    if "semantic_pick.yaml" in joined and "semantic_place.yaml" in joined:
        return "decomposed_semantic_pick_place_reuse"
    if "semantic_pick.yaml" in joined:
        return "semantic_pick_reuse"
    if "stack" in joined:
        return "stack_candidate"
    return "unclassified"


def _evaluation_gate(report: dict[str, Any]) -> bool | None:
    if report.get("task_success") is not None:
        return bool(report.get("task_success"))
    if report.get("success") is not None:
        return bool(report.get("success"))
    return None


def _dominant_repeated_family(observations: list[CandidateObservation]) -> str | None:
    counts = Counter(
        item.skill_family
        for item in observations
        if item.skill_family not in {"unclassified", ""}
    )
    if not counts:
        return None
    family, count = counts.most_common(1)[0]
    if count < 2:
        return None
    return family


def _has_reuse_seed(observations: list[CandidateObservation]) -> bool:
    for item in observations:
        if item.candidate_role in {"pure_wrapper_reuse", "reuse_existing_skill"}:
            return True
        if item.skill_family.endswith("_reuse") and item.selected_existing_skills:
            return True
    return False
