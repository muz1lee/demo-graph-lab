from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .io import read_yaml


@dataclass(frozen=True)
class ActionRef:
    action: str
    kind: str
    step_path: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillIR:
    source_path: str
    description: str
    args: dict[str, Any]
    workflow_steps: int
    actions: list[ActionRef] = field(default_factory=list)
    control_flow: dict[str, int] = field(default_factory=dict)

    def capabilities(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.kind] = counts.get(action.kind, 0) + 1
        return {
            "action_counts": counts,
            "control_flow": dict(self.control_flow),
            "subskills": [item.action for item in self.actions if item.kind == "subskill"],
            "reasoning": [item.action for item in self.actions if item.kind == "reasoning"],
            "control": [item.action for item in self.actions if item.kind == "control"],
            "info": [item.action for item in self.actions if item.kind == "info"],
            "dynamic_actions": [item.action for item in self.actions if item.kind == "dynamic_action"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "description": self.description,
            "args": self.args,
            "workflow_steps": self.workflow_steps,
            "actions": [item.to_dict() for item in self.actions],
            "capabilities": self.capabilities(),
        }


def load_skill_ir(path: str | Path) -> SkillIR:
    source = Path(path).expanduser().resolve()
    data = read_yaml(source)
    if not isinstance(data, dict):
        raise ValueError(f"KW skill YAML must be a mapping: {source}")
    workflow = data.get("workflow")
    if not isinstance(workflow, list):
        raise ValueError(f"KW skill YAML must contain workflow list: {source}")
    args = data.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError(f"KW skill args must be a mapping: {source}")
    actions: list[ActionRef] = []
    flow_counts = {"assert": 0, "if": 0, "while": 0, "del": 0}
    _walk_workflow(workflow, path="workflow", actions=actions, flow_counts=flow_counts)
    return SkillIR(
        source_path=str(source),
        description=str(data.get("description", "")),
        args=dict(args),
        workflow_steps=len(workflow),
        actions=actions,
        control_flow={key: value for key, value in flow_counts.items() if value},
    )


def classify_action(action: str) -> str:
    if not action:
        return "empty"
    if action.startswith("="):
        return "dynamic_action"
    if "/reasoning/" in action:
        return "reasoning"
    if action.startswith("/ctrl/"):
        return "control"
    if action.startswith("/info/"):
        return "info"
    if action.endswith((".yaml", ".yml")):
        return "subskill"
    return "unknown"


def _walk_workflow(
    workflow: list[Any],
    *,
    path: str,
    actions: list[ActionRef],
    flow_counts: dict[str, int],
) -> None:
    for index, step in enumerate(workflow):
        step_path = f"{path}[{index}]"
        if not isinstance(step, dict):
            continue
        if "assert" in step:
            flow_counts["assert"] += 1
        if "del" in step:
            flow_counts["del"] += 1
        if "action" in step:
            action = str(step.get("action", ""))
            actions.append(
                ActionRef(
                    action=action,
                    kind=classify_action(action),
                    step_path=step_path,
                    description=str(step.get("description", "")),
                )
            )
        if "if" in step and isinstance(step["if"], dict):
            flow_counts["if"] += 1
            if_do = step["if"].get("do", [])
            if isinstance(if_do, list):
                _walk_workflow(if_do, path=f"{step_path}.if.do", actions=actions, flow_counts=flow_counts)
            else_do = step.get("else", [])
            if isinstance(else_do, list):
                _walk_workflow(else_do, path=f"{step_path}.else", actions=actions, flow_counts=flow_counts)
        if "while" in step:
            flow_counts["while"] += 1
            loop_do = step.get("do", [])
            if isinstance(loop_do, list):
                _walk_workflow(loop_do, path=f"{step_path}.while.do", actions=actions, flow_counts=flow_counts)
