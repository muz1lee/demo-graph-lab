from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import LLMConfig
from .agent_skill_spec import (
    ROLE_REUSE_EXISTING,
    canonical_candidate_role,
    task_new_skill_spec_prompt_payload,
    validate_new_skill_spec_contract,
    validate_skill_role_contract,
)
from .generator import GeneratedSkill
from .io import read_yaml, safe_id, write_json, write_yaml
from .llm import GPTChatClient, StaticResponseChatClient, resolve_llm_config
from .policy import check_skill, observed_endpoint_args
from .prompt_contract import build_stage_level_prompt_contract, extract_stage_context
from .registry import ToolRegistry, collect_actions
from .feedback_attribution import agent_safe_payload


def generate_skill_from_task_llm(
    *,
    task_path: str | Path,
    candidate_id: str,
    output_dir: str | Path,
    registry: ToolRegistry,
    llm_config: LLMConfig,
    llm_overrides: dict[str, Any] | None = None,
    response_file: str | Path | None = None,
    max_attempts: int = 2,
    client: Any | None = None,
    prompt_override: str | None = None,
    repair_memory: dict[str, Any] | None = None,
) -> GeneratedSkill:
    task_file = Path(task_path).expanduser().resolve()
    task = read_yaml(task_file)
    if not isinstance(task, dict):
        raise ValueError(f"task must be a mapping: {task_file}")
    cleaned_id = safe_id(candidate_id)
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    prompt_path = out / f"{cleaned_id}.llm_prompt.txt"
    response_path = out / f"{cleaned_id}.llm_response.json"
    scratch_path = out / f"{cleaned_id}.yaml"
    settings = resolve_llm_config(llm_config, llm_overrides)
    chat_client = client or (
        StaticResponseChatClient.from_file(response_file)
        if response_file
        else GPTChatClient(settings)
    )
    prompt = str(prompt_override).strip() if prompt_override else build_llm_generation_prompt(
        task=task,
        candidate_id=cleaned_id,
        registry=registry,
    )
    prompt_path.write_text(prompt, encoding="utf-8")

    last_error = ""
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        messages = _messages(prompt, last_error)
        response = chat_client.complete(messages)
        attempt_record: dict[str, Any] = {
            "attempt": attempt,
            "provider": response.provider,
            "model": response.model,
            "settings": settings.safe_dict(),
            "text": response.text,
        }
        try:
            payload = extract_llm_json(response.text)
            skill_yaml = extract_skill_yaml(payload)
            skill = _parse_skill_yaml(skill_yaml)
            if isinstance(payload.get("skill_args"), dict):
                _merge_payload_skill_args(skill, payload["skill_args"])
            _normalize_skill_defaults(skill, cleaned_id, task)
            normalizations = normalize_generated_action_args(skill)
            write_yaml(scratch_path, skill)
            policy = check_skill(scratch_path, registry)
            contract_violations = validate_task_contract(skill=skill, task=task, payload=payload)
            contract_violations.extend(validate_skill_role_contract(skill=skill, payload=payload))
            contract_violations.extend(validate_new_skill_spec_contract(skill=skill, task=task, payload=payload))
            attempt_record["payload"] = payload
            if normalizations:
                attempt_record["normalizations"] = normalizations
            attempt_record["policy"] = policy.to_dict()
            if contract_violations:
                attempt_record["task_contract_violations"] = contract_violations
            attempts.append(attempt_record)
            write_json(response_path, {"attempts": attempts})
            if not policy.ok:
                last_error = "policy violations: " + "; ".join(policy.violations)
                continue
            if contract_violations:
                last_error = "task contract violations: " + "; ".join(contract_violations)
                continue
            return GeneratedSkill(
                candidate_id=cleaned_id,
                task_id=safe_id(str(task.get("task_id") or task_file.stem)),
                template="llm",
                local_path=str(scratch_path),
                skill=skill,
                metadata={
                    "prompt_path": str(prompt_path),
                    "response_path": str(response_path),
                    "attempts": attempt,
                    "hypothesis": str(payload.get("hypothesis") or ""),
                    "change_summary": str(payload.get("change_summary") or ""),
                    "expected_failure_modes": _string_list(payload.get("expected_failure_modes")),
                    "skill_reuse_decision": (
                        payload.get("skill_reuse_decision")
                        if isinstance(payload.get("skill_reuse_decision"), dict)
                        else {}
                    ),
                    "skill_args": payload.get("skill_args") if isinstance(payload.get("skill_args"), dict) else {},
                    "normalizations": normalizations,
                    "llm": {"provider": response.provider, "model": response.model},
                    "prompt_override": bool(prompt_override),
                    "task_context": _task_context(task),
                },
            )
        except Exception as exc:
            last_error = repr(exc)
            attempt_record["error"] = last_error
            attempts.append(attempt_record)
            write_json(response_path, {"attempts": attempts})
    raise RuntimeError(f"failed to generate a valid KW YAML skill after {max_attempts} LLM attempt(s): {last_error}")


