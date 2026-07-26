from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .io import read_yaml
from .registry import NAMESPACES, ToolRegistry, collect_actions


FORBIDDEN_TEXT = [
    "../",
    "..\\",
    "__import__",
    "subprocess",
    "os.",
    "open(",
    "exec(",
    "eval(",
    "sim.data",
    "env.handle",
]


@dataclass(frozen=True)
class PolicyResult:
    ok: bool
    actions: list[str]
    violations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_skill(path: str | Path, registry: ToolRegistry) -> PolicyResult:
    skill_path = Path(path)
    text = skill_path.read_text(encoding="utf-8")
    data = read_yaml(skill_path)
    violations: list[str] = []
    if not isinstance(data, dict):
        return PolicyResult(False, [], ["YAML root must be a mapping"])
    if not isinstance(data.get("workflow"), list):
        violations.append("YAML must contain workflow list")
    else:
        violations.extend(validate_workflow_nodes(data.get("workflow") or []))
    for pattern in FORBIDDEN_TEXT:
        if pattern.lower() in text.lower():
            violations.append(f"forbidden text pattern: {pattern}")
    actions = collect_actions(data.get("workflow") or [])
    for action in actions:
        violations.extend(validate_action(action, registry))
    violations.extend(validate_action_args(data, registry))
    return PolicyResult(ok=not violations, actions=actions, violations=sorted(set(violations)))


def validate_workflow_nodes(node: Any, *, path: str = "workflow") -> list[str]:
    violations: list[str] = []
    if isinstance(node, list):
        for index, item in enumerate(node):
            violations.extend(validate_workflow_nodes(item, path=f"{path}[{index}]"))
        return violations
    if not isinstance(node, dict):
        violations.append(f"{path} must be a mapping step")
        return violations
    executable_keys = {"action", "assert", "if", "while", "del"}
    if not any(key in node for key in executable_keys):
        violations.append(f"{path} has no executable key: expected action/assert/if/while/del")
    action = node.get("action")
    if isinstance(action, str) and action.endswith("/reasoning/identity") and "output" not in node:
        violations.append(f"{path} identity action must declare output; no-output identity is a no-op")
    for branch in ("workflow", "do", "else"):
        if branch in node:
            violations.extend(validate_workflow_nodes(node[branch], path=f"{path}.{branch}"))
    if "if" in node:
        if_node = node.get("if")
        if not isinstance(if_node, dict):
            violations.append(f"{path}.if must be a mapping with condition and do")
        else:
            if "condition" not in if_node:
                violations.append(f"{path}.if must declare condition")
            if not isinstance(if_node.get("do"), list):
                violations.append(f"{path}.if.do must be a workflow list")
            else:
                violations.extend(validate_workflow_nodes(if_node["do"], path=f"{path}.if.do"))
            if "else" in if_node:
                violations.extend(validate_workflow_nodes(if_node["else"], path=f"{path}.if.else"))
    return violations


def validate_action(action: str, registry: ToolRegistry) -> list[str]:
    if not isinstance(action, str) or not action.strip():
        return ["empty action"]
    action = action.strip()
    if action.startswith("="):
        return []
    if "\\" in action or ".." in action or action.startswith("/knowin_skills/"):
        return [f"unsafe action path: {action}"]
    if action.startswith("/ctrl/"):
        name = action.removeprefix("/ctrl/")
        return [] if name in registry.ctrl else [f"unknown ctrl action: {action}"]
    if action.startswith("/info/"):
        name = action.removeprefix("/info/")
        return [] if name in registry.info else [f"unknown info action: {action}"]
    if "/reasoning/" in action:
        namespace, service = action.split("/reasoning/", 1)
        namespace = namespace.strip("/")
        if namespace not in NAMESPACES:
            return [f"unknown reasoning namespace: {action}"]
        return [] if service in registry.reasoning else [f"unknown reasoning service: {action}"]
    if action.endswith((".yaml", ".yml")):
        if action.startswith("knowin_skills/"):
            return [f"subskill action must be relative under knowin_skills: {action}"]
        if action in registry.skill_paths:
            return []
        if action.startswith(registry.test_skill_dir.removeprefix("knowin_skills/") + "/"):
            return []
        return [f"unknown subskill: {action}"]
    return [f"unsupported action: {action}"]


