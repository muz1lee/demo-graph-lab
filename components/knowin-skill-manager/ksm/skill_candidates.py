from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import read_yaml, write_json
from .stage_decision import decide_stage_skill_strategy


SCHEMA = "ksm.robodojo.skill_candidate_artifacts.v1"


def default_stage_guide_path(task_family: str = "put_single_object_into_container") -> Path:
    return Path(__file__).resolve().parents[1] / "robodojo_stage_guides" / f"{task_family}.yaml"


def load_stage_guide(path: str | Path | None = None) -> dict[str, Any]:
    guide_path = Path(path).expanduser().resolve() if path else default_stage_guide_path()
    payload = read_yaml(guide_path)
    if not isinstance(payload, dict):
        raise ValueError(f"stage guide must be a mapping: {guide_path}")
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError(f"stage guide must define non-empty stages: {guide_path}")
    payload = dict(payload)
    payload["_path"] = str(guide_path)
    return payload


def build_skill_candidate_artifacts(
    *,
    selected_task: dict[str, Any],
    registry: Any,
    generated_manifest: dict[str, Any] | None = None,
    guide: dict[str, Any] | None = None,
) -> dict[str, Any]:
    guide_payload = guide or load_stage_guide()
    binding = selected_task.get("binding") if isinstance(selected_task.get("binding"), dict) else {}
    target_asset = selected_task.get("target_asset") if isinstance(selected_task.get("target_asset"), dict) else {}
    place_asset = selected_task.get("place_asset") if isinstance(selected_task.get("place_asset"), dict) else {}
    task_family = str(guide_payload.get("task_family") or "put_single_object_into_container")
    stage_records = [
        instantiate_stage(
            stage=stage,
            selected_task=selected_task,
            registry=registry,
            order=index + 1,
        )
        for index, stage in enumerate(guide_payload.get("stages", []))
        if isinstance(stage, dict)
    ]
    final_workflow = build_workflow_candidate(
        task_family=task_family,
        selected_task=selected_task,
        stage_records=stage_records,
        generated_manifest=generated_manifest,
    )
    stage_skill_decisions = [stage["skill_reuse_decision"] for stage in stage_records]
    subskill_candidates = [
        stage["skill_candidate"]
        for stage in stage_records
        if bool(((stage.get("skill_reuse_decision") or {}).get("candidate_lifecycle") or {}).get("maintain_candidate"))
    ]
    gap_report = build_gap_report(stage_records)
    return {
        "schema": SCHEMA,
        "task_family": task_family,
        "guide": {
            "guide_id": guide_payload.get("guide_id"),
            "source": guide_payload.get("source"),
            "path": guide_payload.get("_path"),
            "stage_ids": [stage["stage_id"] for stage in stage_records],
        },
        "task": {
            "task_id": selected_task.get("task_id"),
            "task_class": selected_task.get("task_class"),
            "prompt": selected_task.get("prompt"),
            "scene_path": selected_task.get("scene_path"),
            "suite_path": selected_task.get("suite_path"),
        },
        "binding": {
            "source_ref": target_asset.get("id") or binding.get("target_ref"),
            "source_category": target_asset.get("category") or binding.get("target_category"),
            "source_label": binding.get("primary_pick_label"),
            "source_aliases": binding.get("candidate_pick_labels") or [],
            "container_ref": place_asset.get("id") or binding.get("place_ref"),
            "container_category": place_asset.get("category") or binding.get("place_category"),
            "container_label": binding.get("primary_place_label"),
            "container_aliases": binding.get("candidate_place_labels") or [],
        },
        "final_workflow_candidate": final_workflow,
        "staged_plan": stage_records,
        "stage_skill_decisions": stage_skill_decisions,
        "subskill_candidates": subskill_candidates,
        "gap_report": gap_report,
    }


