from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .artifacts import new_run_dir, write_report
from .agent_nodes import node_catalog_payload, plan_next_action_node, propose_new_skill_spec_node, summarize_experiment_history_node
from .agent_state import STRATEGY_NEW_SKILL, assert_agent_context_safe, decide_next_action, load_experiment_state_from_roots
from .aspire import run_aspire
from .aspire_loop import run_aspire_iteration
from .candidate import package_skill_candidate
from .config import load_config
from .generator import generate_skill_from_task
from .io import write_json
from .leaderboard import markdown_leaderboard, scan_episode_reports
from .policy import check_skill
from .publisher import publish_skill
from .registry import build_registry
from .robodojo_auto import run_robodojo_auto, run_robodojo_staged_auto
from .robodojo_decision import run_robodojo_decision
from .runner import PipelineDirectClient, run_published_skill


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ksm", description="Knowin skill manager prototype")
    sub = parser.add_subparsers(dest="cmd", required=True)

    registry = sub.add_parser("registry", help="scan KW skills and tools")
    registry.add_argument("--config", required=True)
    registry.add_argument("--out")

    generate = sub.add_parser("generate", help="generate a candidate YAML from a task")
    generate.add_argument("--config", required=True)
    generate.add_argument("--task", required=True)
    generate.add_argument("--candidate-id", required=True)
    generate.add_argument("--out-dir")

    validate = sub.add_parser("validate", help="validate a candidate YAML")
    validate.add_argument("--config", required=True)
    validate.add_argument("--skill", required=True)

    publish = sub.add_parser("publish", help="publish YAML into the KSM test skill folder")
    publish.add_argument("--config", required=True)
    publish.add_argument("--candidate-id", required=True)
    publish.add_argument("--skill", required=True)

    status = sub.add_parser("pipeline-status", help="read direct pipeline status")
    status.add_argument("--config", required=True)

    run = sub.add_parser("run", help="run an already published skill")
    run.add_argument("--config", required=True)
    run.add_argument("--skill-path", required=True)
    run.add_argument("--kwargs-json", default="{}")

    smoke = sub.add_parser("smoke", help="generate, validate, publish, and optionally execute")
    smoke.add_argument("--config", required=True)
    smoke.add_argument("--task", required=True)
    smoke.add_argument("--candidate-id", required=True)
    smoke.add_argument("--execute", action="store_true")

    package = sub.add_parser("package-candidate", help="package a KW YAML skill as an ASPIRE-style candidate")
    package.add_argument("--config", required=True)
    package.add_argument("--candidate-id", required=True)
    package.add_argument("--skill-yaml", required=True)
    package.add_argument("--output-root", required=True)
    package.add_argument("--hypothesis", required=True)
    package.add_argument("--change-summary", default="")
    package.add_argument("--parent-id")
    package.add_argument("--expected-failure-mode", action="append", default=[])
    package.add_argument("--skill-args-json", default="{}")
    package.add_argument("--overwrite", action="store_true")
    package.add_argument("--allow-policy-violations", action="store_true")

    aspire = sub.add_parser("aspire", help="run the first-stage ASPIRE-KW generate/evaluate loop")
    aspire.add_argument("--config", required=True)
    aspire.add_argument("--task", required=True)
    aspire.add_argument("--candidate-id", required=True)
    aspire.add_argument("--execute", action="store_true")
    aspire.add_argument("--no-publish", action="store_true")
    aspire.add_argument("--gpt-base-url", dest="gpt_base_url")
    aspire.add_argument("--gpt-api-key-env", dest="gpt_api_key_env")
    aspire.add_argument("--gpt-model", dest="gpt_model")
    aspire.add_argument("--gpt-model-env", dest="gpt_model_env")
    aspire.add_argument("--gpt-temperature", dest="gpt_temperature", type=float)
    aspire.add_argument("--gpt-max-tokens", dest="gpt_max_tokens", type=int)
    aspire.add_argument("--gpt-max-attempts", dest="gpt_max_attempts", type=int, default=2)
    aspire.add_argument("--gpt-response-file", dest="gpt_response_file", help="use a saved GPT response instead of calling the service")

    aspire_suite = sub.add_parser("aspire-suite", help="run the unified ASPIRE-KW suite/population loop")
    aspire_suite.add_argument("--config", required=True)
    aspire_suite.add_argument("--suite", required=True)
    aspire_suite.add_argument("--candidate-prefix", required=True)
    aspire_suite.add_argument("--population-size", type=int, default=2)
    aspire_suite.add_argument("--generations", type=int, default=1)
    aspire_suite.add_argument("--top-k", type=int, default=3)
    aspire_suite.add_argument("--execute", action="store_true")
    aspire_suite.add_argument("--no-evaluate", action="store_true")
    aspire_suite.add_argument("--no-publish", action="store_true")
    aspire_suite.add_argument("--no-reset-before-execute", action="store_true")
    aspire_suite.add_argument("--gpt-base-url", dest="gpt_base_url")
    aspire_suite.add_argument("--gpt-api-key-env", dest="gpt_api_key_env")
    aspire_suite.add_argument("--gpt-model", dest="gpt_model")
    aspire_suite.add_argument("--gpt-model-env", dest="gpt_model_env")
    aspire_suite.add_argument("--gpt-temperature", dest="gpt_temperature", type=float)
    aspire_suite.add_argument("--gpt-max-tokens", dest="gpt_max_tokens", type=int)
    aspire_suite.add_argument("--gpt-max-attempts", dest="gpt_max_attempts", type=int, default=2)
    aspire_suite.add_argument("--gpt-response-file", dest="gpt_response_files", action="append", default=[])

    leaderboard = sub.add_parser("leaderboard", help="scan ASPIRE-KW episode reports")
    leaderboard.add_argument("--config", required=True)
    leaderboard.add_argument("--root")
    leaderboard.add_argument("--json", action="store_true")

    robodojo_auto = sub.add_parser("robodojo-auto", help="run a full-task RoboDojo KSM experiment")
    robodojo_auto.add_argument("--config", required=True)
    robodojo_auto.add_argument("--output-root")
    robodojo_auto.add_argument("--task-class")
    robodojo_auto.add_argument("--tier", type=int, default=4)
    robodojo_auto.add_argument("--max-scenes", type=int, default=240)
    robodojo_auto.add_argument("--candidate-prefix", default="robodojo_auto")
    robodojo_auto.add_argument("--preferred-task-id")
    robodojo_auto.add_argument("--primary-pick-label")
    robodojo_auto.add_argument("--primary-place-label")
    robodojo_auto.add_argument("--execute", action="store_true")
    robodojo_auto.add_argument("--no-publish", action="store_true")
    robodojo_auto.add_argument("--no-capture", action="store_true")
    robodojo_auto.add_argument("--diagnostic-stages", action="store_true")
    robodojo_auto.add_argument("--gpt-base-url", dest="gpt_base_url")
    robodojo_auto.add_argument("--gpt-api-key-env", dest="gpt_api_key_env")
    robodojo_auto.add_argument("--gpt-model", dest="gpt_model")
    robodojo_auto.add_argument("--gpt-model-env", dest="gpt_model_env")
    robodojo_auto.add_argument("--gpt-temperature", dest="gpt_temperature", type=float)
    robodojo_auto.add_argument("--gpt-max-tokens", dest="gpt_max_tokens", type=int)
    robodojo_auto.add_argument("--gpt-max-attempts", dest="gpt_max_attempts", type=int, default=2)

    robodojo_decision = sub.add_parser("robodojo-decision", help="run task-level RoboDojo reuse/new/gap decision classification")
    robodojo_decision.add_argument("--config", required=True)
    robodojo_decision.add_argument("--output-root")
    robodojo_decision.add_argument("--task-class", action="append", default=[])
    robodojo_decision.add_argument("--max-per-class", type=int, default=1)
    robodojo_decision.add_argument("--candidate-prefix", default="robodojo_decision")
    robodojo_decision.add_argument("--gpt-base-url", dest="gpt_base_url")
    robodojo_decision.add_argument("--gpt-api-key-env", dest="gpt_api_key_env")
    robodojo_decision.add_argument("--gpt-model", dest="gpt_model")
    robodojo_decision.add_argument("--gpt-model-env", dest="gpt_model_env")
    robodojo_decision.add_argument("--gpt-temperature", dest="gpt_temperature", type=float)
    robodojo_decision.add_argument("--gpt-max-tokens", dest="gpt_max_tokens", type=int)
    robodojo_decision.add_argument("--gpt-response-file", dest="gpt_response_file")

    robodojo_staged = sub.add_parser("robodojo-staged", help="run a RoboDojo staged KSM experiment")
    robodojo_staged.add_argument("--config", required=True)
    robodojo_staged.add_argument("--output-root")
    robodojo_staged.add_argument("--task-class", default="put_bottles_into_dustbin")
    robodojo_staged.add_argument("--tier", type=int, default=4)
    robodojo_staged.add_argument("--max-scenes", type=int, default=240)
    robodojo_staged.add_argument("--candidate-prefix", default="robodojo_staged")
    robodojo_staged.add_argument("--execute", action="store_true")
    robodojo_staged.add_argument("--no-publish", action="store_true")
    robodojo_staged.add_argument("--no-capture", action="store_true")
    robodojo_staged.add_argument("--stage-id", action="append", default=[])
    robodojo_staged.add_argument("--stop-after-stage", default="pick_bottle")

    agent_plan = sub.add_parser("agent-plan", help="build an offline controller plan from prior experiment history")
    agent_plan.add_argument("--objective", required=True)
    agent_plan.add_argument("--history-root", action="append", required=True)
    agent_plan.add_argument("--out")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "registry":
        return _registry(args)
    if args.cmd == "generate":
        return _generate(args)
    if args.cmd == "validate":
        return _validate(args)
    if args.cmd == "publish":
        return _publish(args)
    if args.cmd == "pipeline-status":
        return _pipeline_status(args)
    if args.cmd == "run":
        return _run(args)
    if args.cmd == "smoke":
        return _smoke(args)
    if args.cmd == "package-candidate":
        return _package_candidate(args)
    if args.cmd == "aspire":
        return _aspire(args)
    if args.cmd == "aspire-suite":
        return _aspire_suite(args)
    if args.cmd == "leaderboard":
        return _leaderboard(args)
    if args.cmd == "robodojo-auto":
        return _robodojo_auto(args)
    if args.cmd == "robodojo-decision":
        return _robodojo_decision(args)
    if args.cmd == "robodojo-staged":
        return _robodojo_staged(args)
    if args.cmd == "agent-plan":
        return _agent_plan(args)
    raise AssertionError(args.cmd)


