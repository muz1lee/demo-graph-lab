from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .feedback_attribution import (
    agent_safe_payload,
    confirmed_success_evidence,
    success_evidence_level,
    untrusted_reported_success,
)
from .io import safe_id, write_json


SCHEMA = "ksm.skill_library.context_packet.v1"
ENTRY_SCHEMA = "ksm.skill_library.evidence_entry.v1"


def build_skill_context_packet(
    *,
    root: str | Path,
    task: dict[str, Any],
    history: dict[str, Any],
    extra_roots: list[str | Path] | None = None,
    top_k: int = 4,
    snippet_chars: int = 1200,
    max_chars: int = 6000,
) -> dict[str, Any]:
    roots = [Path(root).expanduser()]
    roots.extend(Path(item).expanduser() for item in (extra_roots or []))
    query = _query_text(task=task, history=history)
    entries = []
    for base in roots:
        entries.extend(_scan_entries(base, snippet_chars=max(100, int(snippet_chars))))
    ranked = sorted(entries, key=lambda entry: (-_score(query, entry.get("search_text", "")), entry.get("path", "")))
    selected: list[dict[str, Any]] = []
    used_chars = 0
    for entry in ranked:
        if len(selected) >= max(0, int(top_k)):
            break
        public = {key: value for key, value in entry.items() if key != "search_text"}
        entry_chars = len(str(public.get("snippet") or ""))
        if selected and used_chars + entry_chars > max(100, int(max_chars)):
            break
        selected.append(public)
        used_chars += entry_chars
    return {
        "schema": SCHEMA,
        "roots": [str(path) for path in roots],
        "top_k": int(top_k),
        "snippet_chars": int(snippet_chars),
        "max_chars": int(max_chars),
        "entry_count": len(entries),
        "selected": selected,
    }


def distill_skill_library_entries(
    *,
    suite_run: dict[str, Any],
    output_root: str | Path,
    generation_index: int,
) -> list[dict[str, Any]]:
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index, episode in enumerate(suite_run.get("episodes", []) or [], start=1):
        if not isinstance(episode, dict):
            continue
        report = episode.get("report") if isinstance(episode.get("report"), dict) else {}
        candidate_id = str(report.get("candidate_id") or episode.get("candidate_id") or f"candidate_{index}")
        metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
        candidate_manifest = (
            metadata.get("candidate_manifest")
            if isinstance(metadata.get("candidate_manifest"), dict)
            else {}
        )
        feedback = (
            report.get("feedback_attribution")
            if isinstance(report.get("feedback_attribution"), dict)
            else {}
        )
        confirmed_success = confirmed_success_evidence(report)
        entry = {
            "schema": ENTRY_SCHEMA,
            "generation": int(generation_index),
            "candidate_id": candidate_id,
            "task_id": report.get("task_id") or episode.get("task_id"),
            "hypothesis": candidate_manifest.get("hypothesis"),
            "change_summary": candidate_manifest.get("change_summary"),
            "expected_failure_modes": candidate_manifest.get("expected_failure_modes") or [],
            "success": confirmed_success,
            "reported_success": bool(report.get("success")),
            "confirmed_success": confirmed_success,
            "success_evidence_level": success_evidence_level(report),
            "untrusted_success_evidence": untrusted_reported_success(report),
            "task_success": report.get("task_success"),
            "effect_success": report.get("effect_success"),
            "verifier_success": report.get("verifier_success"),
            "pipeline_success": report.get("pipeline_success"),
            "predicate_success": report.get("predicate_success"),
            "failure_signature": report.get("failure_signature"),
            "failure_analysis": report.get("failure_analysis"),
            "feedback_attribution": feedback,
            "trace_failure_breakdown": feedback.get("trace_failure_breakdown") or {},
            "subgoal_failure_breakdown": feedback.get("subgoal_failure_breakdown") or {},
            "recurring_summary": feedback.get("recurring_summary"),
            "evidence_gaps": feedback.get("evidence_gaps") or [],
            "negative_evidence": feedback.get("negative_evidence"),
            "artifacts": report.get("artifacts"),
            "metadata": {
                "episode_id": episode.get("episode_id"),
                "episode_dir": episode.get("episode_dir"),
            },
        }
        path = root / f"generation_{int(generation_index):02d}_{index:03d}_{safe_id(candidate_id)}.json"
        write_json(path, entry)
        entry["path"] = str(path)
        entries.append(entry)
    if entries:
        write_json(
            root / f"generation_{int(generation_index):02d}_index.json",
            {
                "schema": "ksm.skill_library.evidence_index.v1",
                "generation": int(generation_index),
                "entries": [{"candidate_id": item["candidate_id"], "path": item["path"]} for item in entries],
            },
        )
    return entries


def _scan_entries(root: Path, *, snippet_chars: int) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".yaml", ".yml", ".txt", ".py"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        text = _sanitized_entry_text(path=path, text=text)
        snippet = text[: max(100, int(snippet_chars))]
        entries.append(
            {
                "path": str(path),
                "kind": path.suffix.lower().lstrip("."),
                "snippet": snippet,
                "search_text": f"{path.name}\n{snippet}",
            }
        )
    return entries


def _sanitized_entry_text(*, path: Path, text: str) -> str:
    if path.suffix.lower() != ".json":
        return text
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(data, dict) or data.get("schema") != ENTRY_SCHEMA:
        return text
    confirmed_success = confirmed_success_evidence(data)
    if data.get("success") is True and not confirmed_success:
        data["success"] = False
        data["reported_success"] = True
        data["confirmed_success"] = False
        data["success_evidence_level"] = "reported_success_without_effect_evidence"
        data["untrusted_success_evidence"] = True
        data["success_memory_policy"] = (
            "excluded_from_success_memory_until task/effect evidence is confirmed outside agent repair feedback"
        )
    safe_data = agent_safe_payload(data)
    return json.dumps(safe_data, ensure_ascii=False, indent=2, sort_keys=True)


def _query_text(*, task: dict[str, Any], history: dict[str, Any]) -> str:
    payload = {
        "task": task,
        "leaderboard": agent_safe_payload(history.get("leaderboard")) if isinstance(history, dict) else {},
        "evaluation_summary": agent_safe_payload(history.get("evaluation_summary")) if isinstance(history, dict) else {},
    }
    return json.dumps(payload, ensure_ascii=False).lower()


def _score(query: str, text: str) -> int:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0
    text_tokens = _tokens(text.lower())
    return sum(1 for token in query_tokens if token in text_tokens)


def _tokens(text: str) -> set[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {token for token in normalized.split() if len(token) >= 3}
