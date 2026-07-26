from __future__ import annotations

import json
from typing import Any

from .sanitize import sanitize_aspire_output


def build_feedback_prompt(
    *,
    task: dict[str, Any],
    candidate_manifest: dict[str, Any],
    episode_report: dict[str, Any],
    registry_summary: dict[str, Any],
) -> str:
    compact_registry = {
        "ctrl": registry_summary.get("ctrl", []),
        "info": registry_summary.get("info", []),
        "reasoning": registry_summary.get("reasoning", []),
        "test_skill_dir": registry_summary.get("test_skill_dir"),
        "skills": [
            {
                "path": item.get("path"),
                "description": item.get("description"),
                "args": item.get("args"),
            }
            for item in (registry_summary.get("skills") or [])[:40]
            if isinstance(item, dict)
        ],
    }
    payload = {
        "task": task,
        "candidate_manifest": candidate_manifest,
        "episode_report": {
            "success": episode_report.get("success"),
            "failure_signature": episode_report.get("failure_signature"),
            "policy_ok": episode_report.get("policy_ok"),
            "metadata": episode_report.get("metadata"),
        },
        "available_tools": compact_registry,
    }
    return """
You are improving a knowin-world YAML skill candidate using ASPIRE-style feedback.

Return one strict JSON object with:
- hypothesis
- change_summary
- expected_failure_modes
- skill_args
- skill_yaml

Important constraints:
- Generate a KW YAML workflow, not Python robot code.
- First-stage scope is one continuous top-level skill.
- Use only listed KW actions, subskills, and workflow constructs.
- Do not access simulator internals or files outside the skill tree.

Context:
""".strip() + "\n" + json.dumps(sanitize_aspire_output(payload), indent=2, ensure_ascii=False)