def normalize_generated_action_args(skill: dict[str, Any]) -> list[str]:
    """Apply generator-level normalizations that mirror static policy.

    KSM policy does not allow nested action args because KW endpoint/subskill
    calls expect primitive/list values or expressions. Keeping this as a
    generator normalization prevents ASPIRE from wasting repair attempts on
    structured defaults such as ``gripper`` or ``planner_config``; those should
    be omitted so the called stable skill can use its own defaults.
    """

    workflow = skill.get("workflow") if isinstance(skill.get("workflow"), list) else []
    removed: list[str] = []
    _strip_nested_action_args(workflow, path="workflow", removed=removed)
    return removed


def _strip_nested_action_args(node: Any, *, path: str, removed: list[str]) -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _strip_nested_action_args(item, path=f"{path}[{index}]", removed=removed)
        return
    if not isinstance(node, dict):
        return
    args = node.get("args")
    if isinstance(args, dict):
        for key, value in list(args.items()):
            if _contains_mapping(value):
                action = str(node.get("action") or "<no-action>")
                removed.append(f"{path}.args.{key} removed from {action}: nested action args are policy-disallowed")
                del args[key]
    for branch in ("workflow", "do", "else"):
        if branch in node:
            _strip_nested_action_args(node[branch], path=f"{path}.{branch}", removed=removed)
    if isinstance(node.get("if"), dict):
        _strip_nested_action_args(node["if"], path=f"{path}.if", removed=removed)
    if isinstance(node.get("while"), dict):
        _strip_nested_action_args(node["while"], path=f"{path}.while", removed=removed)


def _contains_mapping(value: Any) -> bool:
    if isinstance(value, dict):
        return True
    if isinstance(value, list):
        return any(_contains_mapping(item) for item in value)
    return False


def validate_task_contract(*, skill: dict[str, Any], task: dict[str, Any], payload: dict[str, Any] | None = None) -> list[str]:
    violations: list[str] = []
    stateful_plan = _stateful_plan_from_task(task)
    steps = stateful_plan.get("steps") if isinstance(stateful_plan, dict) else None
    if not isinstance(steps, list) or len(steps) < 2:
        return violations

    actions = collect_actions(skill.get("workflow") or [])
    stack_operation_count = sum(
        1
        for action in actions
        if action in {"pickplace/semantic_pickplace.yaml", "pickplace/semantic_place.yaml"}
    )
    if stack_operation_count < len(steps):
        violations.append(
            f"stateful_plan requires at least {len(steps)} stack/place operations, found {stack_operation_count}"
        )

    top_args = skill.get("args") if isinstance(skill.get("args"), dict) else {}
    workflow = skill.get("workflow") if isinstance(skill.get("workflow"), list) else []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        bindings = step.get("arg_bindings") if isinstance(step.get("arg_bindings"), dict) else {}
        for label_role in ("pick_label", "place_label"):
            arg_name = str(bindings.get(label_role) or "").strip()
            if not arg_name:
                violations.append(f"stateful_plan step {index} missing arg binding for {label_role}")
                continue
            if arg_name not in top_args:
                violations.append(f"stateful_plan arg '{arg_name}' is missing from skill args")
            if not _workflow_references_arg(workflow, arg_name):
                violations.append(f"stateful_plan arg '{arg_name}' is not used by workflow")

    decision = payload.get("skill_reuse_decision") if isinstance(payload, dict) and isinstance(payload.get("skill_reuse_decision"), dict) else {}
    role = canonical_candidate_role(str(decision.get("candidate_role") or "").strip())
    if role == ROLE_REUSE_EXISTING:
        violations.append("stateful repeated plan cannot be reported as reuse_existing_skill")
    return violations


