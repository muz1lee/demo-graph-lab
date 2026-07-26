from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .artifacts import new_run_dir
from .config import ManagerConfig
from .evaluation import analyze_episode_failure, summarize_episode_reports
from .failure import classify_failure, skill_success
from .feedback_attribution import analyze_episode_report, build_feedback_memory, build_task_analysis_state
from .grounding import preflight_runtime_skill_args
from .io import safe_id, write_json
from .leaderboard import markdown_leaderboard, scan_episode_reports
from .policy import check_skill
from .predicate import evaluate_task_predicates
from .publisher import PublishResult, publish_skill
from .registry import build_registry
from .runner import RunResult, run_published_skill
from .suite import SuiteCandidateRef, SuiteSpec, SuiteTask, load_suite_spec
from .trace import add_episode_event, add_visual_evidence_event, pipeline_status_to_trace
from .trace_analysis import write_trace_analysis
from .visual_feedback import build_visual_evidence


@dataclass(frozen=True)
class SuiteEpisodeResult:
    episode_id: str
    task_id: str
    candidate_id: str
    episode_dir: str
    report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuiteRunResult:
    suite_id: str
    suite_path: str
    run_dir: str
    execute: bool
    publish: bool
    success: bool
    structural_ok: bool
    success_rate: float
    task_success_rate: float
    policy_ok_rate: float
    pipeline_success_rate: float
    effect_success_rate: float | None
    predicate_success_rate: float | None
    episodes: list[dict[str, Any]]
    leaderboard: dict[str, Any]
    evaluation_summary: dict[str, Any]
    feedback_memory: dict[str, Any]
    task_analysis_state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_suite(
    *,
    config: ManagerConfig,
    suite_path: str | Path,
    execute: bool = False,
    publish: bool = True,
    reset_before_execute: bool = True,
) -> SuiteRunResult:
    if execute and not publish:
        raise ValueError("--execute requires publishing candidates into the KW-visible test skill folder")
    suite = load_suite_spec(suite_path)
    registry = build_registry(config)
    suite_dir = new_run_dir(suite.output_root, safe_id(suite.suite_id))
    episodes: list[SuiteEpisodeResult] = []

    index = 0
    for task in suite.tasks:
        for candidate in suite.candidates:
            index += 1
            episode_id = safe_id(f"{suite.suite_id}_{task.task_id}_{candidate.candidate_id}")
            episode_dir = suite_dir / "episodes" / f"{index:03d}_{safe_id(task.task_id)}__{safe_id(candidate.candidate_id)}"
            episode_dir.mkdir(parents=True, exist_ok=False)
            episode = _run_suite_episode(
                config=config,
                suite=suite,
                task=task,
                candidate=candidate,
                episode_id=episode_id,
                episode_dir=episode_dir,
                execute=execute,
                publish=publish,
                reset_before_execute=reset_before_execute,
                registry=registry,
            )
            episodes.append(episode)

    reports = [episode.report for episode in episodes]
    total = len(reports)
    success_count = sum(1 for report in reports if bool(report.get("success")))
    task_success_count = sum(1 for report in reports if bool(report.get("task_success")))
    policy_ok_count = sum(1 for report in reports if bool(report.get("policy_ok")))
    pipeline_success_count = sum(1 for report in reports if bool(report.get("pipeline_success")))
    predicate_reports = [report for report in reports if report.get("predicate_success") is not None]
    predicate_success_count = sum(1 for report in predicate_reports if bool(report.get("predicate_success")))
    effect_reports = [report for report in reports if report.get("effect_success") is not None]
    effect_success_count = sum(1 for report in effect_reports if bool(report.get("effect_success")))
    success_rate = success_count / total if total else 0.0
    task_success_rate = task_success_count / total if total else 0.0
    policy_ok_rate = policy_ok_count / total if total else 0.0
    pipeline_success_rate = pipeline_success_count / total if total else 0.0
    effect_success_rate = effect_success_count / len(effect_reports) if effect_reports else None
    predicate_success_rate = predicate_success_count / len(predicate_reports) if predicate_reports else None
    structural_ok = bool(total and policy_ok_count == total)
    success = bool(execute and total and success_rate >= suite.success_threshold)
    evaluation_summary = summarize_episode_reports(reports)
    feedback_memory = build_feedback_memory(reports)
    leaderboard = scan_episode_reports(suite_dir)
    task_analysis_state = build_task_analysis_state(
        suite_id=suite.suite_id,
        task_ids=[task.task_id for task in suite.tasks],
        stage="suite_run",
        manifest_path=suite.manifest_path,
        run_dir=str(suite_dir),
        success_threshold=suite.success_threshold,
        reports=reports,
        candidates=leaderboard.get("candidates") if isinstance(leaderboard.get("candidates"), list) else [],
        feedback_memory=feedback_memory,
    )
    write_json(suite_dir / "leaderboard.json", leaderboard)
    (suite_dir / "leaderboard.md").write_text(markdown_leaderboard(leaderboard), encoding="utf-8")
    write_json(suite_dir / "feedback_memory.json", feedback_memory)
    write_json(suite_dir / "task_analysis_state.json", task_analysis_state)
    result = SuiteRunResult(
        suite_id=suite.suite_id,
        suite_path=suite.manifest_path,
        run_dir=str(suite_dir),
        execute=execute,
        publish=publish,
        success=success,
        structural_ok=structural_ok,
        success_rate=success_rate,
        task_success_rate=task_success_rate,
        policy_ok_rate=policy_ok_rate,
        pipeline_success_rate=pipeline_success_rate,
        effect_success_rate=effect_success_rate,
        predicate_success_rate=predicate_success_rate,
        episodes=[episode.to_dict() for episode in episodes],
        leaderboard=leaderboard,
        evaluation_summary=evaluation_summary,
        feedback_memory=feedback_memory,
        task_analysis_state=task_analysis_state,
    )
    payload = result.to_dict()
    write_json(suite_dir / "suite_report.json", payload)
    write_json(
        suite_dir / "evolution_manifest.json",
        {
            "schema": "ksm.aspire_kw.suite_run_manifest.v1",
            "suite": suite.to_dict(),
            "run": payload,
        },
    )
    return result


