from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .artifacts import new_run_dir
from .candidate import CandidatePackage, package_skill_candidate
from .capture import PeriodicKeyframeSampler, capture_keyframes
from .config import ManagerConfig
from .io import read_yaml, safe_id, write_json, write_yaml
from .registry import ToolRegistry, build_registry
from .suite_runner import SuiteRunResult, run_suite


SCHEMA = "ksm.robodojo.staged_experiment.v1"
STAGE_EVAL_SCHEMA = "ksm.aspire_kw.stage_evaluation.v1"
STATE_SCHEMA = "ksm.robodojo.stage_state_record.v2"

SuiteRunnerFn = Callable[..., Any]


@dataclass(frozen=True)
class StagedExperimentResult:
    run_dir: str
    task_family: str
    task: dict[str, Any]
    binding: dict[str, Any]
    selected_stage_ids: list[str]
    stage_results: list[dict[str, Any]]
    success: bool
    final_state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_staged_experiment(
    *,
    config: ManagerConfig,
    skill_candidate_artifacts: dict[str, Any],
    output_root: str | Path | None = None,
    stage_ids: list[str] | None = None,
    stop_after_stage: str | None = "pick_bottle",
    candidate_prefix: str = "robodojo_stage",
    parent_id: str | None = None,
    execute: bool = False,
    publish: bool = True,
    capture_artifacts: bool = True,
    registry: ToolRegistry | None = None,
    suite_runner_fn: SuiteRunnerFn = run_suite,
) -> StagedExperimentResult:
    registry = registry or build_registry(config)
    task = _dict(skill_candidate_artifacts.get("task"))
    binding = _dict(skill_candidate_artifacts.get("binding"))
    task_family = str(skill_candidate_artifacts.get("task_family") or "unknown_task_family")
    stages = _selected_stages(
        skill_candidate_artifacts.get("staged_plan") or [],
        stage_ids=stage_ids,
        stop_after_stage=stop_after_stage,
    )
    root = Path(output_root).expanduser().resolve() if output_root else config.root_dir / "experiments" / "robodojo_staged"
    run_dir = new_run_dir(root / "runs", safe_id(f"{candidate_prefix}_{task.get('task_id') or task_family}"))
    write_json(run_dir / "staged_plan_input.json", skill_candidate_artifacts)

    stage_results: list[dict[str, Any]] = []
    successful_prefix: list[dict[str, Any]] = []
    previous_state = initial_state(task=task, binding=binding)
    gate_open = True
    previous_candidate_id = parent_id

    for index, stage in enumerate(stages, start=1):
        stage_id = str(stage.get("stage_id") or f"stage_{index}")
        stage_dir = run_dir / "stages" / f"{index:02d}_{safe_id(stage_id)}"
        if not gate_open:
            skipped = build_skipped_stage_result(
                stage=stage,
                stage_order=index,
                stage_dir=stage_dir,
                input_state=previous_state,
                parent_id=previous_candidate_id,
                reason="previous_stage_not_successful",
            )
            stage_results.append(skipped)
            write_stage_outputs(stage_dir, skipped)
            continue

        result = run_one_stage(
            config=config,
            registry=registry,
            task_family=task_family,
            task=task,
            binding=binding,
            stage=stage,
            stage_order=index,
            stage_dir=stage_dir,
            input_state=previous_state,
            prefix_records=successful_prefix,
            candidate_prefix=candidate_prefix,
            parent_id=previous_candidate_id,
            execute=execute,
            publish=publish,
            capture_artifacts=capture_artifacts,
            suite_runner_fn=suite_runner_fn,
        )
        stage_results.append(result)
        write_stage_outputs(stage_dir, result)
        lifecycle = ((result.get("skill_reuse_decision") or {}).get("candidate_lifecycle") or {})
        if result.get("candidate_id") and bool(lifecycle.get("maintain_candidate")):
            previous_candidate_id = str(result["candidate_id"])
        if result.get("success"):
            previous_state = _dict(result.get("output_state"))
            if result.get("stage_candidate", {}).get("skill_yaml"):
                successful_prefix.append(
                    {
                        "stage_id": stage_id,
                        "skill_yaml": result["stage_candidate"]["skill_yaml"],
                        "candidate_id": result.get("candidate_id"),
                    }
                )
        else:
            gate_open = False

    success = bool(stage_results and all(bool(item.get("success")) for item in stage_results if item.get("stage_status") != "skipped"))
    final_state = previous_state
    payload = {
        "schema": SCHEMA,
        "run_dir": str(run_dir),
        "task_family": task_family,
        "task": task,
        "binding": binding,
        "selected_stage_ids": [str(stage.get("stage_id") or "") for stage in stages],
        "success": success,
        "final_state": final_state,
        "stage_results": stage_results,
    }
    write_json(run_dir / "staged_experiment_report.json", payload)
    (run_dir / "README.md").write_text(render_staged_experiment_report(payload), encoding="utf-8")
    return StagedExperimentResult(
        run_dir=str(run_dir),
        task_family=task_family,
        task=task,
        binding=binding,
        selected_stage_ids=payload["selected_stage_ids"],
        stage_results=stage_results,
        success=success,
        final_state=final_state,
    )