def _stateful_plan_from_task(task: dict[str, Any]) -> dict[str, Any]:
    robodojo = task.get("robodojo") if isinstance(task.get("robodojo"), dict) else {}
    subtask = robodojo.get("subtask") if isinstance(robodojo.get("subtask"), dict) else {}
    plan = subtask.get("stateful_plan") if isinstance(subtask.get("stateful_plan"), dict) else {}
    if plan:
        return plan
    binding = robodojo.get("binding") if isinstance(robodojo.get("binding"), dict) else {}
    plan = binding.get("stateful_plan") if isinstance(binding.get("stateful_plan"), dict) else {}
    return plan


def _workflow_references_arg(node: Any, arg_name: str) -> bool:
    if isinstance(node, dict):
        return any(_workflow_references_arg(value, arg_name) for value in node.values())
    if isinstance(node, list):
        return any(_workflow_references_arg(value, arg_name) for value in node)
    if isinstance(node, str):
        return f"args.{arg_name}" in node or f"${{args.{arg_name}}}" in node
    return False


def build_llm_generation_prompt(
    *,
    task: dict[str, Any],
    candidate_id: str,
    registry: ToolRegistry,
) -> str:
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
            }
            for skill in registry.skills[:80]
        ],
        "endpoint_arg_contracts": _public_endpoint_arg_contracts(registry),
    }
    new_skill_spec = task_new_skill_spec_prompt_payload(task)
    prompt_task = agent_safe_payload(task)
    examples = [
        {
            "action": "pickplace/semantic_pickplace.yaml",
            "args": {"arm_id": "= args.arm_id", "pick_label": "= args.pick_label", "place_label": "= args.place_label"},
        },
        {
            "action": "/ctrl/go_home",
            "args": {"arm_id": "= args.arm_id"},
        },
    ]
    return f"""
You are generating a knowin-world KW YAML skill candidate for KSM.

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
First-stage scope: generate one continuous top-level skill. Do not invent a multi-skill planner yet.
Allowed workflow constructs include action, args, output, assert, if/do/else, while/do, del.
Do not access simulator internals, private state, shell commands, Python imports, files, seed-specific branches, or trial-specific hacks.
Subskill actions are relative paths under knowin_skills, for example pickplace/semantic_pickplace.yaml.
Endpoint actions may use /ctrl/, /info/, /head/reasoning/, /left_hand/reasoning/, /right_hand/reasoning/.
Before writing YAML, decide the skill boundary:
- `reuse_existing_skill`: directly uses an existing mature skill. Useful as baseline/reuse, not a new skill candidate.
- `skill_specialization`: may call existing skills internally but adds a reusable task-family interface, constraints, effects, observable success, and failure modes.
- `new_behavior_skill`: changes the mechanism using available public actions because existing skills do not express the key behavior.
- `blocked_by_gap`: public KW tools lack a required primitive.
Prefer existing high-level KW skills when they truly express the requested behavior. Do not call a relation-specific task solved merely because generic pickplace can move an object.
If an existing skill is reused without a new effect/interface, mark candidate_role as reuse_existing_skill.
If a missing primitive blocks the task, return a minimal legal diagnostic YAML and explain the missing primitive in skill_reuse_decision instead of fabricating fake success behavior.
If Agent controller new-skill spec is present, treat it as the current skill boundary request:
- generate a candidate consistent with reusable_interface, candidate_intent, reuse_policy, and not_allowed;
- do not downgrade it to reuse_existing_skill unless the correct answer is blocked_by_gap;
- skill_specialization may reuse existing KW skills, but must declare the added behavior contract beyond forwarding args;
- still reuse existing KW skills for stable substeps when the spec says to do so.
- for skill_specialization, call the selected stable YAML skill(s) directly when they cover the mechanism; do not inline their internal /reasoning or /ctrl sequence unless endpoint_arg_contracts explicitly exposes those endpoints.
- reusable_interface.semantic_context_args, when present, are semantic context carried by the interface; do not invent fake actions only to consume those args.
Action args must be primitives, lists of primitives, or expressions resolving to primitives/lists. They must also be declared by the called subskill when calling a known YAML skill.
Direct endpoint actions (/ctrl, /info, /reasoning) must appear in endpoint_arg_contracts and must use only the listed argument names. If an endpoint is not listed, use an existing YAML skill wrapper or declare blocked_by_gap instead of guessing its args.
Do not pass nested maps/objects into action args. In particular, do not override structured args such as gripper; omit them and let the called KW skill use its defaults.
Do not invent planner_config, gripper_open_angle, hidden strategy maps, or other undeclared subskill args.
Do not add /reasoning/identity as a comment, verifier, or placeholder. Identity is only valid when it writes an output used by later workflow steps.

{build_stage_level_prompt_contract(task)}

Candidate id: {candidate_id}

Task:
{json.dumps(prompt_task, ensure_ascii=False, indent=2)}

Agent controller new-skill spec, if any:
{json.dumps(new_skill_spec, ensure_ascii=False, indent=2)}

Available KW tools and skills:
{json.dumps(registry_payload, ensure_ascii=False, indent=2)}

Action examples:
{json.dumps(examples, ensure_ascii=False, indent=2)}
""".strip()


