from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent_skill_spec import task_new_skill_spec_prompt_payload
from .artifacts import new_run_dir
from .candidate import CandidatePackage, package_skill_candidate
from .config import ManagerConfig
from .failure import classify_failure
from .feedback_attribution import agent_safe_payload, build_task_analysis_state, merge_feedback_memories
from .generator import GeneratedSkill
from .io import read_yaml, safe_id, write_json, write_yaml
from .leaderboard import scan_episode_reports
from .llm_generator import generate_skill_from_task_llm
from .prompt_contract import build_stage_level_prompt_contract
from .policy import observed_endpoint_args
from .registry import ToolRegistry, build_registry
from .skill_library import build_skill_context_packet, distill_skill_library_entries
from .suite import SuiteSpec, SuiteTask, load_suite_spec, suite_summary
from .suite_runner import SuiteRunResult, run_suite


@dataclass(frozen=True)
class AspireGeneration:
    generation: int
    run_dir: str
    generated_suite_path: str | None
    candidate_count: int
    package_count: int
    candidates: list[dict[str, Any]]
    suite_run: dict[str, Any] | None
    history: dict[str, Any]
    skill_context: dict[str, Any]
    learned_evidence: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AspireResult:
    suite_id: str
    suite_path: str
    run_dir: str
    success: bool
    generations: list[dict[str, Any]]
    final_generated_suite_path: str | None
    final_suite_run: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_aspire(
    *,
    config: ManagerConfig,
    suite_path: str | Path,
    candidate_prefix: str,
    population_size: int = 2,
    generations: int = 1,
    top_k: int = 3,
    llm_overrides: dict[str, Any] | None = None,
    llm_response_files: list[str | Path] | None = None,
    llm_max_attempts: int = 2,
    evaluate: bool = True,
    execute: bool = False,
    publish: bool = True,
    reset_before_execute: bool = True,
) -> AspireResult:
    if execute and not evaluate:
        raise ValueError("--execute requires evaluation")
    suite = load_suite_spec(suite_path)
    registry = build_registry(config)
    aspire_dir = new_run_dir(suite.output_root, safe_id(f"{suite.suite_id}_aspire"))
    generation_results: list[AspireGeneration] = []
    final_suite_run: SuiteRunResult | None = None
    final_generated_suite_path: Path | None = None
    success = False

    total_generations = max(1, int(generations))
    for generation_index in range(1, total_generations + 1):
        generation_result = _run_generation(
            config=config,
            suite=suite,
            registry=registry,
            aspire_dir=aspire_dir,
            generation_index=generation_index,
            candidate_prefix=candidate_prefix,
            population_size=population_size,
            top_k=top_k,
            llm_overrides=llm_overrides,
            llm_response_files=llm_response_files,
            llm_max_attempts=llm_max_attempts,
            evaluate=evaluate,
            execute=execute,
            publish=publish,
            reset_before_execute=reset_before_execute,
        )
        generation_results.append(generation_result)
        if generation_result.generated_suite_path:
            final_generated_suite_path = Path(generation_result.generated_suite_path)
        if generation_result.suite_run:
            final_suite_run = _suite_run_from_dict(generation_result.suite_run)
            success = bool(generation_result.suite_run.get("success"))
        _write_aspire_manifest(
            aspire_dir=aspire_dir,
            suite=suite,
            success=success,
            generations=generation_results,
            final_generated_suite_path=final_generated_suite_path,
            final_suite_run=final_suite_run,
        )
        if success:
            break

    result = AspireResult(
        suite_id=suite.suite_id,
        suite_path=suite.manifest_path,
        run_dir=str(aspire_dir),
        success=success,
        generations=[item.to_dict() for item in generation_results],
        final_generated_suite_path=str(final_generated_suite_path) if final_generated_suite_path else None,
        final_suite_run=final_suite_run.to_dict() if final_suite_run else None,
    )
    write_json(aspire_dir / "aspire_manifest.json", result.to_dict())
    write_json(
        aspire_dir / "evolution_manifest.json",
        {
            "schema": "ksm.aspire_kw.run.v2",
            "result": result.to_dict(),
        },
    )
    return result