def _run_suite_episode(
    *,
    config: ManagerConfig,
    suite: SuiteSpec,
    task: SuiteTask,
    candidate: SuiteCandidateRef,
    episode_id: str,
    episode_dir: Path,
    execute: bool,
    publish: bool,
    reset_before_execute: bool,
    registry: Any,
) -> SuiteEpisodeResult:
    started_at = time.time()
    policy = check_skill(candidate.skill_path, registry)
    publish_result: PublishResult | None = None
    run_result: RunResult | None = None
    run_error: str | None = None
    runtime_args = runtime_skill_args(task=task, candidate=candidate)
    runtime_arg_preflight = preflight_runtime_skill_args(
        config=config,
        task_metadata=task.metadata,
        runtime_args=runtime_args,
        enabled=execute,
    )
    if isinstance(runtime_arg_preflight.get("skill_args"), dict):
        runtime_args = dict(runtime_arg_preflight["skill_args"])

    if policy.ok and publish:
        publish_result = publish_skill(
            config=config,
            candidate_id=episode_id,
            source_path=candidate.skill_path,
            publish_subdir=_publish_subdir_for_config(suite.publish_subdir, config),
        )
    if policy.ok and execute:
        if publish_result is None:
            run_error = "missing published skill"
        else:
            try:
                run_result = run_published_skill(
                    config=config,
                    skill_path=publish_result.pipeline_skill_path,
                    kwargs=runtime_args,
                    reset_before_run=bool(reset_before_execute and task.reset_layout),
                )
            except Exception as exc:
                run_error = repr(exc)

    skill_ok = skill_success(run_result.final_status) if run_result else False
    pipeline_success = bool(execute and policy.ok and skill_ok and run_error is None)
    predicate_eval = evaluate_task_predicates(
        config=config,
        task_path=task.task_path,
        predicates=task.predicates,
        output_dir=episode_dir,
    ) if execute else None
    predicate_report = predicate_eval.to_dict() if predicate_eval else {
        "schema": "ksm.aspire_kw.predicate_evaluation.v1",
        "status": "skipped",
        "predicate_success": None,
        "predicates": [],
        "evidence": {"reason": "suite was not executed"},
    }
    predicate_success = predicate_report.get("predicate_success")
    verifier_evidence = kw_verifier_evidence(
        task=task,
        candidate=candidate,
        run_result=run_result,
        pipeline_success=pipeline_success,
    )
    visual_evidence = build_visual_evidence(
        task_metadata=task.metadata,
        candidate_manifest=candidate.manifest,
        episode_dir=episode_dir,
    )
    visual_task_success = visual_evidence.get("task_success")
    if visual_task_success is None:
        analysis = visual_evidence.get("analysis") if isinstance(visual_evidence.get("analysis"), dict) else {}
        visual_task_success = analysis.get("task_success")
    trace = pipeline_status_to_trace(run_result.final_status if run_result else {})
    trace = add_episode_event(
        trace,
        label="episode_runtime_summary",
        payload={
            "execute": execute,
            "policy_ok": policy.ok,
            "pipeline_success": pipeline_success,
            "skill_success": skill_ok,
            "run_error": run_error,
            "runtime_args": runtime_args,
        },
        status="ok" if pipeline_success else "failed" if execute else "dry_run",
    )
    trace = add_visual_evidence_event(trace, visual_evidence)
    trace_path = episode_dir / "aspire_trace.json"
    write_json(trace_path, trace)
    trace_analysis = write_trace_analysis(trace, episode_dir / "trace_analysis.json")
    outcome = episode_outcome(
        pipeline_success=pipeline_success,
        predicate_success=predicate_success,
        verifier_success=verifier_evidence.get("success"),
        verifier_status=verifier_evidence.get("status"),
    )
    success = bool(outcome["task_success"])
    failure_signature = classify_failure(
        policy_ok=policy.ok,
        execute=execute,
        run_result=run_result,
        run_error=run_error,
    )
    if pipeline_success and predicate_success is False:
        failure_signature = "predicate_failed"
    elif pipeline_success and not success and outcome.get("failure_signature"):
        failure_signature = str(outcome["failure_signature"])
    failure_analysis = analyze_episode_failure(
        failure_signature=failure_signature,
        policy_ok=policy.ok,
        execute=execute,
        run_result=run_result,
        run_error=run_error,
    )
    evaluator_report = build_evaluator_report(
        task=task,
        candidate=candidate,
        predicate_report=predicate_report,
        verifier_evidence=verifier_evidence,
        outcome=outcome,
        policy_ok=policy.ok,
        pipeline_success=pipeline_success,
        skill_success=skill_ok,
        failure_signature=failure_signature,
    )
    evaluator_path = episode_dir / "evaluator_report.json"
    write_json(evaluator_path, evaluator_report)
    report = {
        "schema": "ksm.aspire_kw.episode_report.v1",
        "suite_id": suite.suite_id,
        "episode_id": episode_id,
        "candidate_id": candidate.candidate_id,
        "task_id": task.task_id,
        "task_path": task.task_path,
        "success": success,
        "task_success": success,
        "execution_success": pipeline_success,
        "effect_success": outcome["effect_success"],
        "pipeline_success": pipeline_success,
        "skill_success": skill_ok,
        "predicate_success": predicate_success,
        "verifier_success": verifier_evidence.get("success"),
        "verification": {
            "source": outcome["verification_source"],
            "contract": verifier_evidence.get("contract"),
            "evidence": verifier_evidence,
        },
        "visual_task_success": visual_task_success,
        "policy_ok": policy.ok,
        "failure_signature": failure_signature,
        "failure_analysis": failure_analysis,
        "duration_s": time.time() - started_at,
        "artifacts": {
            "candidate_package": candidate.package_dir,
            "candidate_skill": candidate.skill_path,
            "published_skill": publish_result.published_path if publish_result else None,
            "episode_dir": str(episode_dir),
            "predicate_report": str(episode_dir / "predicate_report.json"),
            "evaluator_report": str(evaluator_path),
            "trace": str(trace_path),
            "trace_analysis": str(episode_dir / "trace_analysis.json"),
        },
        "metadata": {
            "execute": execute,
            "publish": publish,
            "publish_subdir": _publish_subdir_for_config(suite.publish_subdir, config),
            "task_args": task.skill_args,
            "candidate_skill_args": candidate.manifest.get("skill_args") if isinstance(candidate.manifest.get("skill_args"), dict) else {},
            "runtime_args": runtime_args,
            "runtime_arg_preflight": runtime_arg_preflight,
            "predicates": task.predicates,
            "predicate_report": predicate_report,
            "verifier_evidence": verifier_evidence,
            "evaluator_report": evaluator_report,
            "visual_evidence": visual_evidence,
            "trace_analysis": trace_analysis,
            "candidate_manifest": candidate.manifest,
            "policy": policy.to_dict(),
            "publish_result": publish_result.to_dict() if publish_result else None,
            "run_result": run_result.to_dict() if run_result else None,
            "run_error": run_error,
        },
    }
    feedback_attribution = analyze_episode_report(report)
    report["feedback_attribution"] = feedback_attribution
    feedback_path = episode_dir / "feedback_attribution.json"
    write_json(feedback_path, feedback_attribution)
    report["artifacts"]["feedback_attribution"] = str(feedback_path)
    agent_feedback_path = episode_dir / "agent_feedback.json"
    write_json(agent_feedback_path, feedback_attribution.get("agent_feedback") or {})
    report["artifacts"]["agent_feedback"] = str(agent_feedback_path)
    write_json(episode_dir / "episode_report.json", report)
    return SuiteEpisodeResult(
        episode_id=episode_id,
        task_id=task.task_id,
        candidate_id=candidate.candidate_id,
        episode_dir=str(episode_dir),
        report=report,
    )


