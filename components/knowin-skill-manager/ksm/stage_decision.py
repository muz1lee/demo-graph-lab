from __future__ import annotations

from typing import Any


SCHEMA = "ksm.robodojo.skill_reuse_decision.v1"


def decide_stage_skill_strategy(*, stage_id: str, stage: dict[str, Any], registry: Any) -> dict[str, Any]:
    skill_paths = set(getattr(registry, "skill_paths", set()) or set())
    ctrl = set(str(item) for item in getattr(registry, "ctrl", []) or [])
    required = [str(item) for item in stage.get("required_primitives") or []]

    if stage_id == "bind_selected_object_container":
        return _decision(
            stage_id=stage_id,
            decision="reuse_existing_skill",
            yaml_type="metadata_binding",
            maintain_candidate=False,
            selected=[],
            related=["ksm_metadata_binding"],
            rationale="Object/container binding is handled by KSM metadata, not by a new robot skill.",
            aspire_action="record_binding_feedback",
        )

    if stage_id == "pick_bottle" and "pickplace/semantic_pick.yaml" in skill_paths:
        return _decision(
            stage_id=stage_id,
            decision="reuse_existing_skill",
            yaml_type="reuse_wrapper",
            maintain_candidate=False,
            selected=["pickplace/semantic_pick.yaml"],
            related=["pickplace/pick_verifier.yaml"],
            rationale="KW already provides verifier-gated semantic picking; ASPIRE should tune binding and call parameters instead of inventing a new pick skill.",
            aspire_action="iterate_reuse_binding_or_parameters",
        )

    if stage_id == "verify_inside":
        return _decision(
            stage_id=stage_id,
            decision="reuse_existing_skill",
            yaml_type="observable_predicate",
            maintain_candidate=False,
            selected=["sim.eval.predicates.inside"],
            related=[],
            rationale="Inside checking is an observable condition, not a motion skill candidate.",
            aspire_action="record_predicate_feedback",
        )

    if stage_id == "go_home" and "go_home" in ctrl:
        return _decision(
            stage_id=stage_id,
            decision="reuse_existing_skill",
            yaml_type="control_wrapper",
            maintain_candidate=False,
            selected=["/ctrl/go_home"],
            related=[],
            rationale="KW control already exposes go_home; no new skill candidate is needed.",
            aspire_action="record_control_feedback",
        )

    if stage_id == "release_into_dustbin":
        related = [path for path in ("pickplace/semantic_place.yaml", "pickplace/semantic_pickplace.yaml") if path in skill_paths]
        return _decision(
            stage_id=stage_id,
            decision="new_subskill_needed",
            yaml_type="native_skill_spec",
            maintain_candidate=True,
            selected=[],
            related=related,
            rationale="Existing place skills can move toward a label, but they do not express verified container-interior release.",
            aspire_action="rewrite_or_create_subskill_candidate",
            required_primitives=required,
        )

    if stage_id == "move_above_dustbin":
        return _decision(
            stage_id=stage_id,
            decision="new_subskill_needed",
            yaml_type="native_skill_spec",
            maintain_candidate=True,
            selected=[],
            related=[],
            rationale="The registry has no explicit move-above-receptacle approach primitive for this stage.",
            aspire_action="write_subskill_candidate",
            required_primitives=required,
        )

    return _decision(
        stage_id=stage_id,
        decision="new_subskill_needed",
        yaml_type="gap_report",
        maintain_candidate=True,
        selected=[],
        related=[],
        rationale="No reusable KW skill was identified for this stage.",
        aspire_action="write_subskill_candidate",
        required_primitives=required,
    )


def _decision(
    *,
    stage_id: str,
    decision: str,
    yaml_type: str,
    maintain_candidate: bool,
    selected: list[str],
    related: list[str],
    rationale: str,
    aspire_action: str,
    required_primitives: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "stage_id": stage_id,
        "decision": decision,
        "yaml_type": yaml_type,
        "selected_existing_skills": selected,
        "related_existing_skills": related,
        "required_primitives": required_primitives or [],
        "candidate_lifecycle": {
            "maintain_candidate": bool(maintain_candidate),
            "promotion_eligible": bool(maintain_candidate),
            "reason": "new subskill candidate" if maintain_candidate else "reuse/reference only",
        },
        "aspire_action": aspire_action,
        "rationale": rationale,
    }