def run_one_stage(
    *,
    config: ManagerConfig,
    registry: ToolRegistry,
    task_family: str,
    task: dict[str, Any],
    binding: dict[str, Any],
    stage: dict[str, Any],
    stage_order: int,
    stage_dir: Path,
    input_state: dict[str, Any],
    prefix_records: list[dict[str, Any]],
    candidate_prefix: str,
    parent_id: str | None,
    execute: bool,
    publish: bool,
    capture_artifacts: bool,
    suite_runner_fn: SuiteRunnerFn,
) -> dict[str, Any]:
    stage_id = str(stage.get("stage_id") or f"stage_{stage_order}")
    started_at = time.time()
    stage_dir.mkdir(parents=True, exist_ok=True)

    if stage_id == "bind_selected_object_container":
        success = bool(binding.get("source_ref") and binding.get("container_ref"))
        output_state = state_after_stage(
            input_state=input_state,
            stage_id=stage_id,
            success=success,
            binding=binding,
            predicates={},
        )
        return build_stage_result(
            task_family=task_family,
            task=task,
            binding=binding,
            stage=stage,
            stage_order=stage_order,
            stage_dir=stage_dir,
            input_state=input_state,
            output_state=output_state,
            parent_id=parent_id,
            candidate_id=None,
            stage_status="passed" if success else "failed",
            success=success,
            next_stage_allowed=success,
            failure_signature="success" if success else "binding_failed",
            failure_category="success" if success else "binding",
            duration_s=time.time() - started_at,
            suite_run=None,
            stage_candidate=None,
            prefix_replay=None,
            evidence={"binding": binding},
        )

    try:
        skill_payload = build_stage_skill_payload(
            stage_id=stage_id,
            candidate_id=safe_id(f"{candidate_prefix}_{stage_order:02d}_{stage_id}"),
            binding=binding,
            registry=registry,
        )
    except StageNotExecutable as exc:
        output_state = state_after_stage(
            input_state=input_state,
            stage_id=stage_id,
            success=False,
            binding=binding,
            predicates={},
        )
        return build_stage_result(
            task_family=task_family,
            task=task,
            binding=binding,
            stage=stage,
            stage_order=stage_order,
            stage_dir=stage_dir,
            input_state=input_state,
            output_state=output_state,
            parent_id=parent_id,
            candidate_id=None,
            stage_status="blocked",
            success=False,
            next_stage_allowed=False,
            failure_signature="primitive_gap",
            failure_category="primitive",
            duration_s=time.time() - started_at,
            suite_run=None,
            stage_candidate=None,
            prefix_replay=None,
            evidence={"reason": str(exc), "stage_id": stage_id},
        )

    generated_skill = write_yaml(stage_dir / "generated" / f"{skill_payload['name']}.yaml", skill_payload)
    package = package_stage_candidate(
        stage=stage,
        stage_id=stage_id,
        skill_yaml=generated_skill,
        stage_dir=stage_dir,
        registry=registry,
        parent_id=parent_id,
    )
    task_path = write_stage_task(stage_dir / "task.yaml", task=task, binding=binding, stage=stage, skill_payload=skill_payload)
    suite_path = write_stage_suite(stage_dir / "suite.yaml", task_path=task_path, package=package, stage_id=stage_id)
    before_frames = capture_keyframes(config, stage_dir / "frames_before", enabled=capture_artifacts and execute)
    sampler = PeriodicKeyframeSampler(config=config, output_dir=stage_dir / "frames_during", enabled=capture_artifacts and execute)
    sampler.start()
    try:
        suite_run = suite_runner_fn(
            config=config,
            suite_path=suite_path,
            execute=execute,
            publish=publish,
            reset_before_execute=stage_reset_before_execute(stage_id),
        )
    finally:
        sampler.stop()
    after_frames = capture_keyframes(config, stage_dir / "frames_after", enabled=capture_artifacts and execute)
    suite_payload = suite_run.to_dict() if hasattr(suite_run, "to_dict") else dict(suite_run)
    episode_report = first_episode_report(suite_payload)
    stage_success = stage_execution_success(stage_id=stage_id, execute=execute, episode_report=episode_report)
    output_state = state_after_stage(
        input_state=input_state,
        stage_id=stage_id,
        success=stage_success,
        binding=binding,
        predicates=stage_predicate_update(stage_id=stage_id, success=stage_success, execute=execute),
    )
    prefix_replay = write_prefix_replay(
        stage_dir=stage_dir,
        task_family=task_family,
        binding=binding,
        prefix_records=prefix_records + ([{"stage_id": stage_id, "skill_yaml": str(generated_skill), "candidate_id": package.candidate_id}] if stage_success else []),
    )
    failure_signature = str(episode_report.get("failure_signature") or ("success" if stage_success else "stage_failed"))
    failure_analysis = _dict(episode_report.get("failure_analysis"))
    return build_stage_result(
        task_family=task_family,
        task=task,
        binding=binding,
        stage=stage,
        stage_order=stage_order,
        stage_dir=stage_dir,
        input_state=input_state,
        output_state=output_state,
        parent_id=parent_id,
        candidate_id=package.candidate_id,
        stage_status="passed" if stage_success else ("dry_run" if not execute else "failed"),
        success=stage_success,
        next_stage_allowed=stage_success,
        failure_signature=failure_signature,
        failure_category=str(failure_analysis.get("category") or ("success" if stage_success else "unknown")),
        duration_s=time.time() - started_at,
        suite_run=suite_payload,
        stage_candidate={
            "candidate_id": package.candidate_id,
            "skill_yaml": str(generated_skill),
            "package": package.to_dict(),
            "suite_path": str(suite_path),
            "task_path": str(task_path),
            "reset_before_execute": stage_reset_before_execute(stage_id),
            "skill_reuse_decision": _dict(stage.get("skill_reuse_decision")),
        },
        prefix_replay=prefix_replay,
        evidence={
            "before_frames": before_frames,
            "after_frames": after_frames,
            "episode_report": episode_report,
        },
    )