def build_aspire_prompt(
    *,
    suite: SuiteSpec,
    target_task_path: Path,
    candidate_id: str,
    registry: ToolRegistry,
    history: dict[str, Any],
    skill_context: dict[str, Any],
    generation_index: int,
    candidate_index: int,
    population_size: int,
) -> str:
    task = read_yaml(target_task_path)
    prompt_task = agent_safe_payload(task) if isinstance(task, dict) else task
    prompt_history = agent_safe_payload(history)
    prompt_skill_context = agent_safe_payload(skill_context)
    prompt_new_skill_spec = task_new_skill_spec_prompt_payload(task) if isinstance(task, dict) else {}
    registry_payload = {
        "test_skill_dir": registry.test_skill_dir,
        "ctrl": registry.ctrl,
        "info": registry.info,
        "reasoning": _public_reasoning_names(registry.reasoning),
        "skills": [
            {
                "path": skill.path,
                "description": skill.description,
                "args": skill.args,
                "actions": _public_action_names(skill.actions),
                "capabilities": skill.capabilities,
                "uses_reasoning": skill.uses_reasoning,
                "uses_control": skill.uses_control,
                "is_composite": skill.is_composite,
            }
            for skill in registry.skills[:80]
        ],
        "endpoint_arg_contracts": _public_endpoint_arg_contracts(registry),
    }
    return f"""
You are running the unified ASPIRE-KW framework for Knowin World YAML skills.

Return exactly one JSON object, no Markdown, with these fields:
- candidate_id: string, exactly "{candidate_id}"
- hypothesis: string
- change_summary: string
- expected_failure_modes: list of strings
- skill_reuse_decision: object with:
  - decision: reuse_existing_skill, composition_skill_candidate, new_subskill_candidate, or blocked_by_missing_primitive
  - candidate_role: reuse_existing_skill, skill_specialization, new_behavior_skill, or blocked_by_gap
  - reusable_interface: object with name, args, expected_effects, observable_success, and failure_modes when candidate_role is skill_specialization or new_behavior_skill
  - added_behavior_contract: object describing added constraints, checks, or task-family semantics when candidate_role is skill_specialization or new_behavior_skill
  - selected_existing_skills: list of existing KW skills reused internally
  - rationale: short reason for the boundary decision
- skill_args: object
- skill_yaml: string containing the full YAML skill

The skill_yaml must be a complete KW skill mapping, not only a workflow list.
It must start with schema_version/name/description/args/workflow, for example:
```yaml
schema_version: 1.0.0
name: {candidate_id}
description: short description
args:
  arm_id: 0
workflow:
  - action: pickplace/semantic_pickplace.yaml
    args:
      arm_id: = args.arm_id
```
The skill_yaml must be KW YAML, not Python.
The skill_yaml must parse with PyYAML. Quote string scalars that contain ":" or start with "=".
Do not use cloud/private reasoning endpoints unless they are already present in the allowed public registry.
First decide the skill boundary, then write YAML:
- reuse_existing_skill: directly uses an existing mature skill; this is a baseline/reuse candidate, not a new skill candidate.
- skill_specialization: may call existing KW skills internally but adds a reusable task-family interface, constraints, expected effects, observable success, and failure modes.
- new_behavior_skill: changes the mechanism using available public actions because existing skills do not express the key behavior.
- blocked_by_gap: use when the registry lacks a required primitive; return a minimal legal diagnostic YAML and explain the gap instead of pretending success.
Map this boundary into skill_reuse_decision.decision: reuse_existing_skill for direct reuse, composition_skill_candidate for specialization, new_subskill_candidate for new behavior, blocked_by_missing_primitive for gaps.
Prefer stable high-level skills such as pickplace/semantic_pickplace.yaml when they truly match the task, but do not claim direct reuse is a new skill candidate.
If history shows repeated direct-reuse failure, the next candidate must either change runtime binding with a trace-grounded reason or move to a specialization/new behavior candidate.
If Agent controller new-skill spec is present, it is the current boundary request from the outer controller:
- use it as a design brief for the candidate boundary, not as executable code;
- do not downgrade it to reuse_existing_skill unless the correct answer is blocked_by_gap;
- skill_specialization may reuse existing KW skills, but must declare the added behavior contract beyond forwarding args;
- reuse existing KW skills for stable substeps named by the spec;
- for skill_specialization, call the selected stable YAML skill(s) directly when they cover the mechanism; do not inline their internal /reasoning or /ctrl sequence unless endpoint_arg_contracts explicitly exposes those endpoints.
- reusable_interface.semantic_context_args, when present, are semantic context carried by the interface; do not invent fake actions only to consume those args.
- if the registry cannot express the required behavior, return blocked_by_gap instead of faking a new skill.
Generate one continuous top-level skill; do not hide a planner in Python.
Action args must be primitives, lists of primitives, or expressions resolving to primitives/lists. They must also be declared by the called subskill when calling a known YAML skill. Do not pass nested maps into action args.
Do not override structured subskill args such as planner_config or gripper; omit them and let the called KW skill use its defaults.
Direct endpoint actions (/ctrl, /info, /reasoning) must appear in endpoint_arg_contracts and must use only the listed argument names. If an endpoint is not listed, use an existing YAML skill wrapper or declare blocked_by_gap instead of guessing its args.
Do not add /reasoning/identity as a comment, verifier, or placeholder. Identity is only valid when it writes an output used by later workflow steps.
Use retrieved ASPIRE-KW skill evidence as strategy guidance, not as executable code.
Treat task_analysis_state, evidence_gaps, visual_effect_probes, and retrieved_negative_evidence as ASPIRE memory:
- reason from the concrete evidence and summaries rather than from a fixed failure taxonomy;
- do not repeat the same concrete failed workflow pattern unless the new YAML changes why it should work;
- if evidence_gaps show missing visual/trace evidence, make the new candidate expose or preserve observable evidence;
- do not treat extra verifier/assertion/trace steps after a failed prerequisite action as a mechanism-level repair for that prerequisite;
- do not optimize only pipeline success when agent-observable effect evidence is missing.
Repair feedback in this prompt is agent-observable only. Evaluator-only fields such as predicate reports, task/effect success, verifier success, and ground-truth scoring are intentionally omitted from ASPIRE repair evidence.
Do not translate Python code patterns literally. Generate legal KW YAML only.

{build_stage_level_prompt_contract(task)}

Generation: {generation_index}
Candidate id: {candidate_id}
Candidate index in population: {candidate_index}/{population_size}

Suite summary:
{json.dumps(suite_summary(suite), ensure_ascii=False, indent=2)}

Target task:
{json.dumps(prompt_task, ensure_ascii=False, indent=2)}

ASPIRE agent-observable history:
{json.dumps(prompt_history, ensure_ascii=False, indent=2)}

Retrieved ASPIRE-KW skill evidence, agent-observable view:
{json.dumps(prompt_skill_context, ensure_ascii=False, indent=2)}

Agent controller new-skill spec, if any:
{json.dumps(prompt_new_skill_spec, ensure_ascii=False, indent=2)}

Available KW tools and skills:
{json.dumps(registry_payload, ensure_ascii=False, indent=2)}
""".strip()