def write_skill_candidate_artifacts(output_dir: str | Path, artifacts: dict[str, Any]) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    staged_plan_path = write_json(root / "staged_plan.json", {
        "schema": "ksm.robodojo.staged_plan.v1",
        "task_family": artifacts.get("task_family"),
        "guide": artifacts.get("guide"),
        "task": artifacts.get("task"),
        "binding": artifacts.get("binding"),
        "stages": artifacts.get("staged_plan") or [],
        "final_workflow_candidate": artifacts.get("final_workflow_candidate"),
    })
    candidates_path = write_json(root / "skill_candidates.json", {
        "schema": "ksm.robodojo.skill_candidates.v1",
        "task_family": artifacts.get("task_family"),
        "binding": artifacts.get("binding"),
        "final_workflow_candidate": artifacts.get("final_workflow_candidate"),
        "stage_skill_decisions": artifacts.get("stage_skill_decisions") or [],
        "subskill_candidates": artifacts.get("subskill_candidates") or [],
    })
    gap_path = write_json(root / "gap_report.json", artifacts.get("gap_report") or {})
    report_path = root / "README.md"
    report_path.write_text(render_skill_candidate_report(artifacts), encoding="utf-8")
    return {
        "staged_plan": str(staged_plan_path),
        "skill_candidates": str(candidates_path),
        "gap_report": str(gap_path),
        "report": str(report_path),
    }


def instantiate_stage(*, stage: dict[str, Any], selected_task: dict[str, Any], registry: Any, order: int | None = None) -> dict[str, Any]:
    stage_id = str(stage.get("stage_id") or "")
    coverage = assess_stage_coverage(stage_id=stage_id, stage=stage, registry=registry)
    decision = decide_stage_skill_strategy(stage_id=stage_id, stage=stage, registry=registry)
    maintain_candidate = bool((decision.get("candidate_lifecycle") or {}).get("maintain_candidate"))
    candidate = {
        "name": stage.get("candidate_name") or stage_id,
        "candidate_type": (stage.get("candidate_type") if maintain_candidate else "reuse_reference") or "subskill_candidate",
        "stage_id": stage_id,
        "args": stage.get("args") or {},
        "preconditions": stage.get("preconditions") or [],
        "expected_effects": stage.get("expected_effects") or [],
        "observable_success": stage.get("observable_success") or [],
        "failure_modes": stage.get("failure_modes") or [],
        "required_primitives": stage.get("required_primitives") or [],
        "coverage": coverage,
        "skill_reuse_decision": decision,
        "promotion_status": "draft",
        "evidence_runs": [],
    }
    return {
        "stage_id": stage_id,
        "label": stage.get("label") or stage_id,
        "order": order,
        "skill_candidate": candidate,
        "coverage": coverage,
        "skill_reuse_decision": decision,
    }


def assess_stage_coverage(*, stage_id: str, stage: dict[str, Any], registry: Any) -> dict[str, Any]:
    skill_paths = set(getattr(registry, "skill_paths", set()) or set())
    ctrl = set(str(item) for item in getattr(registry, "ctrl", []) or [])
    info = set(str(item) for item in getattr(registry, "info", []) or [])
    required = [str(item) for item in stage.get("required_primitives") or []]
    available = []
    missing = []
    for primitive in required:
        if primitive.startswith("/ctrl/"):
            name = primitive.split("/")[-1]
            (available if name in ctrl else missing).append(primitive)
        elif primitive.startswith("/info/"):
            name = primitive.split("/")[-1]
            (available if name in info else missing).append(primitive)
        elif primitive.startswith("sim.eval.predicates."):
            available.append(primitive)
        elif primitive == "ksm_metadata_binding":
            available.append(primitive)
        elif primitive in skill_paths:
            available.append(primitive)
        else:
            missing.append(primitive)

    if stage_id == "bind_selected_object_container":
        status = "covered_by_ksm_metadata"
    elif stage_id == "pick_bottle":
        status = "covered_by_verifier_gated_skill" if "pickplace/semantic_pick.yaml" in skill_paths else "missing_native_skill"
    elif stage_id == "move_above_dustbin":
        status = "missing_native_skill"
    elif stage_id == "release_into_dustbin":
        status = "partially_covered_no_container_semantics" if any(path in skill_paths for path in ("pickplace/semantic_place.yaml", "pickplace/semantic_pickplace.yaml")) else "missing_native_skill"
    elif stage_id == "verify_inside":
        status = "covered_by_predicate"
    elif stage_id == "go_home":
        status = "covered_by_control" if "go_home" in ctrl else "missing_native_skill"
    else:
        status = "unknown"

    return {
        "status": status,
        "available_primitives": available,
        "missing_primitives": missing,
        "notes": coverage_notes(stage_id, status),
    }