def build_evaluator_report(
    *,
    task: SuiteTask,
    candidate: SuiteCandidateRef,
    predicate_report: dict[str, Any],
    verifier_evidence: dict[str, Any],
    outcome: dict[str, Any],
    policy_ok: bool,
    pipeline_success: bool,
    skill_success: bool,
    failure_signature: str,
) -> dict[str, Any]:
    """Evaluator-only task/effect evidence kept out of agent repair prompts."""

    return {
        "schema": "ksm.aspire_kw.evaluator_report.v1",
        "source_policy": "evaluator_only_not_for_agent_prompt",
        "task_id": task.task_id,
        "candidate_id": candidate.candidate_id,
        "policy_ok": bool(policy_ok),
        "pipeline_success": bool(pipeline_success),
        "skill_success": bool(skill_success),
        "task_success": bool(outcome.get("task_success")),
        "effect_success": outcome.get("effect_success"),
        "verification_source": outcome.get("verification_source"),
        "failure_signature": failure_signature,
        "predicate_report": predicate_report,
        "verifier_evidence": verifier_evidence,
    }


def _publish_subdir_for_config(publish_subdir: str, config: ManagerConfig) -> str | None:
    value = str(publish_subdir or "").strip("/")
    prefix = str(config.test_skill_dir or "").strip("/")
    if not value:
        return None
    if prefix and value == prefix:
        return None
    if prefix and value.startswith(f"{prefix}/"):
        return value[len(prefix) + 1 :]
    return value