def _run_generation(
    *,
    config: ManagerConfig,
    suite: SuiteSpec,
    registry: ToolRegistry,
    aspire_dir: Path,
    generation_index: int,
    candidate_prefix: str,
    population_size: int,
    top_k: int,
    llm_overrides: dict[str, Any] | None,
    llm_response_files: list[str | Path] | None,
    llm_max_attempts: int,
    evaluate: bool,
    execute: bool,
    publish: bool,
    reset_before_execute: bool,
) -> AspireGeneration:
    generation_dir = aspire_dir / f"generation_{generation_index:02d}"
    generation_dir.mkdir(parents=True, exist_ok=False)
    history = _load_history(suite=suite, aspire_dir=aspire_dir, top_k=top_k)
    target_task_path = _target_task_path(suite.tasks[0], generation_dir)
    task = read_yaml(target_task_path)
    if not isinstance(task, dict):
        raise ValueError(f"target task YAML must be a mapping: {target_task_path}")
    skill_context = build_skill_context_packet(
        root=config.skill_library.root,
        task=task,
        history=history,
        extra_roots=[aspire_dir / "learned_skill_library"],
        top_k=config.skill_library.top_k,
        snippet_chars=config.skill_library.snippet_chars,
        max_chars=config.skill_library.max_chars,
    )
    parent_id = _select_parent_id(suite, history)
    candidates: list[dict[str, Any]] = []
    packages: list[CandidatePackage] = []
    total = max(1, int(population_size))
    for candidate_index in range(1, total + 1):
        candidate_id = safe_id(f"{candidate_prefix}_g{generation_index}_c{candidate_index}")
        try:
            prompt = build_aspire_prompt(
                suite=suite,
                target_task_path=target_task_path,
                candidate_id=candidate_id,
                registry=registry,
                history=history,
                skill_context=skill_context,
                generation_index=generation_index,
                candidate_index=candidate_index,
                population_size=total,
            )
            generated = generate_skill_from_task_llm(
                task_path=target_task_path,
                candidate_id=candidate_id,
                output_dir=generation_dir / "generated" / candidate_id,
                registry=registry,
                llm_config=config.llm,
                llm_overrides=llm_overrides,
                response_file=_response_file_for_index(llm_response_files, candidate_index),
                max_attempts=llm_max_attempts,
                prompt_override=prompt,
                repair_memory=history,
            )
            package = _package_generated_candidate(
                generated=generated,
                output_root=generation_dir / "packages",
                registry=registry,
                parent_id=parent_id,
            )
            packages.append(package)
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "status": "packaged",
                    "generated": generated.to_dict(),
                    "package": package.to_dict(),
                }
            )
        except Exception as exc:
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "status": "generation_failed",
                    "error": repr(exc),
                }
            )

    generated_suite_path: Path | None = None
    suite_run: SuiteRunResult | None = None
    learned_evidence: list[dict[str, Any]] = []
    if packages:
        generated_suite_path = generation_dir / "candidate_suite.yaml"
        write_yaml(
            generated_suite_path,
            {
                "suite_id": f"{suite.suite_id}_aspire_g{generation_index}_eval",
                "description": f"Generated by unified ASPIRE-KW from {suite.suite_id}.",
                "output_root": str(generation_dir / "suite_runs"),
                "publish_subdir": suite.publish_subdir,
                "success_threshold": suite.success_threshold,
                "tasks": [_suite_task_ref(task) for task in suite.tasks],
                "candidate_packages": [package.package_dir for package in packages],
            },
        )
        if evaluate:
            suite_run = run_suite(
                config=config,
                suite_path=generated_suite_path,
                execute=execute,
                publish=publish,
                reset_before_execute=reset_before_execute,
            )
            learned_evidence = distill_skill_library_entries(
                suite_run=suite_run.to_dict(),
                output_root=aspire_dir / "learned_skill_library",
                generation_index=generation_index,
            )

    result = AspireGeneration(
        generation=generation_index,
        run_dir=str(generation_dir),
        generated_suite_path=str(generated_suite_path) if generated_suite_path else None,
        candidate_count=len(candidates),
        package_count=len(packages),
        candidates=candidates,
        suite_run=suite_run.to_dict() if suite_run else None,
        history=history,
        skill_context=skill_context,
        learned_evidence=learned_evidence,
    )
    write_json(generation_dir / "generation_manifest.json", result.to_dict())
    return result


