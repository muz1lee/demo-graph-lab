from __future__ import annotations

import json
from typing import Any


def build_stage_level_prompt_contract(task: dict[str, Any]) -> str:
    context = extract_stage_context(task)
    feedback = extract_stage_feedback(task)
    if not context.get("stage_id"):
        robodojo = task.get("robodojo") if isinstance(task.get("robodojo"), dict) else {}
        if robodojo:
            return """
Full-task RoboDojo ASPIRE contract:
- No staged subtask contract is active for this task.
- Do not assume a fixed subtask chain. Treat static staged guides as diagnostic fallback material only.
- First decide whether the complete task can be expressed by existing KW skills from the registry.
- Return skill_reuse_decision.decision as reuse_existing_skill when the workflow is mainly a composition/wrapper around existing KW skills.
- Return skill_reuse_decision.decision as new_subskill_needed only when the registry cannot express an essential behavior.
- If reusing existing skills, choose the skill path and public args from the registry; do not create a new reusable skill candidate.
- If a new subskill is needed, state the missing behavior in skill_reuse_decision.rationale and keep the YAML conservative; do not fake task success.
""".strip()
        return """
Stage-level ASPIRE contract:
- No RoboDojo stage-level contract was detected in this task.
- Use the general KW YAML contract and prefer existing high-level skills when they match the requested behavior.
""".strip()

    decision = context.get("skill_reuse_decision") if isinstance(context.get("skill_reuse_decision"), dict) else {}
    lifecycle = decision.get("candidate_lifecycle") if isinstance(decision.get("candidate_lifecycle"), dict) else {}
    selected = [str(item) for item in decision.get("selected_existing_skills") or []]
    related = [str(item) for item in decision.get("related_existing_skills") or []]
    output_contract = (
        "maintain_or_rewrite_subskill_candidate"
        if bool(lifecycle.get("maintain_candidate"))
        else "do_not_create_new_skill_candidate"
    )
    lines = [
        "Stage-level ASPIRE contract:",
        f"- Stage id: {context.get('stage_id')}",
        f"- Skill decision: {decision.get('decision') or 'unknown'}",
        f"- Output contract: {output_contract}",
        f"- Selected existing skills: {json.dumps(selected, ensure_ascii=False)}",
        f"- Related existing skills: {json.dumps(related, ensure_ascii=False)}",
        f"- Candidate lifecycle: {json.dumps(lifecycle, ensure_ascii=False)}",
    ]
    if feedback:
        execution = feedback.get("execution_feedback") if isinstance(feedback.get("execution_feedback"), dict) else {}
        lines.extend(
            [
                "Previous stage feedback:",
                f"- Previous status: {execution.get('stage_status')}",
                f"- Previous success: {execution.get('success')}",
                f"- Failure signature: {execution.get('failure_signature')}",
                f"- Failure category: {execution.get('failure_category')}",
                f"- ASPIRE next action: {feedback.get('aspire_next_action')}",
                "- The new candidate must make a concrete change that addresses this feedback; do not only rename the candidate.",
            ]
        )
    if decision.get("decision") == "reuse_existing_skill":
        lines.extend(
            [
                "- This is a reuse/refinement stage. Do not propose, name, or imply a new reusable robot skill for this stage.",
                "- The generated YAML may wrap or retune the selected existing skill, but it must not replace it with a newly invented primitive.",
                "- Keep verifier-gated semantics intact. Do not remove verifier calls, bypass returned success values, assert unconditional success, or fabricate task success.",
                "- Prefer changing explicit call arguments, labels, aliases, offsets, delays, or other public parameters that can plausibly address the observed failure.",
                "- The hypothesis and change_summary must state why the reuse/refinement should improve the previous failure.",
            ]
        )
        if selected:
            lines.append("- The workflow should normally call one of selected_existing_skills unless the response explicitly explains why a related existing skill is safer.")
    elif decision.get("decision") == "new_subskill_needed":
        lines.extend(
            [
                "- This is a maintained new-subskill candidate stage. ASPIRE may rewrite the candidate YAML for this stage.",
                "- The generated candidate must still be legal KW YAML and must expose clear args, expected effects, and verifier/observable evidence where available.",
                "- If the current KW registry cannot express the stage, output a conservative gap-oriented candidate rather than fake success.",
            ]
        )
    else:
        lines.extend(
            [
                "- The stage decision is unknown. Prefer existing skills first and avoid introducing new skills unless the task explicitly requires a missing primitive.",
                "- Do not fake verifier, predicate, or task success.",
            ]
        )
    return "\n".join(lines)


def extract_stage_context(task: dict[str, Any]) -> dict[str, Any]:
    robodojo = task.get("robodojo") if isinstance(task.get("robodojo"), dict) else {}
    stage = robodojo.get("stage") if isinstance(robodojo.get("stage"), dict) else {}
    candidate = stage.get("skill_candidate") if isinstance(stage.get("skill_candidate"), dict) else {}
    decision = stage.get("skill_reuse_decision")
    if not isinstance(decision, dict):
        decision = candidate.get("skill_reuse_decision") if isinstance(candidate.get("skill_reuse_decision"), dict) else {}
    return {
        "task_id": task.get("task_id"),
        "stage_id": stage.get("stage_id") or candidate.get("stage_id"),
        "skill_reuse_decision": decision,
    }


def extract_stage_feedback(task: dict[str, Any]) -> dict[str, Any]:
    robodojo = task.get("robodojo") if isinstance(task.get("robodojo"), dict) else {}
    for key in ("stage_feedback", "aspire_feedback_packet"):
        value = robodojo.get(key)
        if isinstance(value, dict):
            return value
    value = task.get("aspire_feedback_packet")
    return value if isinstance(value, dict) else {}
