from __future__ import annotations

import json
from typing import Any

from .agent_state import (
    AgentDecision,
    ExperimentState,
    STRATEGY_NEW_SKILL,
    assert_agent_context_safe,
)
from .feedback_attribution import agent_safe_payload


SKILL_SPEC_SCHEMA = "ksm.agent_controller.new_skill_spec.v1"
AGENT_CONTROLLER_TASK_KEY = "agent_controller"

ROLE_REUSE_EXISTING = "reuse_existing_skill"
ROLE_SKILL_SPECIALIZATION = "skill_specialization"
ROLE_NEW_BEHAVIOR = "new_behavior_skill"
ROLE_BLOCKED_BY_GAP = "blocked_by_gap"

_ROLE_ALIASES = {
    "pure_wrapper_reuse": ROLE_REUSE_EXISTING,
    "reusable_composition_skill": ROLE_SKILL_SPECIALIZATION,
    "new_behavior_subskill": ROLE_NEW_BEHAVIOR,
    "blocked_by_missing_primitive": ROLE_BLOCKED_BY_GAP,
}


def propose_new_skill_spec(
    *,
    state: ExperimentState,
    decision: AgentDecision,
) -> dict[str, Any]:
    """Propose a new-skill boundary from agent-safe evidence.

    This is intentionally not a YAML generator. It produces a design intent for
    a later ASPIRE node and refuses to proceed when observable evidence is too
    thin.
    """

    context = decision.agent_prompt_context
    assert_agent_context_safe(context)
    if decision.strategy != STRATEGY_NEW_SKILL:
        return {
            "schema": SKILL_SPEC_SCHEMA,
            "status": "skipped",
            "reason": f"strategy is {decision.strategy}, not {STRATEGY_NEW_SKILL}",
        }

    agent_summary = context.get("agent_observable_summary") if isinstance(context.get("agent_observable_summary"), dict) else {}
    evidence_gaps = agent_summary.get("evidence_gap_counts") if isinstance(agent_summary.get("evidence_gap_counts"), dict) else {}
    visual_ready = _has_structured_visual_observation(context)
    if evidence_gaps.get("missing_structured_visual_feedback") and not visual_ready:
        return {
            "schema": SKILL_SPEC_SCHEMA,
            "status": "needs_observation",
            "strategy_judgment": {
                "strategy": STRATEGY_NEW_SKILL,
                "confidence": "low_without_visual_feedback",
                "reason": "The route may require a new behavior boundary, but the agent-visible record lacks structured visual evidence.",
            },
            "next_node": {
                "node": "request_feedback_enrichment",
                "required_inputs": [
                    "WebUI video or keyframes, if available",
                    "task goal text",
                    "candidate action timeline",
                    "ASPIRE/evaluator feedback enrichment pipeline",
                ],
            },
            "agent_prompt_context": context,
        }

    family = _target_family(context)
    selected_skills = _selected_existing_skills(context)
    spec = {
        "schema": SKILL_SPEC_SCHEMA,
        "status": "proposed",
        "strategy_judgment": {
            "strategy": STRATEGY_NEW_SKILL,
            "confidence": "medium",
            "reason": (
                "Agent-visible history suggests the next candidate should define a reusable behavior boundary "
                "instead of only repeating the same existing-skill wrapper."
            ),
        },
        "target_skill_family": family["family"],
        "candidate_role": family["candidate_role"],
        "candidate_intent": family["intent"],
        "reusable_interface": {
            "name": family["interface_name"],
            "args": family["args"],
            "expected_effects": family["expected_effects"],
            "observable_success": family["observable_success"],
            "failure_modes": family["failure_modes"],
        },
        "reuse_policy": {
            "reuse_existing_skills_for": [
                "stable low-level acquisition or placement substeps when they are directly useful",
            ],
            "selected_existing_skills": selected_skills,
            "must_add_behavior_contract": [
                "task-family arguments and reusable interface",
                "expected effects and observable success",
                "failure modes that ASPIRE can iterate on",
            ],
        },
        "not_allowed": [
            "Do not pass evaluator-only predicate reports or geometry measurements into the generator.",
            "Do not report a candidate as skill_specialization unless it adds a named behavior contract beyond forwarding args.",
            "Do not hard-code scene-specific object IDs beyond declared task args.",
        ],
        "next_node": {
            "node": "run_aspire_iteration",
            "required_inputs": [
                "this skill spec",
                "agent-safe history",
                "KW skill registry",
            ],
        },
        "agent_prompt_context": agent_safe_payload(context),
    }
    assert_agent_context_safe(spec["agent_prompt_context"])
    return spec