def _package_generated_candidate(
    *,
    generated: GeneratedSkill,
    output_root: str | Path,
    registry: ToolRegistry,
    parent_id: str | None,
) -> CandidatePackage:
    metadata = _generated_candidate_metadata(generated)
    return package_skill_candidate(
        candidate_id=generated.candidate_id,
        skill_yaml=generated.local_path,
        output_root=output_root,
        registry=registry,
        hypothesis=str(generated.metadata.get("hypothesis") or "ASPIRE-KW candidate."),
        change_summary=str(generated.metadata.get("change_summary") or "Generated by unified ASPIRE-KW."),
        expected_failure_modes=list(generated.metadata.get("expected_failure_modes") or []),
        skill_args=dict(generated.metadata.get("skill_args") or {}),
        parent_id=parent_id,
        overwrite=True,
        metadata=metadata,
    )


def _generated_candidate_metadata(generated: GeneratedSkill) -> dict[str, Any]:
    task_context = generated.metadata.get("task_context") if isinstance(generated.metadata.get("task_context"), dict) else {}
    decision = task_context.get("skill_reuse_decision") if isinstance(task_context.get("skill_reuse_decision"), dict) else {}
    llm_decision = generated.metadata.get("skill_reuse_decision") if isinstance(generated.metadata.get("skill_reuse_decision"), dict) else {}
    lifecycle = decision.get("candidate_lifecycle") if isinstance(decision.get("candidate_lifecycle"), dict) else {}
    metadata: dict[str, Any] = {
        "source": "aspire_generated_candidate",
        "task_context": task_context,
    }
    if llm_decision:
        metadata["llm_skill_reuse_decision"] = llm_decision
    if task_context.get("stage_id"):
        metadata["stage_id"] = task_context.get("stage_id")
    if decision:
        metadata["skill_reuse_decision"] = decision
        metadata["candidate_lifecycle"] = lifecycle
        metadata["runtime_wrapper_only"] = not bool(lifecycle.get("maintain_candidate"))
    return metadata