def build_stage_skill_payload(
    *,
    stage_id: str,
    candidate_id: str,
    binding: dict[str, Any],
    registry: ToolRegistry,
) -> dict[str, Any]:
    if stage_id == "pick_bottle":
        if "pickplace/semantic_pick.yaml" in registry.skill_paths:
            return {
                "schema_version": "1.0.0",
                "name": candidate_id,
                "description": "KSM staged candidate: pick the selected source object.",
                "return": "= pick_success",
                "args": {
                    "arm_id": 0,
                    "pick_label": binding.get("source_label") or "bottle:dof",
                    "direct_pick": True,
                    "use_motion_planning": False,
                    "pick_check_offset": [0.0, 0.0, 0.07],
                    "delay_sec": 0.8,
                },
                "workflow": [
                    {
                        "action": "pickplace/semantic_pick.yaml",
                        "description": "pick selected source object",
                        "output": "pick_success",
                        "args": {
                            "arm_id": "= args.arm_id",
                            "pick_label": "= args.pick_label",
                            "direct_pick": "= args.direct_pick",
                            "use_motion_planning": "= args.use_motion_planning",
                            "pick_check_offset": "= args.pick_check_offset",
                            "delay_sec": "= args.delay_sec",
                        },
                    },
                    {"assert": "= pick_success", "message": "Pick stage failed"},
                ],
            }
        if "pickplace/semantic_pickplace.yaml" in registry.skill_paths:
            return {
                "schema_version": "1.0.0",
                "name": candidate_id,
                "description": "KSM staged candidate: pick through semantic_pickplace with no place target.",
                "args": {
                    "arm_id": 0,
                    "pick_label": binding.get("source_label") or "bottle:dof",
                    "place_label": "",
                    "direct_pick": True,
                    "adjust_arm_id": False,
                    "use_motion_planning": False,
                },
                "workflow": [
                    {
                        "action": "pickplace/semantic_pickplace.yaml",
                        "description": "pick selected source object without placing",
                        "args": {
                            "arm_id": "= args.arm_id",
                            "pick_label": "= args.pick_label",
                            "place_label": "= args.place_label",
                            "direct_pick": "= args.direct_pick",
                            "adjust_arm_id": "= args.adjust_arm_id",
                            "use_motion_planning": "= args.use_motion_planning",
                        },
                    }
                ],
            }
        raise StageNotExecutable("registry lacks semantic pick or pickplace skill")

    if stage_id == "go_home":
        if "go_home" not in set(registry.ctrl):
            raise StageNotExecutable("registry lacks /ctrl/go_home")
        return {
            "schema_version": "1.0.0",
            "name": candidate_id,
            "description": "KSM staged candidate: return selected arm home.",
            "args": {"arm_id": 0},
            "workflow": [{"action": "/ctrl/go_home", "args": {"arm_id": "= args.arm_id"}}],
        }

    raise StageNotExecutable(f"stage is not executable by the current staged harness: {stage_id}")