def task_new_skill_spec(task: dict[str, Any]) -> dict[str, Any]:
    controller = task.get(AGENT_CONTROLLER_TASK_KEY) if isinstance(task.get(AGENT_CONTROLLER_TASK_KEY), dict) else {}
    value = controller.get("new_skill_spec") if isinstance(controller.get("new_skill_spec"), dict) else None
    if value is None:
        value = task.get("new_skill_spec") if isinstance(task.get("new_skill_spec"), dict) else None
    return dict(value) if isinstance(value, dict) else {}


def task_new_skill_spec_prompt_payload(task: dict[str, Any]) -> dict[str, Any]:
    spec = task_new_skill_spec(task)
    if not spec:
        return {}
    payload = agent_safe_payload(spec)
    if isinstance(payload, dict):
        assert_agent_context_safe(payload)
        return payload
    return {}


def validate_new_skill_spec_contract(*, skill: dict[str, Any], task: dict[str, Any], payload: dict[str, Any] | None = None) -> list[str]:
    spec = task_new_skill_spec(task)
    if not spec or spec.get("status") != "proposed":
        return []
    violations: list[str] = []
    decision = payload.get("skill_reuse_decision") if isinstance(payload, dict) and isinstance(payload.get("skill_reuse_decision"), dict) else {}
    role = canonical_candidate_role(str(decision.get("candidate_role") or "").strip())
    requested_role = canonical_candidate_role(
        str(spec.get("candidate_role") or spec.get("target_candidate_role") or ROLE_SKILL_SPECIALIZATION).strip()
    )
    if requested_role in {ROLE_SKILL_SPECIALIZATION, ROLE_NEW_BEHAVIOR} and role == ROLE_REUSE_EXISTING:
        violations.append(
            "agent new_skill_spec requires skill_specialization or new_behavior_skill, not reuse_existing_skill"
        )
    if requested_role in {ROLE_SKILL_SPECIALIZATION, ROLE_NEW_BEHAVIOR} and role not in {
        ROLE_SKILL_SPECIALIZATION,
        ROLE_NEW_BEHAVIOR,
        ROLE_BLOCKED_BY_GAP,
    }:
        violations.append(
            "agent new_skill_spec response must declare candidate_role as skill_specialization, new_behavior_skill, or blocked_by_gap"
        )
    actions = _collect_actions(skill.get("workflow") or [])
    selected = _selected_existing_from_spec(spec)
    if role == ROLE_NEW_BEHAVIOR and actions and selected and all(action in selected for action in actions):
        violations.append("new_behavior_skill must introduce a new mechanism, not only selected existing skills")
    if role == ROLE_SKILL_SPECIALIZATION and actions and selected and all(action in selected for action in actions):
        if not _has_added_behavior_contract(decision):
            violations.append(
                "skill_specialization may reuse existing skills, but must declare added_behavior_contract beyond selected skill forwarding"
            )
    interface = spec.get("reusable_interface") if isinstance(spec.get("reusable_interface"), dict) else {}
    name = str(interface.get("name") or "").strip()
    llm_interface = decision.get("reusable_interface") if isinstance(decision.get("reusable_interface"), dict) else {}
    if name and role in {ROLE_SKILL_SPECIALIZATION, ROLE_NEW_BEHAVIOR} and not llm_interface:
        violations.append(f"agent new_skill_spec expects reusable_interface details for {name}")
    if role in {ROLE_SKILL_SPECIALIZATION, ROLE_NEW_BEHAVIOR}:
        top_args = skill.get("args") if isinstance(skill.get("args"), dict) else {}
        workflow = skill.get("workflow") if isinstance(skill.get("workflow"), list) else []
        semantic_context_args = set(_semantic_context_arg_names(interface))
        for arg_name in _interface_arg_names(interface):
            if arg_name not in top_args:
                violations.append(f"agent new_skill_spec interface arg '{arg_name}' is missing from skill args")
            elif arg_name in semantic_context_args:
                continue
            elif not _workflow_references_arg(workflow, arg_name):
                violations.append(f"agent new_skill_spec interface arg '{arg_name}' is not used by workflow")
            elif not _workflow_references_arg_in_effective_step(workflow, arg_name):
                violations.append(f"agent new_skill_spec interface arg '{arg_name}' is used only in identity/no-op steps")
    return violations