def _load_history(*, suite: SuiteSpec, aspire_dir: Path, top_k: int) -> dict[str, Any]:
    summaries = [
        scan_episode_reports(suite.output_root),
        scan_episode_reports(aspire_dir),
    ]
    candidates_by_id: dict[str, dict[str, Any]] = {}
    failure_breakdown: dict[str, int] = {}
    evaluation_summaries: list[dict[str, Any]] = []
    feedback_memories: list[dict[str, Any]] = []
    num_trials = 0
    task_completed = 0
    for summary in summaries:
        num_trials += int(summary.get("num_trials") or 0)
        task_completed += int(summary.get("task_completed") or 0)
        for name, count in (summary.get("failure_breakdown") or {}).items():
            failure_breakdown[str(name)] = failure_breakdown.get(str(name), 0) + int(count)
        if isinstance(summary.get("evaluation_summary"), dict):
            evaluation_summaries.append(summary["evaluation_summary"])
        if isinstance(summary.get("feedback_memory"), dict):
            feedback_memories.append(summary["feedback_memory"])
        for candidate in summary.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("candidate_id") or "unknown")
            existing = candidates_by_id.setdefault(
                candidate_id,
                {
                    "candidate_id": candidate_id,
                    "num_trials": 0,
                    "task_completed": 0,
                    "success_rate": 0.0,
                    "failure_breakdown": {},
                    "trace_failure_breakdown": {},
                    "subgoal_failure_breakdown": {},
                    "recurring_summaries": {},
                    "evidence_gaps": {},
                    "reports": [],
                },
            )
            existing["num_trials"] += int(candidate.get("num_trials") or 0)
            existing["task_completed"] += int(candidate.get("task_completed") or 0)
            existing["reports"].extend(candidate.get("reports") or [])
            for name, count in (candidate.get("failure_breakdown") or {}).items():
                failures = existing["failure_breakdown"]
                failures[str(name)] = failures.get(str(name), 0) + int(count)
            for key in ("trace_failure_breakdown", "subgoal_failure_breakdown", "recurring_summaries", "evidence_gaps"):
                bucket = existing[key]
                for name, count in (candidate.get(key) or {}).items():
                    bucket[str(name)] = bucket.get(str(name), 0) + int(count)
    for candidate in candidates_by_id.values():
        trials = int(candidate["num_trials"])
        candidate["success_rate"] = float(candidate["task_completed"]) / trials if trials else 0.0
        candidate["failure_breakdown"] = dict(sorted(candidate["failure_breakdown"].items()))
        candidate["trace_failure_breakdown"] = dict(sorted(candidate["trace_failure_breakdown"].items()))
        candidate["subgoal_failure_breakdown"] = dict(sorted(candidate["subgoal_failure_breakdown"].items()))
        candidate["recurring_summaries"] = dict(sorted(candidate["recurring_summaries"].items(), key=lambda item: (-item[1], item[0]))[:5])
        candidate["evidence_gaps"] = dict(sorted(candidate["evidence_gaps"].items()))
    candidates = sorted(
        candidates_by_id.values(),
        key=lambda item: (-float(item["success_rate"]), str(item["candidate_id"])),
    )
    feedback_memory = merge_feedback_memories(feedback_memories)
    task_analysis_state = build_task_analysis_state(
        suite_id=suite.suite_id,
        task_ids=[task.task_id for task in suite.tasks],
        stage="history",
        manifest_path=suite.manifest_path,
        run_dir=str(aspire_dir),
        success_threshold=suite.success_threshold,
        candidates=candidates,
        feedback_memory=feedback_memory,
    )
    return {
        "source_roots": [suite.output_root, str(aspire_dir)],
        "top_k": top_k,
        "leaderboard": {
            "num_trials": num_trials,
            "task_completed": task_completed,
            "success_rate": float(task_completed) / num_trials if num_trials else 0.0,
            "failure_breakdown": dict(sorted(failure_breakdown.items())),
            "candidates": candidates[: max(0, int(top_k))],
        },
        "evaluation_summary": _merge_evaluation_summaries(evaluation_summaries),
        "feedback_memory": feedback_memory,
        "task_analysis_state": task_analysis_state,
        "seed_candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "package_dir": candidate.package_dir,
                "hypothesis": candidate.manifest.get("hypothesis"),
                "change_summary": candidate.manifest.get("change_summary"),
                "parent_id": candidate.manifest.get("parent_id"),
            }
            for candidate in suite.candidates
        ],
    }


