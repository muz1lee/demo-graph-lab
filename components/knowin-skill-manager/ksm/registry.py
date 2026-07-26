from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from collections import Counter
from pathlib import Path
from typing import Any

from .config import ManagerConfig
from .io import read_yaml


CTRL_NAMES = [
    "disable_arm",
    "enable_arm",
    "cancel",
    "go_zero",
    "go_home",
    "go_rest",
    "set_gripper",
    "qpos_move",
    "xquat_move",
    "delta_move",
    "local_delta_move",
    "local_rotation_move",
    "follow_xquat_trajectory",
    "stream_xquat_trajectory",
    "follow_delta_trajectory",
    "dual_qpos_move",
    "dual_xquat_move",
    "dual_local_delta_move",
    "dual_follow_xquat_trajectory",
    "dual_stream_xquat_trajectory",
    "dual_follow_delta_trajectory",
]

INFO_NAMES = [
    "get_sensor_info",
    "get_arm_info",
    "is_gripping_sth",
    "calibrate_grippers",
    "get_qpos",
    "get_xquat",
    "get_ee_extforce",
]

NAMESPACES = ["head", "left_hand", "right_hand"]


@dataclass(frozen=True)
class SkillSummary:
    path: str
    description: str
    args: dict[str, Any]
    actions: list[str]

    @property
    def uses_reasoning(self) -> bool:
        return any("/reasoning/" in action for action in self.actions)

    @property
    def uses_control(self) -> bool:
        return any(action.startswith("/ctrl/") for action in self.actions)

    @property
    def is_composite(self) -> bool:
        return any(action.endswith(".yaml") for action in self.actions)

    @property
    def capabilities(self) -> list[str]:
        caps: set[str] = set()
        if self.uses_reasoning:
            caps.add("reasoning")
        if self.uses_control:
            caps.add("control")
        if self.is_composite:
            caps.add("composite")
        if "pick" in self.path:
            caps.add("pick")
        if "place" in self.path:
            caps.add("place")
        return sorted(caps)


@dataclass(frozen=True)
class ToolRegistry:
    k1_dir: str
    test_skill_dir: str
    ctrl: list[str]
    info: list[str]
    reasoning: list[str]
    namespaces: list[str]
    skills: list[SkillSummary]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["skills"] = [asdict(skill) for skill in self.skills]
        return payload

    @property
    def skill_paths(self) -> set[str]:
        return {skill.path for skill in self.skills}

    @property
    def capability_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for skill in self.skills:
            counts.update(skill.capabilities)
        return dict(sorted(counts.items()))


def build_registry(config: ManagerConfig) -> ToolRegistry:
    skills = scan_skills(config.k1_dir)
    reasoning = scan_reasoning_services(config.k1_dir)
    return ToolRegistry(
        k1_dir=str(config.k1_dir),
        test_skill_dir=config.test_skill_dir,
        ctrl=list(CTRL_NAMES),
        info=list(INFO_NAMES),
        reasoning=reasoning,
        namespaces=list(NAMESPACES),
        skills=skills,
    )


def scan_skills(k1_dir: Path) -> list[SkillSummary]:
    root = k1_dir / "knowin_skills"
    results: list[SkillSummary] = []
    if not root.exists():
        return results
    for path in sorted(root.rglob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        try:
            data = read_yaml(path) or {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        results.append(
            SkillSummary(
                path=rel,
                description=str(data.get("description") or ""),
                args=dict(data.get("args") or {}) if isinstance(data.get("args"), dict) else {},
                actions=collect_actions(data.get("workflow") or []),
            )
        )
    return results


def scan_reasoning_services(k1_dir: Path) -> list[str]:
    services_dir = k1_dir / "knowin_reasoner" / "services"
    names: set[str] = set()
    if not services_dir.exists():
        return []
    pattern = re.compile(r"@ServiceMeta\(service_name=[\"']([^\"']+)[\"']\)")
    for path in services_dir.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        names.update(pattern.findall(text))
    return sorted(names)


def collect_actions(node: Any) -> list[str]:
    actions: list[str] = []
    if isinstance(node, dict):
        action = node.get("action")
        if isinstance(action, str):
            actions.append(action)
        for key in ("workflow", "do", "else"):
            if key in node:
                actions.extend(collect_actions(node[key]))
        if "if" in node:
            actions.extend(collect_actions(node["if"]))
    elif isinstance(node, list):
        for item in node:
            actions.extend(collect_actions(item))
    return actions