def coverage_notes(stage_id: str, status: str) -> list[str]:
    notes: list[str] = []
    if status == "covered_by_verifier_gated_skill":
        notes.append("Stage follows KW's existing self-verifying skill pattern; downstream stages may use its state only after the embedded verifier gate passes.")
    if status == "partially_covered_no_container_semantics":
        notes.append("Existing place/pickplace skill can release near a label, but does not guarantee container-interior placement.")
    if stage_id == "move_above_dustbin":
        notes.append("This stage needs an explicit container approach / over-receptacle primitive.")
    if stage_id == "verify_inside":
        notes.append("Covered as observable predicate, not as a motion primitive.")
    return notes


def build_workflow_candidate(
    *,
    task_family: str,
    selected_task: dict[str, Any],
    stage_records: list[dict[str, Any]],
    generated_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    manifest = generated_manifest or {}
    actions = []
    policy = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}
    if isinstance(policy.get("actions"), list):
        actions = [str(action) for action in policy["actions"]]
    return {
        "name": task_family,
        "candidate_type": "family_skill_candidate",
        "args": {
            "arm_id": "int",
            "source_label": "string",
            "container_label": "string",
            "source_ref": "string",
            "container_ref": "string",
        },
        "stage_sequence": [record["stage_id"] for record in stage_records],
        "current_executable_yaml": {
            "candidate_id": manifest.get("candidate_id"),
            "actions": actions,
            "classification": "workflow_candidate",
            "limitations": [
                "Current executable YAML collapses multiple stages into semantic_pickplace.",
                "Container-interior placement is not explicit.",
                "Verification is external predicate evidence, not embedded staged execution.",
            ],
        },
        "promotion_status": "draft",
        "evidence_runs": [],
    }


def build_gap_report(stage_records: list[dict[str, Any]]) -> dict[str, Any]:
    breakdown: dict[str, list[str]] = {}
    gaps: list[dict[str, Any]] = []
    for record in stage_records:
        status = str(record.get("coverage", {}).get("status") or "unknown")
        breakdown.setdefault(status, []).append(record["stage_id"])
        decision = record.get("skill_reuse_decision") or {}
        lifecycle = decision.get("candidate_lifecycle") if isinstance(decision.get("candidate_lifecycle"), dict) else {}
        if bool(lifecycle.get("maintain_candidate")) or status in {"missing_native_skill", "partially_covered", "partially_covered_no_container_semantics"}:
            gaps.append(
                {
                    "stage_id": record["stage_id"],
                    "status": status,
                    "decision": decision,
                    "missing_primitives": record.get("coverage", {}).get("missing_primitives") or [],
                    "notes": record.get("coverage", {}).get("notes") or [],
                }
            )
    return {
        "schema": "ksm.robodojo.skill_gap_report.v1",
        "coverage_breakdown": breakdown,
        "gaps": gaps,
        "next_recommended_focus": "container placement stages before full task-family promotion",
    }


def render_skill_candidate_report(artifacts: dict[str, Any]) -> str:
    lines = [
        "# RoboDojo Skill Candidate Extraction",
        "",
        f"- Task family: `{artifacts.get('task_family')}`",
        f"- Guide: `{artifacts.get('guide', {}).get('guide_id')}`",
        f"- Task: `{artifacts.get('task', {}).get('task_id')}`",
        f"- Source: `{artifacts.get('binding', {}).get('source_ref')}` / `{artifacts.get('binding', {}).get('source_label')}`",
        f"- Container: `{artifacts.get('binding', {}).get('container_ref')}` / `{artifacts.get('binding', {}).get('container_label')}`",
        "",
        "## Stages",
        "",
        "| Stage | Candidate | Coverage | Decision | Maintained Candidate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for record in artifacts.get("staged_plan") or []:
        candidate = record.get("skill_candidate") or {}
        coverage = record.get("coverage") or {}
        decision = record.get("skill_reuse_decision") or {}
        lifecycle = decision.get("candidate_lifecycle") if isinstance(decision.get("candidate_lifecycle"), dict) else {}
        lines.append(
            f"| `{record.get('stage_id')}` | `{candidate.get('name')}` | `{coverage.get('status')}` | "
            f"`{decision.get('decision')}` | `{bool(lifecycle.get('maintain_candidate'))}` |"
        )
    lines.extend(["", "## Gap Report", ""])
    for gap in artifacts.get("gap_report", {}).get("gaps", []) or []:
        lines.append(f"- `{gap.get('stage_id')}`: `{gap.get('status')}`")
    lines.append("")
    return "\n".join(lines)