def validate_skill_role_contract(*, skill: dict[str, Any], payload: dict[str, Any] | None = None) -> list[str]:
    """Validate the declared candidate role against the generated workflow.

    This check is intentionally independent from agent_controller.new_skill_spec.
    The role taxonomy should be coherent for every LLM-generated candidate:
    direct reuse is allowed, specialization may reuse existing skills while
    adding a behavior contract, and new behavior cannot be only a relabeled
    call sequence over selected existing skills.
    """

    decision = payload.get("skill_reuse_decision") if isinstance(payload, dict) and isinstance(payload.get("skill_reuse_decision"), dict) else {}
    if not decision:
        return []
    violations: list[str] = []
    role = canonical_candidate_role(str(decision.get("candidate_role") or "").strip())
    if role not in {
        "",
        ROLE_REUSE_EXISTING,
        ROLE_SKILL_SPECIALIZATION,
        ROLE_NEW_BEHAVIOR,
        ROLE_BLOCKED_BY_GAP,
    }:
        violations.append(
            "skill_reuse_decision.candidate_role must be reuse_existing_skill, skill_specialization, new_behavior_skill, or blocked_by_gap"
        )
        return violations

    if role in {ROLE_SKILL_SPECIALIZATION, ROLE_NEW_BEHAVIOR}:
        if not isinstance(decision.get("reusable_interface"), dict) or not decision.get("reusable_interface"):
            violations.append(f"{role} requires reusable_interface details")
        if not _has_added_behavior_contract(decision):
            violations.append(f"{role} requires added_behavior_contract details")

    actions = _collect_actions(skill.get("workflow") or [])
    selected = {
        str(item)
        for item in (decision.get("selected_existing_skills") or [])
        if isinstance(item, str)
    }
    if role == ROLE_NEW_BEHAVIOR and actions and selected and all(action in selected for action in actions):
        violations.append(
            "new_behavior_skill must introduce a new mechanism; use skill_specialization when only composing selected existing skills"
        )
    if role == ROLE_REUSE_EXISTING and _has_added_behavior_contract(decision):
        violations.append("reuse_existing_skill must not claim an added_behavior_contract")
    return violations


def canonical_candidate_role(role: str) -> str:
    value = str(role or "").strip()
    return _ROLE_ALIASES.get(value, value)


def _has_added_behavior_contract(decision: dict[str, Any]) -> bool:
    for key in ("added_behavior_contract", "behavior_contract"):
        value = decision.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _interface_arg_names(interface: dict[str, Any]) -> list[str]:
    raw = interface.get("args") if isinstance(interface, dict) else []
    names: list[str] = []
    if isinstance(raw, dict):
        names = [str(key) for key in raw]
    elif isinstance(raw, list):
        names = [str(item) for item in raw if str(item or "").strip()]
    return [name for name in names if name.replace("_", "").isalnum()]


def _semantic_context_arg_names(interface: dict[str, Any]) -> list[str]:
    raw = interface.get("semantic_context_args") if isinstance(interface, dict) else []
    names: list[str] = []
    if isinstance(raw, list):
        names = [str(item) for item in raw if str(item or "").strip()]
    elif isinstance(raw, dict):
        names = [str(key) for key in raw]
    return [name for name in names if name.replace("_", "").isalnum()]


def _workflow_references_arg_in_effective_step(node: Any, arg_name: str) -> bool:
    if isinstance(node, list):
        return any(_workflow_references_arg_in_effective_step(item, arg_name) for item in node)
    if not isinstance(node, dict):
        return False
    action = str(node.get("action") or "")
    if action.endswith("/reasoning/identity"):
        return any(
            _workflow_references_arg_in_effective_step(node.get(branch), arg_name)
            for branch in ("workflow", "do", "else", "if", "while")
        )
    if _workflow_references_arg(node.get("args"), arg_name):
        return True
    return any(
        _workflow_references_arg_in_effective_step(node.get(branch), arg_name)
        for branch in ("workflow", "do", "else", "if", "while")
    )