def _registry(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config)
    payload = registry.to_dict()
    if args.out:
        write_json(args.out, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _generate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config)
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else config.artifacts.candidates_dir
    generated = generate_skill_from_task(
        task_path=args.task,
        candidate_id=args.candidate_id,
        output_dir=out_dir,
        registry=registry,
    )
    print(json.dumps(generated.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config)
    result = check_skill(args.skill, registry)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.ok else 2


def _publish(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = publish_skill(config=config, candidate_id=args.candidate_id, source_path=args.skill)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _pipeline_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = PipelineDirectClient(config.pipeline.base_url)
    print(json.dumps(client.pipeline_status(), indent=2, ensure_ascii=False))
    return 0


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    kwargs = _json_object(args.kwargs_json)
    result = run_published_skill(config=config, skill_path=args.skill_path, kwargs=kwargs)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _smoke(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config)
    run_dir = new_run_dir(config.artifacts.runs_dir, args.candidate_id)
    generated = generate_skill_from_task(
        task_path=args.task,
        candidate_id=args.candidate_id,
        output_dir=run_dir / "candidates",
        registry=registry,
    )
    policy = check_skill(generated.local_path, registry)
    publish = None
    run_result = None
    if policy.ok:
        publish = publish_skill(config=config, candidate_id=args.candidate_id, source_path=generated.local_path)
        if args.execute:
            run_result = run_published_skill(config=config, skill_path=publish.pipeline_skill_path, kwargs={})
    report: dict[str, Any] = {
        "candidate_id": args.candidate_id,
        "task": str(Path(args.task).expanduser().resolve()),
        "generated": generated.to_dict(),
        "policy": policy.to_dict(),
        "publish": publish.to_dict() if publish else None,
        "executed": bool(args.execute),
        "run_result": run_result.to_dict() if run_result else None,
        "run_dir": str(run_dir),
    }
    write_report(run_dir, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if policy.ok else 2


def _package_candidate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config)
    skill_args = _json_object(args.skill_args_json)
    package = package_skill_candidate(
        candidate_id=args.candidate_id,
        skill_yaml=args.skill_yaml,
        output_root=args.output_root,
        registry=registry,
        hypothesis=args.hypothesis,
        change_summary=args.change_summary,
        expected_failure_modes=args.expected_failure_mode,
        skill_args=skill_args,
        parent_id=args.parent_id,
        overwrite=args.overwrite,
    )
    print(json.dumps(package.to_dict(), indent=2, ensure_ascii=False))
    if package.policy_ok is False and not args.allow_policy_violations:
        return 2
    return 0


def _aspire(args: argparse.Namespace) -> int:
    if args.execute and args.no_publish:
        raise ValueError("--execute requires publishing the candidate into the KW-visible test skill folder")
    config = load_config(args.config)
    llm_overrides = {
        "base_url": args.gpt_base_url,
        "api_key_env": args.gpt_api_key_env,
        "model": args.gpt_model,
        "model_env": args.gpt_model_env,
        "temperature": args.gpt_temperature,
        "max_tokens": args.gpt_max_tokens,
    }
    result = run_aspire_iteration(
        config=config,
        task_path=args.task,
        candidate_id=args.candidate_id,
        generator_mode="llm",
        llm_overrides=llm_overrides,
        llm_response_file=args.gpt_response_file,
        llm_max_attempts=args.gpt_max_attempts,
        execute=bool(args.execute),
        publish=not bool(args.no_publish),
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    if args.execute:
        return 0 if bool(result.episode_report.get("success")) else 1
    return 0 if bool(result.policy.get("ok")) else 2


def _aspire_suite(args: argparse.Namespace) -> int:
    if args.execute and args.no_publish:
        raise ValueError("--execute requires publishing candidates into the KW-visible test skill folder")
    config = load_config(args.config)
    llm_overrides = {
        "base_url": args.gpt_base_url,
        "api_key_env": args.gpt_api_key_env,
        "model": args.gpt_model,
        "model_env": args.gpt_model_env,
        "temperature": args.gpt_temperature,
        "max_tokens": args.gpt_max_tokens,
    }
    result = run_aspire(
        config=config,
        suite_path=args.suite,
        candidate_prefix=args.candidate_prefix,
        population_size=args.population_size,
        generations=args.generations,
        top_k=args.top_k,
        llm_overrides=llm_overrides,
        llm_response_files=list(args.gpt_response_files or []),
        llm_max_attempts=args.gpt_max_attempts,
        evaluate=not bool(args.no_evaluate),
        execute=bool(args.execute),
        publish=not bool(args.no_publish),
        reset_before_execute=not bool(args.no_reset_before_execute),
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    if args.execute:
        return 0 if result.success else 1
    return 0


def _leaderboard(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = Path(args.root).expanduser().resolve() if args.root else config.artifacts.runs_dir
    summary = scan_episode_reports(root)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(markdown_leaderboard(summary))
    return 0


def _robodojo_auto(args: argparse.Namespace) -> int:
    if args.execute and args.no_publish:
        raise ValueError("--execute requires publishing candidates into the KW-visible test skill folder")
    config = load_config(args.config)
    llm_overrides = {
        "base_url": args.gpt_base_url,
        "api_key_env": args.gpt_api_key_env,
        "model": args.gpt_model,
        "model_env": args.gpt_model_env,
        "temperature": args.gpt_temperature,
        "max_tokens": args.gpt_max_tokens,
    }
    result = run_robodojo_auto(
        config=config,
        output_root=args.output_root,
        task_class=args.task_class,
        tier=args.tier,
        max_scenes=args.max_scenes,
        candidate_prefix=args.candidate_prefix,
        execute=bool(args.execute),
        publish=not bool(args.no_publish),
        capture_artifacts=not bool(args.no_capture),
        diagnostic_stages=bool(args.diagnostic_stages),
        preferred_task_id=args.preferred_task_id,
        primary_pick_label=args.primary_pick_label,
        primary_place_label=args.primary_place_label,
        llm_overrides=llm_overrides,
        llm_max_attempts=args.gpt_max_attempts,
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.success or not args.execute else 1


def _robodojo_decision(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    llm_overrides = {
        "base_url": args.gpt_base_url,
        "api_key_env": args.gpt_api_key_env,
        "model": args.gpt_model,
        "model_env": args.gpt_model_env,
        "temperature": args.gpt_temperature,
        "max_tokens": args.gpt_max_tokens,
    }
    result = run_robodojo_decision(
        config=config,
        output_root=args.output_root,
        task_classes=list(args.task_class or []) or None,
        max_per_class=args.max_per_class,
        candidate_prefix=args.candidate_prefix,
        llm_overrides=llm_overrides,
        response_file=args.gpt_response_file,
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _robodojo_staged(args: argparse.Namespace) -> int:
    if args.execute and args.no_publish:
        raise ValueError("--execute requires publishing candidates into the KW-visible test skill folder")
    config = load_config(args.config)
    result = run_robodojo_staged_auto(
        config=config,
        output_root=args.output_root,
        task_class=args.task_class,
        tier=args.tier,
        max_scenes=args.max_scenes,
        candidate_prefix=args.candidate_prefix,
        execute=bool(args.execute),
        publish=not bool(args.no_publish),
        capture_artifacts=not bool(args.no_capture),
        stage_ids=list(args.stage_id or []) or None,
        stop_after_stage=args.stop_after_stage,
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.success or not args.execute else 1


def _agent_plan(args: argparse.Namespace) -> int:
    state = load_experiment_state_from_roots(
        objective=args.objective,
        history_roots=list(args.history_root or []),
    )
    decision = decide_next_action(state)
    assert_agent_context_safe(decision.agent_prompt_context)
    history_node = summarize_experiment_history_node(
        objective=args.objective,
        history_roots=list(args.history_root or []),
    )
    decision_node = plan_next_action_node(state)
    nodes = [
        history_node.to_dict(),
        decision_node.to_dict(),
    ]
    if decision.strategy == STRATEGY_NEW_SKILL:
        nodes.append(propose_new_skill_spec_node(state=state, decision=decision).to_dict())
    payload = {
        "schema": "ksm.agent_controller.offline_plan.v1",
        "objective": args.objective,
        "node_catalog": node_catalog_payload(),
        "nodes": nodes,
        "state": state.to_controller_dict(),
        "decision": decision.to_dict(),
    }
    if args.out:
        write_json(args.out, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _json_object(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON must decode to an object")
    return data


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
