from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import safe_id, write_json, write_yaml


SCHEMA = "ksm.robodojo.stage_state_artifacts.v1"
RECORD_SCHEMA = "ksm.robodojo.stage_state_record.v1"


def build_stage_state_artifacts(
    *,
    skill_candidate_artifacts: dict[str, Any],
    suite_run: dict[str, Any] | None = None,
    before_frames: dict[str, Any] | None = None,
    after_frames: dict[str, Any] | None = None,
    generated_manifest: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a whole-workflow run onto the static RoboDojo stage guide.

    This does not claim stage-isolated execution. It records the current evidence
    boundary so later work can replace whole-workflow observations with true
    per-stage execution and prefix replay.
    """
    suite_payload = suite_run if isinstance(suite_run, dict) else {}
    episode_report = _first_episode_report(suite_payload)
    predicate_report = _predicate_report(episode_report)
    binding = _dict(skill_candidate_artifacts.get("binding"))
    task = _dict(skill_candidate_artifacts.get("task"))
    guide = _dict(skill_candidate_artifacts.get("guide"))
    final_candidate = _dict(skill_candidate_artifacts.get("final_workflow_candidate"))
    generated_payload = generated_manifest if isinstance(generated_manifest, dict) else {}
    package_payload = package if isinstance(package, dict) else {}
    stages = [
        stage
        for stage in skill_candidate_artifacts.get("staged_plan", []) or []
        if isinstance(stage, dict)
    ]

    records: list[dict[str, Any]] = []
    prefix_stage_ids: list[str] = []
    for index, stage in enumerate(stages):
        stage_id = str(stage.get("stage_id") or f"stage_{index + 1}")
        prefix_stage_ids.append(stage_id)
        records.append(
            build_stage_state_record(
                task_family=str(skill_candidate_artifacts.get("task_family") or ""),
                guide=guide,
                task=task,
                binding=binding,
                stage=stage,
                stage_order=int(stage.get("order") or index + 1),
                prefix_stage_ids=list(prefix_stage_ids),
                final_candidate=final_candidate,
                episode_report=episode_report,
                predicate_report=predicate_report,
                before_frames=before_frames,
                after_frames=after_frames,
                generated_manifest=generated_payload,
                package=package_payload,
            )
        )

    summary = summarize_stage_records(records=records, suite_run=suite_payload, episode_report=episode_report)
    return {
        "schema": SCHEMA,
        "task_family": skill_candidate_artifacts.get("task_family"),
        "guide": guide,
        "task": task,
        "binding": binding,
        "summary": summary,
        "records": records,
    }


def build_stage_state_record(
    *,
    task_family: str,
    guide: dict[str, Any],
    task: dict[str, Any],
    binding: dict[str, Any],
    stage: dict[str, Any],
    stage_order: int,
    prefix_stage_ids: list[str],
    final_candidate: dict[str, Any],
    episode_report: dict[str, Any],
    predicate_report: dict[str, Any],
    before_frames: dict[str, Any] | None,
    after_frames: dict[str, Any] | None,
    generated_manifest: dict[str, Any],
    package: dict[str, Any],
) -> dict[str, Any]:
    candidate = _dict(stage.get("skill_candidate"))
    coverage = _dict(stage.get("coverage"))
    stage_id = str(stage.get("stage_id") or candidate.get("stage_id") or "")
    outcome = stage_outcome(
        stage_id=stage_id,
        binding=binding,
        coverage=coverage,
        episode_report=episode_report,
        predicate_report=predicate_report,
    )
    return {
        "schema": RECORD_SCHEMA,
        "task_family": task_family,
        "guide": {
            "guide_id": guide.get("guide_id"),
            "source": guide.get("source"),
            "path": guide.get("path"),
        },
        "task": task,
        "stage": {
            "order": stage_order,
            "stage_id": stage_id,
            "label": stage.get("label") or stage_id,
            "candidate_name": candidate.get("name"),
            "candidate_type": candidate.get("candidate_type"),
            "coverage": coverage,
            "outcome": outcome,
        },
        "object_binding": binding,
        "logical_state": {
            "predicates": predicate_state(predicate_report),
            "frames": {
                "before": frame_paths(before_frames),
                "after": frame_paths(after_frames),
            },
        },
        "replay": {
            "mode": "prefix_replay_plan",
            "prefix_stage_ids": prefix_stage_ids,
            "executable_now": False,
            "reason": "Current KSM run executes one continuous YAML workflow; stage-isolated replay is not implemented yet.",
        },
        "workflow_context": {
            "final_workflow_candidate": final_candidate,
            "generated_candidate_id": generated_manifest.get("candidate_id"),
            "generated_actions": _generated_actions(generated_manifest),
            "candidate_package": package.get("package_dir"),
            "candidate_skill": package.get("skill_path"),
        },
        "evidence": evidence_context(
            episode_report=episode_report,
            predicate_report=predicate_report,
            before_frames=before_frames,
            after_frames=after_frames,
        ),
        "next_action": next_action(stage_id=stage_id, coverage=coverage, outcome=outcome),
    }


def write_stage_state_artifacts(output_dir: str | Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    records = [record for record in artifacts.get("records", []) or [] if isinstance(record, dict)]
    records_path = write_json(root / "stage_state_records.json", artifacts)
    summary_path = write_json(root / "state_summary.json", artifacts.get("summary") or {})
    stage_paths: dict[str, dict[str, str]] = {}
    for record in records:
        stage = _dict(record.get("stage"))
        order = int(stage.get("order") or 0)
        stage_id = str(stage.get("stage_id") or f"stage_{order}")
        stage_dir = root / f"{order:02d}_{safe_id(stage_id)}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        state_record = write_json(stage_dir / "state_record.json", record)
        prefix_replay = write_yaml(stage_dir / "prefix_replay.yaml", prefix_replay_payload(record))
        stage_paths[stage_id] = {
            "state_record": str(state_record),
            "prefix_replay": str(prefix_replay),
        }
    report_path = root / "README.md"
    report_path.write_text(render_stage_state_report(artifacts), encoding="utf-8")
    return {
        "records_dir": str(root),
        "stage_state_records": str(records_path),
        "state_summary": str(summary_path),
        "report": str(report_path),
        "stage_records": stage_paths,
    }


def stage_outcome(
    *,
    stage_id: str,
    binding: dict[str, Any],
    coverage: dict[str, Any],
    episode_report: dict[str, Any],
    predicate_report: dict[str, Any],
) -> dict[str, Any]:
    execute = bool(_dict(episode_report.get("metadata")).get("execute"))
    pipeline_success = episode_report.get("pipeline_success")
    predicate_success = episode_report.get("predicate_success")
    coverage_status = str(coverage.get("status") or "unknown")
    promotion_blocker = coverage_status in {
        "missing_native_skill",
        "partially_covered",
        "partially_covered_no_container_semantics",
    }

    if stage_id == "bind_selected_object_container":
        bound = bool(binding.get("source_ref") and binding.get("container_ref"))
        execution_status = "observed_passed" if bound else "observed_failed"
        observation_source = "ksm_metadata_binding"
    elif not execute:
        execution_status = "planned_not_executed"
        observation_source = "static_stage_guide"
    elif stage_id == "verify_inside":
        inside = predicate_state(predicate_report).get("inside")
        if inside is True:
            execution_status = "observed_passed"
        elif inside is False or predicate_success is False:
            execution_status = "observed_failed"
        else:
            execution_status = "not_observed"
        observation_source = "predicate_report"
    elif pipeline_success is True:
        execution_status = "workflow_executed_not_stage_isolated"
        observation_source = "whole_yaml_workflow"
    elif pipeline_success is False:
        execution_status = "not_reached_or_failed_with_workflow"
        observation_source = "whole_yaml_workflow"
    else:
        execution_status = "not_observed"
        observation_source = "suite_report"

    category = None
    if stage_id == "verify_inside" and execution_status == "observed_failed":
        category = "predicate"
    elif promotion_blocker:
        category = "primitive_gap"
    elif execution_status == "observed_failed":
        category = "binding"

    return {
        "execution_status": execution_status,
        "observation_source": observation_source,
        "promotion_blocker": promotion_blocker,
        "blocker_category": category,
        "coverage_status": coverage_status,
        "pipeline_success": pipeline_success,
        "predicate_success": predicate_success,
        "notes": outcome_notes(stage_id=stage_id, coverage=coverage, execution_status=execution_status),
    }


def predicate_state(predicate_report: dict[str, Any]) -> dict[str, Any]:
    inside: bool | None = None
    robot_home: bool | None = None
    for item in predicate_report.get("predicates", []) or []:
        if not isinstance(item, dict):
            continue
        predicate_type = str(item.get("type") or "")
        if predicate_type == "inside" and item.get("supported"):
            inside = bool(item.get("success"))
        elif predicate_type == "robot_home" and item.get("supported"):
            robot_home = bool(item.get("success"))
    return {
        "holding": None,
        "inside": inside,
        "robot_home": robot_home,
        "predicate_report_status": predicate_report.get("status"),
    }


def summarize_stage_records(
    *,
    records: list[dict[str, Any]],
    suite_run: dict[str, Any],
    episode_report: dict[str, Any],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    blockers: list[dict[str, Any]] = []
    first_failed_stage: str | None = None
    for record in records:
        stage = _dict(record.get("stage"))
        outcome = _dict(stage.get("outcome"))
        status = str(outcome.get("execution_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if outcome.get("promotion_blocker"):
            blockers.append(
                {
                    "stage_id": stage.get("stage_id"),
                    "coverage_status": outcome.get("coverage_status"),
                    "blocker_category": outcome.get("blocker_category"),
                }
            )
        if first_failed_stage is None and status == "observed_failed":
            first_failed_stage = str(stage.get("stage_id") or "")
    return {
        "schema": "ksm.robodojo.stage_state_summary.v1",
        "stage_count": len(records),
        "execution_status_counts": status_counts,
        "first_observed_failed_stage": first_failed_stage,
        "promotion_blockers": blockers,
        "suite_run_dir": suite_run.get("run_dir"),
        "pipeline_success": episode_report.get("pipeline_success"),
        "predicate_success": episode_report.get("predicate_success"),
        "interpretation": "Stage records are evidence-indexed projections of the current whole-workflow run, not true stage-isolated execution.",
    }


def frame_paths(frames_payload: dict[str, Any] | None) -> list[str]:
    payload = frames_payload if isinstance(frames_payload, dict) else {}
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return []
    return [str(item.get("path")) for item in frames if isinstance(item, dict) and item.get("path")]


def evidence_context(
    *,
    episode_report: dict[str, Any],
    predicate_report: dict[str, Any],
    before_frames: dict[str, Any] | None,
    after_frames: dict[str, Any] | None,
) -> dict[str, Any]:
    artifacts = _dict(episode_report.get("artifacts"))
    return {
        "episode_dir": artifacts.get("episode_dir"),
        "predicate_report": artifacts.get("predicate_report"),
        "predicate_report_inline": predicate_report,
        "before_frames": frame_paths(before_frames),
        "after_frames": frame_paths(after_frames),
        "failure_signature": episode_report.get("failure_signature"),
        "failure_analysis": episode_report.get("failure_analysis"),
    }


def prefix_replay_payload(record: dict[str, Any]) -> dict[str, Any]:
    replay = _dict(record.get("replay"))
    workflow_context = _dict(record.get("workflow_context"))
    return {
        "schema": "ksm.robodojo.prefix_replay_plan.v1",
        "task_family": record.get("task_family"),
        "task": record.get("task"),
        "stage": _dict(record.get("stage")),
        "prefix_stage_ids": replay.get("prefix_stage_ids") or [],
        "executable_now": False,
        "reason": replay.get("reason"),
        "current_whole_workflow": {
            "candidate_id": workflow_context.get("generated_candidate_id"),
            "candidate_skill": workflow_context.get("candidate_skill"),
            "actions": workflow_context.get("generated_actions") or [],
        },
    }


def render_stage_state_report(artifacts: dict[str, Any]) -> str:
    summary = _dict(artifacts.get("summary"))
    lines = [
        "# RoboDojo Staged State Records",
        "",
        f"- Task family: `{artifacts.get('task_family')}`",
        f"- Task: `{_dict(artifacts.get('task')).get('task_id')}`",
        f"- Pipeline success: `{summary.get('pipeline_success')}`",
        f"- Predicate success: `{summary.get('predicate_success')}`",
        f"- First observed failed stage: `{summary.get('first_observed_failed_stage')}`",
        "",
        "## Stage Outcomes",
        "",
        "| Stage | Execution Status | Coverage | Promotion Blocker |",
        "| --- | --- | --- | --- |",
    ]
    for record in artifacts.get("records", []) or []:
        stage = _dict(record.get("stage"))
        outcome = _dict(stage.get("outcome"))
        lines.append(
            f"| `{stage.get('stage_id')}` | `{outcome.get('execution_status')}` | `{outcome.get('coverage_status')}` | `{outcome.get('promotion_blocker')}` |"
        )
    lines.extend(["", "## Interpretation", "", str(summary.get("interpretation") or ""), ""])
    return "\n".join(lines)


def next_action(*, stage_id: str, coverage: dict[str, Any], outcome: dict[str, Any]) -> str:
    if stage_id == "move_above_dustbin":
        return "Define a native move_object_above_container candidate with explicit receptacle/opening targeting."
    if stage_id == "release_into_dustbin":
        return "Define a native release_into_container candidate and verify with inside(object, container)."
    if stage_id == "verify_inside" and outcome.get("execution_status") == "observed_failed":
        return "Inspect final frames and predicate geometry, then revise approach/release stages before promoting the family skill."
    if outcome.get("promotion_blocker"):
        return "Replace whole-workflow coverage with a stage-isolated subskill candidate before promotion."
    return "Collect more evidence runs or replace whole-workflow observation with stage-isolated execution."


def outcome_notes(*, stage_id: str, coverage: dict[str, Any], execution_status: str) -> list[str]:
    notes: list[str] = []
    if execution_status == "workflow_executed_not_stage_isolated":
        notes.append("Observed only through the current continuous YAML workflow.")
    for note in coverage.get("notes", []) or []:
        if isinstance(note, str):
            notes.append(note)
    return notes


def _first_episode_report(suite_run: dict[str, Any]) -> dict[str, Any]:
    episodes = suite_run.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return {}
    first = episodes[0]
    if not isinstance(first, dict):
        return {}
    report = first.get("report")
    return report if isinstance(report, dict) else {}


def _predicate_report(episode_report: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict(episode_report.get("metadata"))
    report = metadata.get("predicate_report")
    return report if isinstance(report, dict) else {}


def _generated_actions(generated_manifest: dict[str, Any]) -> list[str]:
    policy = generated_manifest.get("policy")
    if not isinstance(policy, dict):
        return []
    actions = policy.get("actions")
    if not isinstance(actions, list):
        return []
    return [str(action) for action in actions]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