def package_stage_candidate(
    *,
    stage: dict[str, Any],
    stage_id: str,
    skill_yaml: Path,
    stage_dir: Path,
    registry: ToolRegistry,
    parent_id: str | None,
) -> CandidatePackage:
    candidate = _dict(stage.get("skill_candidate"))
    decision = _dict(stage.get("skill_reuse_decision"))
    lifecycle = _dict(decision.get("candidate_lifecycle"))
    return package_skill_candidate(
        candidate_id=skill_yaml.stem,
        skill_yaml=skill_yaml,
        output_root=stage_dir / "packages",
        registry=registry,
        hypothesis=f"Stage `{stage_id}` can be solved by the generated KW YAML candidate.",
        change_summary=f"Generated from staged guide candidate `{candidate.get('name') or stage_id}`.",
        expected_failure_modes=[str(item) for item in candidate.get("failure_modes", []) or []],
        skill_args=_dict(read_yaml(skill_yaml).get("args")),
        parent_id=parent_id,
        overwrite=True,
        metadata={
            "stage_id": stage_id,
            "skill_reuse_decision": decision,
            "candidate_lifecycle": lifecycle,
            "verification": stage_verification_contract(stage),
            "runtime_wrapper_only": not bool(lifecycle.get("maintain_candidate")),
        },
    )


def write_stage_task(path: str | Path, *, task: dict[str, Any], binding: dict[str, Any], stage: dict[str, Any], skill_payload: dict[str, Any]) -> Path:
    verification = stage_verification_contract(stage)
    return write_yaml(
        path,
        {
            "task_id": safe_id(f"{task.get('task_id')}_{stage.get('stage_id')}"),
            "description": f"Staged RoboDojo task: {stage.get('stage_id')}",
            "args": _dict(skill_payload.get("args")),
            "predicates": [],
            "verification": verification,
            "reset_layout": stage_reset_before_execute(str(stage.get("stage_id") or "")),
            "robodojo": {
                "source_task": task,
                "binding": binding,
                "stage": {
                    "stage_id": stage.get("stage_id"),
                    "label": stage.get("label"),
                    "skill_candidate": stage.get("skill_candidate"),
                },
            },
        },
    )