def _write_aspire_manifest(
    *,
    aspire_dir: Path,
    suite: SuiteSpec,
    success: bool,
    generations: list[AspireGeneration],
    final_generated_suite_path: Path | None,
    final_suite_run: SuiteRunResult | None,
) -> None:
    write_json(
        aspire_dir / "aspire_manifest.json",
        {
            "suite_id": suite.suite_id,
            "suite_path": suite.manifest_path,
            "run_dir": str(aspire_dir),
            "success": success,
            "generations": [item.to_dict() for item in generations],
            "final_generated_suite_path": str(final_generated_suite_path) if final_generated_suite_path else None,
            "final_suite_run": final_suite_run.to_dict() if final_suite_run else None,
        },
    )


def _suite_run_from_dict(data: dict[str, Any]) -> SuiteRunResult:
    return SuiteRunResult(
        suite_id=str(data.get("suite_id")),
        suite_path=str(data.get("suite_path")),
        run_dir=str(data.get("run_dir")),
        execute=bool(data.get("execute")),
        publish=bool(data.get("publish")),
        success=bool(data.get("success")),
        structural_ok=bool(data.get("structural_ok")),
        success_rate=float(data.get("success_rate") or 0.0),
        task_success_rate=float(data.get("task_success_rate", data.get("success_rate")) or 0.0),
        policy_ok_rate=float(data.get("policy_ok_rate") or 0.0),
        pipeline_success_rate=float(data.get("pipeline_success_rate", data.get("success_rate")) or 0.0),
        effect_success_rate=(
            None
            if data.get("effect_success_rate") is None
            else float(data.get("effect_success_rate") or 0.0)
        ),
        predicate_success_rate=(
            None
            if data.get("predicate_success_rate") is None
            else float(data.get("predicate_success_rate") or 0.0)
        ),
        episodes=list(data.get("episodes") or []),
        leaderboard=dict(data.get("leaderboard") or {}),
        evaluation_summary=dict(data.get("evaluation_summary") or {}),
        feedback_memory=dict(data.get("feedback_memory") or {}),
        task_analysis_state=dict(data.get("task_analysis_state") or {}),
    )


