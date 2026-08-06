"""Run the three-arm constraint-guided CaP code-generation experiment."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ..common import artifacts, llm
from . import backchain
from .compiler import compile_prompt, dry_run, static_check
from .program import compile_program, validate_program


def _program_for_mode(source: dict, graph: dict, mode: str) -> dict:
    """Derive one arm from a shared generated action-and-wiring skeleton."""
    program = deepcopy(source)
    contexts = backchain.selection_context(graph, mode=mode)
    stages = program.get("stages")
    if not isinstance(stages, list):
        return program
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        context = contexts.get(stage.get("index"))
        if context is None:
            stage.pop("selection", None)
            continue
        grasp_holes = context["grasp_holes"]
        if len(grasp_holes) != 1:
            raise ValueError(
                f"stage {stage.get('index')} must expose exactly one grasp hole"
            )
        stage["selection"] = {
            "grasp_hole": grasp_holes[0],
            "current_constraints": list(context["current_constraints"]),
            "downstream_constraints": list(context["downstream_constraints"]),
        }
    return program


def _generation(
    graph: dict,
    output_dir: Path,
    *,
    repeat: int,
    model: str | None,
) -> tuple[dict | None, dict]:
    """Generate one shared StageProgram skeleton for all three arms."""
    tag = f"shared_{repeat:03d}"
    prompt = compile_prompt(graph, selection_mode="vanilla")
    messages = [{"role": "user", "content": prompt}]
    generation = {
        "tag": tag,
        "repeat": repeat,
        "valid": False,
        "violations": [],
    }
    try:
        raw = llm.chat(
            messages,
            output_dir,
            tag=tag,
            model=model,
            # Reasoning models can consume several thousand internal tokens
            # before emitting the six-stage JSON program.
            max_tokens=20000,
            temperature=0.1,
            role="cap_shared_program_ablation",
            input_refs=["graph.json", "package:prompts/compile_policy.md"],
        )
    except Exception as error:
        generation["violations"] = [
            f"model_call:{type(error).__name__}:{error}"
        ]
        return None, generation
    try:
        source = llm.parse_json_block(raw)
    except ValueError as error:
        generation["violations"] = [str(error)]
        llm.record_result(output_dir, tag, parse_error=str(error))
        return None, generation

    violations = validate_program(source, graph, selection_mode="vanilla")
    llm.record_result(
        output_dir, tag, parsed=source, validation_errors=violations,
    )
    source_ref = Path("programs") / f"{tag}.json"
    artifacts.write_json(output_dir / source_ref, source)
    generation.update(
        valid=not violations,
        violations=violations,
        program_ref=str(source_ref),
    )
    return source, generation


def _attempt(
    graph: dict,
    output_dir: Path,
    *,
    mode: str,
    repeat: int,
    source: dict,
    generation_tag: str,
) -> dict:
    tag = f"{mode}_{repeat:03d}"
    result = {
        "tag": tag,
        "mode": mode,
        "repeat": repeat,
        "source_generation": generation_tag,
        "valid": False,
        "violations": [],
        "selection_calls": [],
        "action_ops": [],
    }
    try:
        program = _program_for_mode(source, graph, mode)
    except ValueError as error:
        result["violations"] = [str(error)]
        return result

    violations = validate_program(program, graph, selection_mode=mode)
    result["violations"] = violations
    artifacts.write_json(output_dir / "programs" / f"{tag}.json", program)
    if violations:
        return result

    code = compile_program(program, graph, selection_mode=mode)
    static_violations = static_check(code)
    result["violations"] = static_violations
    if static_violations:
        return result
    try:
        run = dry_run(code, graph)
    except Exception as error:
        result["violations"] = [f"dry_run:{type(error).__name__}:{error}"]
        return result
    if not (
        run.get("normal", {}).get("ok") is True
        and run.get("retry_injection", {}).get("ok") is True
    ):
        result["violations"] = ["dry_run did not pass both paths"]
        return result

    code_path = output_dir / "policies" / f"{tag}.py"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text(code)
    result.update(
        valid=True,
        policy_ref=str(code_path.relative_to(output_dir)),
        selection_calls=[
            line.strip()
            for line in code.splitlines()
            if any(f"rt.{name}(" in line for name in (
                "begin_candidates", "rank_by", "require_future", "choose",
            ))
        ],
        action_ops=[
            action["op"]
            for stage in program["stages"]
            for action in stage["actions"]
        ],
    )
    return result


def run(
    graph_path: str | Path,
    output_dir: str | Path,
    *,
    repeats: int = 3,
    model: str | None = None,
) -> Path:
    """Generate and validate paired vanilla/local/backchain CaP programs."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    graph_path = Path(graph_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    graph = artifacts.read_json(graph_path)
    artifacts.write_json(output_dir / "graph.json", graph)

    attempts = []
    generations = []
    summary_path = output_dir / "summary.json"
    for repeat in range(repeats):
        source, generation = _generation(
            graph, output_dir, repeat=repeat, model=model,
        )
        generations.append(generation)
        if source is None:
            for mode in backchain.MODES:
                attempts.append({
                    "tag": f"{mode}_{repeat:03d}",
                    "mode": mode,
                    "repeat": repeat,
                    "source_generation": generation["tag"],
                    "valid": False,
                    "violations": list(generation["violations"]),
                    "selection_calls": [],
                    "action_ops": [],
                })
        else:
            for mode in backchain.MODES:
                attempts.append(_attempt(
                    graph,
                    output_dir,
                    mode=mode,
                    repeat=repeat,
                    source=source,
                    generation_tag=generation["tag"],
                ))
        artifacts.write_json(summary_path, {
            "graph_ref": "graph.json",
            "repeats_requested": repeats,
            "generation_design": "one_shared_program_per_repeat",
            "generations": generations,
            "attempts": attempts,
        })

    by_mode = {
        mode: {
            "attempts": sum(item["mode"] == mode for item in attempts),
            "valid": sum(item["mode"] == mode and item["valid"] for item in attempts),
        }
        for mode in backchain.MODES
    }
    paired = []
    for repeat in range(repeats):
        group = {
            item["mode"]: item for item in attempts if item["repeat"] == repeat
        }
        paired.append({
            "repeat": repeat,
            "all_valid": all(group[mode]["valid"] for mode in backchain.MODES),
            "action_ops_equal": (
                len({tuple(group[mode]["action_ops"])
                     for mode in backchain.MODES}) == 1
                if all(group[mode]["valid"] for mode in backchain.MODES)
                else None
            ),
            "selection_code_differs": (
                len({tuple(group[mode]["selection_calls"])
                     for mode in backchain.MODES}) == len(backchain.MODES)
                if all(group[mode]["valid"] for mode in backchain.MODES)
                else None
            ),
        })
    artifacts.write_json(summary_path, {
        "graph_ref": "graph.json",
        "repeats_requested": repeats,
        "generation_design": "one_shared_program_per_repeat",
        "generations": generations,
        "modes": by_mode,
        "paired": paired,
        "attempts": attempts,
    })
    return summary_path