def _workflow_references_arg(node: Any, arg_name: str) -> bool:
    if isinstance(node, dict):
        return any(_workflow_references_arg(value, arg_name) for value in node.values())
    if isinstance(node, list):
        return any(_workflow_references_arg(value, arg_name) for value in node)
    if isinstance(node, str):
        return f"args.{arg_name}" in node or f"${{args.{arg_name}}}" in node
    return False


def _selected_existing_from_spec(spec: dict[str, Any]) -> set[str]:
    reuse_policy = spec.get("reuse_policy") if isinstance(spec.get("reuse_policy"), dict) else {}
    selected = reuse_policy.get("selected_existing_skills")
    return {str(item) for item in selected} if isinstance(selected, list) else set()


def _collect_actions(node: Any) -> list[str]:
    actions: list[str] = []
    if isinstance(node, list):
        for item in node:
            actions.extend(_collect_actions(item))
    elif isinstance(node, dict):
        action = node.get("action")
        if isinstance(action, str):
            actions.append(action)
        for key in ("workflow", "do", "else"):
            actions.extend(_collect_actions(node.get(key)))
        if isinstance(node.get("if"), dict):
            actions.extend(_collect_actions(node["if"]))
        if isinstance(node.get("while"), dict):
            actions.extend(_collect_actions(node["while"]))
    return actions


def _has_structured_visual_observation(context: dict[str, Any]) -> bool:
    for observation in context.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        feedback = observation.get("agent_feedback") if isinstance(observation.get("agent_feedback"), dict) else {}
        visual = feedback.get("visual_feedback") if isinstance(feedback.get("visual_feedback"), dict) else {}
        if visual.get("analysis_available") is True:
            return True
        if visual.get("status") in {"analyzed", "model_analyzed"}:
            return True
    return False


def _target_family(context: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(context, ensure_ascii=False).lower()
    if "stack" in text or "on_block" in text or "on block" in text:
        return {
            "family": "stacking",
            "candidate_role": ROLE_SKILL_SPECIALIZATION,
            "interface_name": "stack_step",
            "args": {
                "source_label": "object to place on the support",
                "support_label": "current support object or top-of-stack target",
                "arm_id": "optional arm selector; default should preserve existing KW skill behavior when possible",
            },
            "intent": [
                "reuse stable object acquisition when available",
                "re-localize the support immediately before placement",
                "place onto the support top surface with alignment-aware release",
                "retreat without disturbing the newly placed object",
                "preserve visual/trace hooks for post-action observation",
            ],
            "expected_effects": [
                "source object is released on the support object rather than beside it",
                "support object remains usable for the next stack step",
            ],
            "observable_success": [
                "source visibly contacts the support top surface",
                "source remains near the support after gripper retreat",
                "no obvious push-over or slide-away occurs in the camera view",
            ],
            "failure_modes": [
                "support was not re-localized after previous manipulation",
                "release height or lateral alignment is poor",
                "retreat motion disturbs the stack",
                "existing pick skill succeeds but placement semantics are too coarse",
            ],
        }
    return {
        "family": "task_family_behavior",
        "candidate_role": ROLE_SKILL_SPECIALIZATION,
        "interface_name": "task_step_behavior",
        "args": {
            "source_label": "primary manipulated object",
            "target_label": "target object, region, or relation anchor",
            "arm_id": "optional arm selector; default should preserve existing KW skill behavior when possible",
        },
        "intent": [
            "reuse existing mature skills for substeps that are already reliable",
            "make the missing task-family behavior explicit as a reusable interface",
            "preserve observable feedback hooks for later iteration",
        ],
        "expected_effects": [
            "the source object satisfies the intended task-family relation to the target",
        ],
        "observable_success": [
            "camera evidence shows the source object changed state in the intended direction",
        ],
        "failure_modes": [
            "existing reusable skill does not express the task-family relation",
            "visual feedback is insufficient to localize the failed behavior",
        ],
    }


def _selected_existing_skills(context: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for observation in context.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        for skill in observation.get("selected_existing_skills") or []:
            if isinstance(skill, str):
                out.append(skill)
        feedback = observation.get("agent_feedback") if isinstance(observation.get("agent_feedback"), dict) else {}
        for item in feedback.get("action_timeline") or []:
            if isinstance(item, dict) and isinstance(item.get("action"), str):
                out.append(item["action"])
    return sorted(dict.fromkeys(out))