def _merge_evaluation_summaries(items: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "schema": "ksm.aspire_kw.evaluation_summary.v1",
        "num_reports": 0,
        "category_breakdown": {},
        "stage_breakdown": {},
        "recommended_focus_breakdown": {},
        "trace_failure_breakdown": {},
        "subgoal_failure_breakdown": {},
        "evidence_gap_breakdown": {},
        "recurring_summaries": {},
        "top_failed_actions": [],
    }
    failed_actions: dict[str, int] = {}
    for item in items:
        merged["num_reports"] += int(item.get("num_reports") or 0)
        for key in (
            "category_breakdown",
            "stage_breakdown",
            "recommended_focus_breakdown",
            "trace_failure_breakdown",
            "subgoal_failure_breakdown",
            "evidence_gap_breakdown",
        ):
            bucket = merged[key]
            for name, count in (item.get(key) or {}).items():
                bucket[str(name)] = bucket.get(str(name), 0) + int(count)
        for name, count in (item.get("recurring_summaries") or {}).items():
            bucket = merged["recurring_summaries"]
            bucket[str(name)] = bucket.get(str(name), 0) + int(count)
        for action in item.get("top_failed_actions") or []:
            if not isinstance(action, dict):
                continue
            name = str(action.get("action") or "")
            if not name:
                continue
            failed_actions[name] = failed_actions.get(name, 0) + int(action.get("count") or 0)
    merged["top_failed_actions"] = [
        {"action": action, "count": count}
        for action, count in sorted(failed_actions.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]
    merged["recurring_summaries"] = dict(
        sorted(merged["recurring_summaries"].items(), key=lambda item: (-item[1], item[0]))[:8]
    )
    return merged


def _select_parent_id(suite: SuiteSpec, history: dict[str, Any]) -> str | None:
    candidates = ((history.get("leaderboard") or {}).get("candidates") or [])
    if candidates:
        candidate_id = candidates[0].get("candidate_id")
        if candidate_id:
            return str(candidate_id)
    if suite.candidates:
        return suite.candidates[0].candidate_id
    return None


def _target_task_path(task: SuiteTask, generation_dir: Path) -> Path:
    if task.task_path:
        return Path(task.task_path).expanduser().resolve()
    target = generation_dir / "target_task.yaml"
    write_yaml(
        target,
        {
            "task_id": task.task_id,
            "args": task.skill_args,
            "predicates": task.predicates,
            "reset_layout": task.reset_layout,
        },
    )
    return target


def _suite_task_ref(task: SuiteTask) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": task.task_id,
        "skill_args": task.skill_args,
        "predicates": task.predicates,
        "reset_layout": task.reset_layout,
    }
    if task.task_path:
        payload["task_path"] = task.task_path
    return payload


def _response_file_for_index(files: list[str | Path] | None, index: int) -> str | Path | None:
    if not files:
        return None
    position = min(max(index - 1, 0), len(files) - 1)
    return files[position]


def _public_endpoint_arg_contracts(registry: ToolRegistry) -> dict[str, list[str]]:
    return {
        action: sorted(args)
        for action, args in observed_endpoint_args(registry).items()
        if not _is_private_reasoning_name(action)
    }


def _public_reasoning_names(names: list[str]) -> list[str]:
    return [name for name in names if not _is_private_reasoning_name(name)]


def _public_action_names(actions: list[str]) -> list[str]:
    return [action for action in actions if not _is_private_reasoning_name(action)]


def _is_private_reasoning_name(value: str) -> bool:
    return ("qw" + "en") in value.lower()
