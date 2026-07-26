from __future__ import annotations

import json
import re
from typing import Any


_AUTH_FAILURE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])401(?![A-Za-z0-9_.-])|"
    r"(?<![A-Za-z0-9_])unauthorized(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_GROUNDING_FAILURE_RE = re.compile(
    r"cannot find the object|grounding failed|failed to ground|qwen grounding failed",
    re.IGNORECASE,
)


def _has_auth_failure(text: str | None) -> bool:
    return bool(_AUTH_FAILURE_RE.search(text or ""))


def _has_grounding_failure(text: str | None) -> bool:
    return bool(_GROUNDING_FAILURE_RE.search(text or ""))


def classify_failure(
    *,
    policy_ok: bool,
    execute: bool,
    run_result: Any | None,
    run_error: str | None,
) -> str:
    if not policy_ok:
        return "static_policy_violation"
    if not execute:
        return "dry_run"
    if run_error:
        lowered = run_error.lower()
        if _has_auth_failure(lowered):
            return "reasoning_unauthorized"
        return "execution_exception"
    if run_result is None:
        return "missing_run_result"

    status_text = json.dumps(getattr(run_result, "final_status", {}), ensure_ascii=False)
    lowered = status_text.lower()
    if _has_auth_failure(lowered):
        return "reasoning_unauthorized"
    if "failed to fetch current frame" in lowered:
        return "frame_capture_failed"
    if _has_grounding_failure(lowered):
        return "vision_grounding_failed"
    if "motion planning failed" in lowered or "motion_planning failed" in lowered:
        return "motion_planning_failed"
    if skill_success(getattr(run_result, "final_status", {})):
        return "success"
    return "skill_execution_failed"


def skill_success(status: dict[str, Any]) -> bool:
    response = status.get("response") if isinstance(status.get("response"), dict) else status
    success_items = response.get("success") if isinstance(response, dict) else None
    if isinstance(success_items, list) and success_items:
        latest = success_items[-1]
        return bool(isinstance(latest, dict) and latest.get("success"))
    return bool(response.get("success")) if isinstance(response, dict) else False