def stage_verification_contract(stage: dict[str, Any]) -> dict[str, Any]:
    required = [str(item) for item in stage.get("required_primitives") or []]
    predicate_primitives = [
        item for item in required if item.startswith("sim.eval.predicates.")
    ]
    verifier_primitives = [
        item for item in required if "verifier" in item.lower()
    ]
    if predicate_primitives:
        return {
            "type": "kw_predicate",
            "required_primitives": predicate_primitives,
        }
    if verifier_primitives:
        return {
            "type": "kw_verifier",
            "required_primitives": verifier_primitives,
        }
    return {
        "type": "none",
        "required_primitives": required,
    }


def write_stage_suite(path: str | Path, *, task_path: Path, package: CandidatePackage, stage_id: str) -> Path:
    root = Path(path).parent
    return write_yaml(
        path,
        {
            "suite_id": safe_id(f"staged_{stage_id}_{package.candidate_id}"),
            "description": f"KSM staged execution suite for {stage_id}.",
            "output_root": str(root / "suite_runs"),
            "publish_subdir": safe_id(f"staged_{stage_id}"),
            "success_threshold": 1.0,
            "tasks": [{"task_path": str(task_path)}],
            "candidate_packages": [package.package_dir],
        },
    )


def write_prefix_replay(
    *,
    stage_dir: Path,
    task_family: str,
    binding: dict[str, Any],
    prefix_records: list[dict[str, Any]],
) -> dict[str, Any]:
    prefix_path = stage_dir / "prefix_replay.yaml"
    if not prefix_records:
        payload = {
            "schema": "ksm.robodojo.prefix_replay_plan.v2",
            "task_family": task_family,
            "binding": binding,
            "executable_now": False,
            "reason": "No executable successful prefix stages yet.",
            "prefix_stage_ids": [],
        }
        write_yaml(prefix_path, payload)
        return {"path": str(prefix_path), "executable_now": False, "prefix_stage_ids": []}

    args: dict[str, Any] = {}
    workflow: list[Any] = []
    stage_ids: list[str] = []
    for record in prefix_records:
        skill_path = Path(str(record.get("skill_yaml") or ""))
        if not skill_path.exists():
            continue
        skill = read_yaml(skill_path)
        if not isinstance(skill, dict):
            continue
        args.update(_dict(skill.get("args")))
        workflow.extend(skill.get("workflow") if isinstance(skill.get("workflow"), list) else [])
        stage_ids.append(str(record.get("stage_id") or skill_path.stem))
    payload = {
        "schema_version": "1.0.0",
        "name": safe_id(f"prefix_{task_family}_{'_'.join(stage_ids)}"),
        "description": "KSM generated prefix replay skill for staged RoboDojo execution.",
        "args": args,
        "workflow": workflow,
        "metadata": {
            "schema": "ksm.robodojo.prefix_replay_plan.v2",
            "task_family": task_family,
            "binding": binding,
            "prefix_stage_ids": stage_ids,
        },
    }
    write_yaml(prefix_path, payload)
    return {"path": str(prefix_path), "executable_now": bool(workflow), "prefix_stage_ids": stage_ids}


