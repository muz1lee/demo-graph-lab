from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .io import read_yaml, safe_id, write_yaml
from .registry import ToolRegistry


@dataclass(frozen=True)
class GeneratedSkill:
    candidate_id: str
    task_id: str
    template: str
    local_path: str
    skill: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_skill_from_task(
    *,
    task_path: str | Path,
    candidate_id: str,
    output_dir: str | Path,
    registry: ToolRegistry,
) -> GeneratedSkill:
    task_file = Path(task_path).expanduser().resolve()
    task = read_yaml(task_file)
    if not isinstance(task, dict):
        raise ValueError(f"task must be a mapping: {task_file}")
    template = str(task.get("template") or "").strip()
    task_id = safe_id(str(task.get("task_id") or task_file.stem))
    cleaned_id = safe_id(candidate_id)
    args = dict(task.get("args") or {}) if isinstance(task.get("args"), dict) else {}
    if template == "pickplace_wrapper":
        skill = _pickplace_wrapper(cleaned_id, task, args)
    elif template == "go_home":
        skill = _go_home(cleaned_id, task, args)
    elif template == "set_gripper":
        skill = _set_gripper(cleaned_id, task, args)
    elif template == "assert_true":
        skill = _assert_true(cleaned_id, task)
    else:
        raise ValueError(f"unsupported template: {template}")

    target = Path(output_dir).expanduser().resolve() / f"{cleaned_id}.yaml"
    write_yaml(target, skill)
    _ensure_generated_skill_uses_registry(skill, registry)
    return GeneratedSkill(
        candidate_id=cleaned_id,
        task_id=task_id,
        template=template,
        local_path=str(target),
        skill=skill,
    )


def _pickplace_wrapper(candidate_id: str, task: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "name": candidate_id,
        "description": str(task.get("description") or "KSM generated pickplace wrapper."),
        "args": {
            "arm_id": int(args.get("arm_id", 0)),
            "pick_label": str(args.get("pick_label", "")),
            "place_label": str(args.get("place_label", "")),
        },
        "workflow": [
            {
                "action": "pickplace/semantic_pickplace.yaml",
                "description": "delegate to KW semantic pickplace",
                "args": {
                    "arm_id": "= args.arm_id",
                    "pick_label": "= args.pick_label",
                    "place_label": "= args.place_label",
                },
            }
        ],
    }


def _go_home(candidate_id: str, task: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "name": candidate_id,
        "description": str(task.get("description") or "KSM generated go_home skill."),
        "args": {"arm_id": int(args.get("arm_id", 0))},
        "workflow": [
            {
                "action": "/ctrl/go_home",
                "description": "move selected arm to home pose",
                "args": {"arm_id": "= args.arm_id"},
            }
        ],
    }


def _set_gripper(candidate_id: str, task: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "name": candidate_id,
        "description": str(task.get("description") or "KSM generated set_gripper skill."),
        "args": {
            "arm_id": int(args.get("arm_id", 0)),
            "angle": float(args.get("angle", 50.0)),
            "check": bool(args.get("check", False)),
        },
        "workflow": [
            {
                "action": "/ctrl/set_gripper",
                "description": "move selected gripper",
                "args": {
                    "arm_id": "= args.arm_id",
                    "angle": "= args.angle",
                    "check": "= args.check",
                },
            }
        ],
    }


def _assert_true(candidate_id: str, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "name": candidate_id,
        "description": str(task.get("description") or "KSM generated pipeline assertion smoke."),
        "args": {},
        "workflow": [
            {
                "assert": "= True",
                "report": "KSM generated YAML executed successfully",
            }
        ],
    }


def _ensure_generated_skill_uses_registry(skill: dict[str, Any], registry: ToolRegistry) -> None:
    # This is a cheap template sanity check. Full validation lives in policy.py.
    actions = []
    for step in skill.get("workflow") or []:
        if isinstance(step, dict) and isinstance(step.get("action"), str):
            actions.append(step["action"])
    if "pickplace/semantic_pickplace.yaml" in actions and "pickplace/semantic_pickplace.yaml" not in registry.skill_paths:
        raise ValueError("KW registry does not contain pickplace/semantic_pickplace.yaml")
    if "/ctrl/go_home" in actions and "go_home" not in registry.ctrl:
        raise ValueError("KW registry does not contain /ctrl/go_home")
    if "/ctrl/set_gripper" in actions and "set_gripper" not in registry.ctrl:
        raise ValueError("KW registry does not contain /ctrl/set_gripper")
