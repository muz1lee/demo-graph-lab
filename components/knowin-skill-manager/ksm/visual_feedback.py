from __future__ import annotations

import base64
import glob
import json
import os
import random
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .io import read_json


SCHEMA = "ksm.visual_feedback.v1"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


def build_visual_evidence(
    *,
    task_metadata: dict[str, Any],
    candidate_manifest: dict[str, Any],
    episode_dir: Path,
) -> dict[str, Any]:
    request = _visual_feedback_request(task_metadata=task_metadata, candidate_manifest=candidate_manifest)
    artifacts = _visual_artifacts(request)
    analysis_path = _analysis_path(request, episode_dir)
    if analysis_path:
        analysis = _load_analysis(analysis_path)
        if analysis:
            return {
                "schema": SCHEMA,
                "status": "analyzed",
                "provider": str(request.get("provider") or analysis.get("provider") or "sidecar"),
                "analysis_available": True,
                "analysis": analysis,
                "artifacts": artifacts,
                "analysis_path": str(analysis_path),
                "source_policy": "agent_observable_video_only",
            }
    provider = str(request.get("provider") or "").strip().lower()
    if provider in {"gemini", "google-gemini"}:
        return analyze_with_gemini(
            request=request,
            task_metadata=task_metadata,
            candidate_manifest=candidate_manifest,
            episode_dir=episode_dir,
        )
    return {
        "schema": SCHEMA,
        "status": "model_not_configured",
        "provider": str(request.get("provider") or "none"),
        "analysis_available": False,
        "analysis": {},
        "artifacts": artifacts,
        "note": "No structured video model output was attached. WebUI frames/video remain human-review evidence only.",
        "source_policy": "agent_observable_video_only",
    }


def analyze_with_gemini(
    *,
    request: dict[str, Any],
    task_metadata: dict[str, Any],
    candidate_manifest: dict[str, Any],
    episode_dir: Path,
) -> dict[str, Any]:
    artifacts = _visual_artifacts(request)
    api_key_env = str(request.get("api_key_env") or "GEMINI_API_KEY")
    api_key = os.environ.get(api_key_env)
    model = str(request.get("model") or os.environ.get("KSM_VISUAL_MODEL") or DEFAULT_GEMINI_MODEL)
    frame_paths = _select_frame_paths(request=request, episode_dir=episode_dir)
    if not api_key:
        return _not_analyzed(
            status="model_not_configured",
            provider="gemini",
            model=model,
            artifacts=artifacts,
            note=f"Missing {api_key_env}; structured visual feedback was not generated.",
        )
    if not frame_paths:
        return _not_analyzed(
            status="no_visual_frames",
            provider="gemini",
            model=model,
            artifacts=artifacts,
            note="No WebUI frame paths matched the configured visual feedback request.",
        )
    prompt = _gemini_prompt(task_metadata=task_metadata, candidate_manifest=candidate_manifest, frame_paths=frame_paths)
    try:
        analysis = _call_gemini(api_key=api_key, model=model, prompt=prompt, frame_paths=frame_paths)
    except Exception as exc:
        return _not_analyzed(
            status="analysis_failed",
            provider="gemini",
            model=model,
            artifacts={**artifacts, "frame_paths": [str(path) for path in frame_paths]},
            note=repr(exc),
        )
    return {
        "schema": SCHEMA,
        "status": "analyzed",
        "provider": "gemini",
        "model": model,
        "analysis_available": True,
        "analysis": analysis,
        "artifacts": {**artifacts, "frame_paths": [str(path) for path in frame_paths]},
        "source_policy": "agent_observable_video_only",
    }


def _not_analyzed(*, status: str, provider: str, model: str, artifacts: dict[str, Any], note: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": status,
        "provider": provider,
        "model": model,
        "analysis_available": False,
        "analysis": {},
        "artifacts": artifacts,
        "note": note,
        "source_policy": "agent_observable_video_only",
    }


def _visual_feedback_request(*, task_metadata: dict[str, Any], candidate_manifest: dict[str, Any]) -> dict[str, Any]:
    for source in (
        _dict(candidate_manifest.get("visual_feedback")),
        _dict(_dict(candidate_manifest.get("metadata")).get("visual_feedback")),
        _dict(task_metadata.get("visual_feedback")),
    ):
        if source:
            return source
    return {}


def _visual_artifacts(request: dict[str, Any]) -> dict[str, Any]:
    artifacts = request.get("artifacts")
    return dict(artifacts) if isinstance(artifacts, dict) else {}


def _analysis_path(request: dict[str, Any], episode_dir: Path) -> Path | None:
    value = request.get("analysis_path") or request.get("sidecar_path")
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = episode_dir / path
    return path