def extract_llm_json(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.S)
        if fenced:
            data = json.loads(fenced.group(1))
        else:
            start = stripped.find("{")
            if start < 0:
                raise ValueError("LLM response did not contain a JSON object")
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(stripped[start:])
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON is not an object")
    return data


def extract_skill_yaml(payload: dict[str, Any]) -> str:
    skill_yaml = str(payload.get("skill_yaml") or "").strip()
    if not skill_yaml:
        raise ValueError("LLM payload missing non-empty skill_yaml")
    fenced = re.match(r"^```(?:yaml|yml)?\s*(.*?)\s*```$", skill_yaml, flags=re.S | re.I)
    if fenced:
        skill_yaml = fenced.group(1).strip()
    return skill_yaml


def _parse_skill_yaml(skill_yaml: str) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(skill_yaml)
    if isinstance(data, list):
        data = {"schema_version": "1.0.0", "args": {}, "workflow": data}
    elif isinstance(data, dict) and isinstance(data.get("actions"), list) and not isinstance(data.get("workflow"), list):
        data = dict(data)
        data["workflow"] = data.pop("actions")
    elif isinstance(data, dict) and "action" in data and not isinstance(data.get("workflow"), list):
        data = {"schema_version": "1.0.0", "args": {}, "workflow": [data]}
    if not isinstance(data, dict):
        raise ValueError("skill_yaml must decode to a YAML mapping or workflow list")
    if not isinstance(data.get("workflow"), list):
        raise ValueError("skill_yaml must contain workflow list")
    return data


def _merge_payload_skill_args(skill: dict[str, Any], skill_args: dict[str, Any]) -> None:
    args = skill.get("args")
    if not isinstance(args, dict):
        args = {}
        skill["args"] = args
    for key, value in skill_args.items():
        args.setdefault(str(key), value)


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


def _normalize_skill_defaults(skill: dict[str, Any], candidate_id: str, task: dict[str, Any]) -> None:
    skill.setdefault("schema_version", "1.0.0")
    skill["name"] = candidate_id
    skill.setdefault("description", str(task.get("description") or f"LLM generated candidate {candidate_id}"))
    if not isinstance(skill.get("args"), dict):
        skill["args"] = {}


def _task_context(task: dict[str, Any]) -> dict[str, Any]:
    return extract_stage_context(task)


def _messages(prompt: str, last_error: str) -> list[dict[str, Any]]:
    user = prompt
    if last_error:
        user += (
            "\n\nThe previous attempt was rejected by KSM validation:\n"
            f"{last_error}\n"
            "Return a corrected JSON object only."
        )
    return [
        {
            "role": "system",
            "content": "You generate safe, valid knowin-world YAML skills and return strict JSON only.",
        },
        {"role": "user", "content": user},
    ]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]