def build_stage_result(
    *,
    task_family: str,
    task: dict[str, Any],
    binding: dict[str, Any],
    stage: dict[str, Any],
    stage_order: int,
    stage_dir: Path,
    input_state: dict[str, Any],
    output_state: dict[str, Any],
    parent_id: str | None,
    candidate_id: str | None,
    stage_status: str,
    success: bool,
    next_stage_allowed: bool,
    failure_signature: str,
    failure_category: str,
    duration_s: float,
    suite_run: dict[str, Any] | None,
    stage_candidate: dict[str, Any] | None,
    prefix_replay: dict[str, Any] | None,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    stage_id = str(stage.get("stage_id") or "")
    decision = _dict(stage.get("skill_reuse_decision"))
    result = {
        "schema": STAGE_EVAL_SCHEMA,
        "task_family": task_family,
        "task": task,
        "binding": binding,
        "stage_id": stage_id,
        "stage_order": stage_order,
        "candidate_id": candidate_id,
        "parent_id": parent_id,
        "input_state_id": input_state.get("state_id"),
        "output_state_id": output_state.get("state_id"),
        "input_state": input_state,
        "output_state": output_state,
        "stage_status": stage_status,
        "success": success,
        "next_stage_allowed": next_stage_allowed,
        "failure_signature": failure_signature,
        "failure_category": failure_category,
        "duration_s": duration_s,
        "skill_reuse_decision": decision,
        "stage_candidate": stage_candidate or {},
        "prefix_replay": prefix_replay or {},
        "suite_run": suite_run,
        "evidence": evidence,
        "state_record": {
            "schema": STATE_SCHEMA,
            "stage_id": stage_id,
            "stage_order": stage_order,
            "state_id": output_state.get("state_id"),
            "input_state": input_state,
            "output_state": output_state,
            "object_binding": binding,
            "candidate_id": candidate_id,
            "skill_reuse_decision": decision,
            "success": success,
            "failure_signature": failure_signature,
            "evidence": evidence,
        },
    }
    result["aspire_feedback_packet"] = build_aspire_feedback_packet(result)
    return result


def build_skipped_stage_result(
    *,
    stage: dict[str, Any],
    stage_order: int,
    stage_dir: Path,
    input_state: dict[str, Any],
    parent_id: str | None,
    reason: str,
) -> dict[str, Any]:
    output_state = dict(input_state)
    output_state["blocked_stage_id"] = stage.get("stage_id")
    return build_stage_result(
        task_family="",
        task={},
        binding={},
        stage=stage,
        stage_order=stage_order,
        stage_dir=stage_dir,
        input_state=input_state,
        output_state=output_state,
        parent_id=parent_id,
        candidate_id=None,
        stage_status="skipped",
        success=False,
        next_stage_allowed=False,
        failure_signature=reason,
        failure_category="stage_gate",
        duration_s=0.0,
        suite_run=None,
        stage_candidate=None,
        prefix_replay=None,
        evidence={"reason": reason},
    )


def build_aspire_feedback_packet(stage_result: dict[str, Any]) -> dict[str, Any]:
    decision = _dict(stage_result.get("skill_reuse_decision"))
    lifecycle = _dict(decision.get("candidate_lifecycle"))
    maintain_candidate = bool(lifecycle.get("maintain_candidate"))
    success = bool(stage_result.get("success"))
    episode_report = _dict((_dict(stage_result.get("evidence")).get("episode_report")))
    if maintain_candidate:
        next_action = "collect_promotion_evidence" if success else str(decision.get("aspire_action") or "rewrite_or_create_subskill_candidate")
        output_contract = "maintain_or_rewrite_subskill_candidate"
    else:
        next_action = "reuse_skill_confirmed" if success else str(decision.get("aspire_action") or "iterate_reuse_binding_or_parameters")
        output_contract = "do_not_create_new_skill_candidate"
    return {
        "schema": "ksm.aspire_kw.stage_feedback_packet.v1",
        "stage_id": stage_result.get("stage_id"),
        "stage_order": stage_result.get("stage_order"),
        "task_family": stage_result.get("task_family"),
        "task": stage_result.get("task"),
        "binding": stage_result.get("binding"),
        "skill_reuse_decision": decision,
        "candidate_lifecycle": {
            "maintain_candidate": maintain_candidate,
            "promotion_eligible": bool(lifecycle.get("promotion_eligible")),
            "runtime_candidate_id": stage_result.get("candidate_id"),
            "output_contract": output_contract,
        },
        "execution_feedback": {
            "stage_status": stage_result.get("stage_status"),
            "success": success,
            "next_stage_allowed": stage_result.get("next_stage_allowed"),
            "failure_signature": stage_result.get("failure_signature"),
            "failure_category": stage_result.get("failure_category"),
            "input_state_id": stage_result.get("input_state_id"),
            "output_state_id": stage_result.get("output_state_id"),
            "episode_report": episode_report,
        },
        "aspire_next_action": next_action,
    }


def write_stage_outputs(stage_dir: Path, result: dict[str, Any]) -> None:
    stage_dir.mkdir(parents=True, exist_ok=True)
    write_json(stage_dir / "stage_evaluation.json", result)
    write_json(stage_dir / "state_record.json", result.get("state_record") or {})
    write_json(stage_dir / "aspire_feedback_packet.json", result.get("aspire_feedback_packet") or {})


def initial_state(*, task: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ksm.robodojo.logical_state.v1",
        "state_id": "initial",
        "task_id": task.get("task_id"),
        "stage_id": None,
        "binding": binding,
        "predicates": {"holding": None, "inside": None, "robot_home": None},
        "completed_stage_ids": [],
    }


def state_after_stage(
    *,
    input_state: dict[str, Any],
    stage_id: str,
    success: bool,
    binding: dict[str, Any],
    predicates: dict[str, Any],
) -> dict[str, Any]:
    state = dict(input_state)
    current_predicates = dict(_dict(state.get("predicates")))
    current_predicates.update(predicates)
    completed = list(state.get("completed_stage_ids") or [])
    if success and stage_id not in completed:
        completed.append(stage_id)
    state.update(
        {
            "schema": "ksm.robodojo.logical_state.v1",
            "state_id": f"after_{stage_id}" if success else f"failed_at_{stage_id}",
            "stage_id": stage_id,
            "binding": binding,
            "predicates": current_predicates,
            "completed_stage_ids": completed,
        }
    )
    return state


def stage_predicate_update(*, stage_id: str, success: bool, execute: bool) -> dict[str, Any]:
    if not execute:
        return {}
    if stage_id == "pick_bottle":
        return {"holding": bool(success)}
    if stage_id == "go_home":
        return {"robot_home": bool(success)}
    return {}


def stage_execution_success(*, stage_id: str, execute: bool, episode_report: dict[str, Any]) -> bool:
    if not execute:
        return False
    if stage_id in {"pick_bottle", "go_home"}:
        return bool(episode_report.get("pipeline_success") and episode_report.get("skill_success"))
    return bool(episode_report.get("success"))


def stage_reset_before_execute(stage_id: str) -> bool:
    return stage_id in {"pick_bottle"}


def first_episode_report(suite_run: dict[str, Any]) -> dict[str, Any]:
    episodes = suite_run.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return {}
    first = episodes[0]
    if not isinstance(first, dict):
        return {}
    report = first.get("report")
    return report if isinstance(report, dict) else {}


def render_staged_experiment_report(payload: dict[str, Any]) -> str:
    lines = [
        "# RoboDojo Staged Experiment",
        "",
        f"- Task family: `{payload.get('task_family')}`",
        f"- Task: `{_dict(payload.get('task')).get('task_id')}`",
        f"- Success: `{payload.get('success')}`",
        f"- Final state: `{_dict(payload.get('final_state')).get('state_id')}`",
        "",
        "| Stage | Status | Success | Next Allowed | Failure |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in payload.get("stage_results", []) or []:
        lines.append(
            f"| `{result.get('stage_id')}` | `{result.get('stage_status')}` | `{result.get('success')}` | `{result.get('next_stage_allowed')}` | `{result.get('failure_signature')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _selected_stages(raw_stages: list[Any], *, stage_ids: list[str] | None, stop_after_stage: str | None) -> list[dict[str, Any]]:
    stages = [stage for stage in raw_stages if isinstance(stage, dict)]
    if stage_ids:
        wanted = set(stage_ids)
        return [stage for stage in stages if str(stage.get("stage_id") or "") in wanted]
    if not stop_after_stage:
        return stages
    selected: list[dict[str, Any]] = []
    for stage in stages:
        selected.append(stage)
        if str(stage.get("stage_id") or "") == stop_after_stage:
            break
    return selected


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class StageNotExecutable(RuntimeError):
    pass