def runtime_skill_args(*, task: SuiteTask, candidate: SuiteCandidateRef) -> dict[str, Any]:
    args = dict(task.skill_args)
    candidate_args = candidate.manifest.get("skill_args")
    if isinstance(candidate_args, dict):
        args.update(candidate_args)
    return args


def episode_outcome(
    *,
    pipeline_success: bool,
    predicate_success: bool | None,
    verifier_success: Any,
    verifier_status: str | None = None,
) -> dict[str, Any]:
    if not pipeline_success:
        return {
            "task_success": False,
            "effect_success": False,
            "verification_source": "pipeline",
            "failure_signature": None,
        }
    if predicate_success is not None:
        return {
            "task_success": bool(predicate_success),
            "effect_success": bool(predicate_success),
            "verification_source": "kw_predicate",
            "failure_signature": "predicate_failed" if not predicate_success else None,
        }
    if verifier_success is not None:
        failure_signature = "verifier_failed" if not verifier_success else None
        if not verifier_success and verifier_status == "inconclusive":
            failure_signature = "verifier_inconclusive"
        return {
            "task_success": bool(verifier_success),
            "effect_success": bool(verifier_success),
            "verification_source": "kw_verifier",
            "failure_signature": failure_signature,
        }
    return {
        "task_success": False,
        "effect_success": None,
        "verification_source": "missing_effect_feedback",
        "failure_signature": "effect_feedback_missing",
    }