def _load_analysis(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else payload
    return dict(analysis) if isinstance(analysis, dict) else {}


def _select_frame_paths(*, request: dict[str, Any], episode_dir: Path) -> list[Path]:
    max_frames = max(1, int(request.get("max_frames") or 8))
    paths: list[Path] = []
    for value in request.get("frame_paths") or []:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = episode_dir / path
        if path.exists():
            paths.append(path)
    for pattern in request.get("frame_globs") or []:
        pattern_path = Path(str(pattern)).expanduser()
        if pattern_path.is_absolute():
            matches = [Path(item) for item in sorted(glob.glob(pattern_path.as_posix(), recursive=True))]
        else:
            matches = sorted(episode_dir.glob(pattern_path.as_posix()))
        paths.extend(path for path in matches if path.is_file())
    unique = sorted(dict.fromkeys(path.resolve() for path in paths), key=lambda path: (path.stat().st_mtime, str(path)))
    if len(unique) <= max_frames:
        return unique
    if max_frames == 1:
        return [unique[-1]]
    indexes = [round(i * (len(unique) - 1) / (max_frames - 1)) for i in range(max_frames)]
    return [unique[index] for index in indexes]


def _gemini_prompt(*, task_metadata: dict[str, Any], candidate_manifest: dict[str, Any], frame_paths: list[Path]) -> str:
    binding = _dict(_dict(candidate_manifest.get("metadata")).get("binding")) or _dict(task_metadata.get("binding"))
    skill_args = _dict(candidate_manifest.get("skill_args"))
    return (
        "You are analyzing WebUI frame evidence from one robot execution. "
        "Use only visible evidence in the provided temporal frames. Do not use simulator ground truth, "
        "asset metadata, predicates, or success labels. Describe what changed over time rather than "
        "forcing the run into a task-specific schema. Return one JSON object only.\n\n"
        "Task context:\n"
        + json.dumps(
            {
                "task_id": candidate_manifest.get("metadata", {}).get("task_id") if isinstance(candidate_manifest.get("metadata"), dict) else None,
                "task_class": candidate_manifest.get("metadata", {}).get("task_class") if isinstance(candidate_manifest.get("metadata"), dict) else None,
                "pick_label": skill_args.get("pick_label") or binding.get("primary_pick_label"),
                "place_label": skill_args.get("place_label") or binding.get("primary_place_label"),
                "frame_count": len(frame_paths),
            },
            ensure_ascii=False,
        )
        + "\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "temporal_observations": [\n'
        '    {"frame_range": "string", "observation": "visible event/state", "confidence": number|null}\n'
        "  ],\n"
        '  "robot_object_interactions": [\n'
        '    {"actor": "robot/arm/gripper", "object": "visible object", "interaction": "approach|contact|grasp|lift|carry|release|other", "evidence": "string", "confidence": number|null}\n'
        "  ],\n"
        '  "visible_state_changes": [\n'
        '    {"object": "visible object", "changed": boolean|null, "change": "pose|position|height|containment|contact|none|unknown", "summary": "string", "confidence": number|null}\n'
        "  ],\n"
        '  "uncertain_points": ["short uncertainty or missing-view note"],\n'
        '  "evidence_summary": "concise visible-evidence summary",\n'
        '  "suggested_next_probe": "what visual/trace evidence should be collected next",\n'
        '  "confidence": number\n'
        "}"
    )


def _call_gemini(*, api_key: str, model: str, prompt: str, frame_paths: list[Path]) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for index, path in enumerate(frame_paths):
        parts.append({"text": f"Frame {index + 1}/{len(frame_paths)}: {path.name}"})
        parts.append(
            {
                "inline_data": {
                    "mime_type": _mime_type(path),
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            }
        )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?{urlencode({'key': api_key})}"
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "response_mime_type": "application/json"},
        }
    ).encode("utf-8")
    timeout_s = max(1.0, float(os.environ.get("KSM_VISUAL_TIMEOUT_S", "90")))
    max_attempts = max(1, int(os.environ.get("KSM_VISUAL_MAX_ATTEMPTS", "3")))
    backoff_base_s = max(0.0, float(os.environ.get("KSM_VISUAL_BACKOFF_BASE_S", "3")))
    backoff_max_s = max(backoff_base_s, float(os.environ.get("KSM_VISUAL_BACKOFF_MAX_S", "20")))
    jitter_s = max(0.0, float(os.environ.get("KSM_VISUAL_BACKOFF_JITTER_S", "1")))
    payload = None
    for attempt in range(1, max_attempts + 1):
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except HTTPError as exc:
            if exc.code not in GEMINI_RETRYABLE_HTTP_STATUSES or attempt >= max_attempts:
                raise
            delay_s = _gemini_retry_delay(
                attempt=attempt,
                backoff_base_s=backoff_base_s,
                backoff_max_s=backoff_max_s,
                jitter_s=jitter_s,
                retry_after=exc.headers.get("Retry-After") if exc.headers else None,
            )
            time.sleep(delay_s)
        except (URLError, TimeoutError, ConnectionError) as exc:
            if attempt >= max_attempts:
                raise
            delay_s = _gemini_retry_delay(
                attempt=attempt,
                backoff_base_s=backoff_base_s,
                backoff_max_s=backoff_max_s,
                jitter_s=jitter_s,
            )
            time.sleep(delay_s)
    if not isinstance(payload, dict):
        raise RuntimeError("Gemini response was unavailable after retry handling")
    text = "".join(
        str(part.get("text") or "")
        for part in (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        if isinstance(part, dict)
    ).strip()
    return _parse_json_object(text)


def _gemini_retry_delay(
    *,
    attempt: int,
    backoff_base_s: float,
    backoff_max_s: float,
    jitter_s: float,
    retry_after: str | None = None,
) -> float:
    exponential_s = backoff_base_s * (2 ** max(0, attempt - 1))
    server_delay_s = _retry_after_seconds(retry_after)
    base_delay_s = max(exponential_s, server_delay_s or 0.0)
    return min(backoff_max_s, base_delay_s) + random.uniform(0.0, jitter_s)


def _retry_after_seconds(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(text)
        now = time.time()
        return max(0.0, retry_at.timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return {"raw_text": text}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {"raw_text": text}
    return payload if isinstance(payload, dict) else {"raw_text": text}


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