def validate_action_args(skill: dict[str, Any], registry: ToolRegistry) -> list[str]:
    top_args = skill.get("args") if isinstance(skill.get("args"), dict) else {}
    violations: list[str] = []
    skill_args_by_path = {summary.path: set(summary.args) for summary in registry.skills if summary.args}
    endpoint_args = observed_endpoint_args(registry)
    enforce_endpoint_contract = bool(endpoint_args)
    for action, args in _iter_action_calls(skill.get("workflow") or []):
        if enforce_endpoint_contract and _is_public_endpoint(action) and action not in endpoint_args:
            violations.append(f"endpoint '{action}' is not called by stable KW skills; direct use lacks an observed argument contract")
        for key, value in args.items():
            reason = _unsupported_action_arg(key, value, top_args)
            if reason:
                violations.append(reason)
            if _should_validate_declared_subskill_args(action, registry, skill_args_by_path):
                allowed = skill_args_by_path.get(str(action), set())
                if key not in allowed:
                    violations.append(f"action arg '{key}' is not declared by subskill '{action}'")
            if _should_validate_observed_endpoint_args(action, endpoint_args):
                allowed = endpoint_args.get(str(action), set())
                if key not in allowed:
                    violations.append(f"action arg '{key}' has not been observed for endpoint '{action}' in stable KW skills")
    return violations


def observed_endpoint_args(registry: ToolRegistry) -> dict[str, set[str]]:
    root = Path(registry.k1_dir) / "knowin_skills"
    observed: dict[str, set[str]] = {}
    if not root.exists():
        return observed
    test_prefix = registry.test_skill_dir.removeprefix("knowin_skills/").strip("/")
    for path in root.rglob("*.yaml"):
        rel = path.relative_to(root).as_posix()
        if test_prefix and (rel == test_prefix or rel.startswith(f"{test_prefix}/")):
            continue
        try:
            data = read_yaml(path) or {}
        except Exception:
            continue
        if isinstance(data, dict):
            for action, args in _iter_action_calls(data.get("workflow") or []):
                if _is_public_endpoint(action):
                    observed.setdefault(action, set()).update(str(key) for key in args)
    return observed


def _should_validate_observed_endpoint_args(action: str, endpoint_args: dict[str, set[str]]) -> bool:
    return _is_public_endpoint(action) and action in endpoint_args


def _is_public_endpoint(action: str) -> bool:
    if not isinstance(action, str) or action.startswith("="):
        return False
    return action.startswith("/ctrl/") or action.startswith("/info/") or "/reasoning/" in action


def _iter_action_calls(node: Any) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_iter_action_calls(item))
        return found
    if not isinstance(node, dict):
        return found
    action = node.get("action")
    if isinstance(action, str) and isinstance(node.get("args"), dict):
        found.append((action, node["args"]))
    for branch in ("workflow", "do", "else"):
        if branch in node:
            found.extend(_iter_action_calls(node[branch]))
    if isinstance(node.get("if"), dict):
        found.extend(_iter_action_calls(node["if"]))
    if isinstance(node.get("while"), dict):
        found.extend(_iter_action_calls(node["while"]))
    return found


def _should_validate_declared_subskill_args(action: str, registry: ToolRegistry, skill_args_by_path: dict[str, set[str]]) -> bool:
    if not isinstance(action, str) or not action.endswith((".yaml", ".yml")):
        return False
    if action.startswith("="):
        return False
    if action.startswith(registry.test_skill_dir.removeprefix("knowin_skills/") + "/"):
        return False
    return action in skill_args_by_path


def _unsupported_action_arg(key: str, value: Any, top_args: dict[str, Any]) -> str:
    if isinstance(value, dict):
        return f"action arg '{key}' must not be a nested mapping; pass primitive/list values or omit it"
    if isinstance(value, list) and any(isinstance(item, dict) for item in value):
        return f"action arg '{key}' list must not contain nested mappings"
    if isinstance(value, str):
        ref = _top_arg_reference(value)
        if ref and isinstance(top_args.get(ref), dict):
            return f"action arg '{key}' must not pass structured top-level arg '{ref}'"
    return ""


def _top_arg_reference(value: str) -> str:
    text = value.strip()
    if not text.startswith("="):
        return ""
    expr = text.removeprefix("=").strip()
    if not expr.startswith("args."):
        return ""
    name = expr.removeprefix("args.").strip()
    return name if name.replace("_", "").isalnum() else ""