def kw_verifier_evidence(
    *,
    task: SuiteTask,
    candidate: SuiteCandidateRef,
    run_result: RunResult | None,
    pipeline_success: bool,
) -> dict[str, Any]:
    contract = verifier_contract(task=task, candidate=candidate)
    if not contract.get("enabled"):
        return {
            "success": None,
            "status": "not_declared",
            "source": "none",
            "contract": contract,
            "reason": "task did not declare a KW verifier-gated evaluation contract",
        }
    actions = action_statuses(run_result.final_status if run_result else {})
    declared_actions = [
        str(action)
        for action in contract.get("verifier_actions", []) or []
        if str(action).strip()
    ]
    verifier_actions = [
        item
        for item in actions
        if _is_declared_verifier_action(item.get("action"), declared_actions)
        or "verifier" in str(item.get("action") or "").lower()
    ]
    success_actions = [
        item for item in actions if str(item.get("status") or "").lower() == "success"
    ]
    failed_actions = [
        item for item in actions if str(item.get("status") or "").lower() == "failed"
    ]
    missing_declared = [
        action
        for action in declared_actions
        if not any(_same_action(item.get("action"), action) for item in verifier_actions)
    ]
    non_success_verifier_actions = [
        item
        for item in verifier_actions
        if str(item.get("status") or "").lower() != "success"
    ]
    verifier_failed = any(
        str(item.get("status") or "").lower() == "failed"
        for item in verifier_actions
    )
    verifier_success = bool(
        pipeline_success
        and verifier_actions
        and not missing_declared
        and not non_success_verifier_actions
    )
    if not pipeline_success:
        verifier_status = "pipeline_failed"
        reason = "KW verifier-gated task contract but pipeline skill did not return success"
    elif verifier_success:
        verifier_status = "verified"
        reason = "declared KW verifier action returned success"
    elif verifier_failed:
        verifier_status = "failed"
        reason = "declared KW verifier action returned failed"
    else:
        verifier_status = "inconclusive"
        reason = "declared KW verifier action was missing or did not return explicit success"
    return {
        "success": verifier_success,
        "status": verifier_status,
        "source": "kw_verifier_gated_skill",
        "contract": contract,
        "reason": reason,
        "actions": actions,
        "declared_verifier_actions": declared_actions,
        "missing_declared_verifier_actions": missing_declared,
        "verifier_actions": verifier_actions,
        "non_success_verifier_actions": non_success_verifier_actions,
        "success_actions": success_actions,
        "failed_actions": failed_actions,
    }


def verifier_contract(*, task: SuiteTask, candidate: SuiteCandidateRef) -> dict[str, Any]:
    task_payload = task.metadata.get("raw") if isinstance(task.metadata, dict) else {}
    contract = task_payload.get("verification") if isinstance(task_payload, dict) else None
    if isinstance(contract, dict) and str(contract.get("type") or "") == "kw_verifier":
        return {"enabled": True, **contract}

    manifest_metadata = candidate.manifest.get("metadata")
    if isinstance(manifest_metadata, dict):
        candidate_contract = manifest_metadata.get("verification")
        if isinstance(candidate_contract, dict) and str(candidate_contract.get("type") or "") == "kw_verifier":
            return {"enabled": True, **candidate_contract}

    return {"enabled": False, "type": "none"}


def action_statuses(status: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            step = value.get("step")
            if isinstance(step, dict):
                action = step.get("action")
                state = value.get("status")
                if isinstance(action, str) or isinstance(state, str):
                    actions.append(
                        {
                            "action": action,
                            "status": state,
                            "description": step.get("description") or value.get("description"),
                            "action_type": value.get("action_type"),
                            "system_time": value.get("system_time"),
                        }
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(status)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for item in actions:
        key = (item.get("system_time"), item.get("action"), item.get("status"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _is_declared_verifier_action(action: Any, declared_actions: list[str]) -> bool:
    if not declared_actions:
        return False
    return any(_same_action(action, declared) for declared in declared_actions)


def _same_action(left: Any, right: str) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    return (
        left_text == right_text
        or left_text.endswith(f"/{right_text}")
        or right_text.endswith(f"/{left_text}")
        or Path(left_text).name == Path(right_text).name
    )
