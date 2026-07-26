from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from robot_subtask_seg.schema import SegmentPrediction, SegmentationResponse


def extract_json_object(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned.strip(), flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = _repair_json_suffix(cleaned)
        if repaired != cleaned:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair_json_suffix(cleaned[start:])
            if repaired != cleaned[start:]:
                return json.loads(repaired)
    raise ValueError("model response does not contain a JSON object")


def _repair_json_suffix(text: str) -> str:
    """Append missing closing brackets/braces for truncated JSON suffixes.

    This intentionally does not alter content in the middle of the response.
    It only handles the common model failure where the response ends before the
    final `]` or `}` characters.
    """
    stack: list[str] = []
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif char == "]":
            if stack and stack[-1] == "[":
                stack.pop()
    if in_string:
        return text
    closers = {"{": "}", "[": "]"}
    return text + "".join(closers[item] for item in reversed(stack))


@dataclass(frozen=True)
class NormalizedSegmentation:
    demonstration_method: list[str]
    quality_warnings: list[str]
    segments: list[SegmentPrediction]


def normalize_segmentation(raw: Any, *, duration_sec: float | None = None) -> NormalizedSegmentation:
    if isinstance(raw, str):
        raw = extract_json_object(raw)
    if isinstance(raw, list):
        raw = {"segments": raw}
    parsed = SegmentationResponse.model_validate(raw)

    normalized: list[SegmentPrediction] = []
    for segment in parsed.segments:
        start = round(max(0.0, float(segment.start_sec)), 3)
        end = round(max(0.0, float(segment.end_sec)), 3)
        if duration_sec is not None:
            start = min(start, round(duration_sec, 3))
            end = min(end, round(duration_sec, 3))
        if end <= start:
            continue
        label = re.sub(r"\s+", " ", segment.subtask.strip())
        if not label:
            continue
        normalized.append(
            SegmentPrediction(
                start_sec=start,
                end_sec=end,
                subtask=label,
                actor_arm=_clean_optional_text(segment.actor_arm),
                receiver_arm=_clean_optional_text(segment.receiver_arm),
                eef_event=_clean_optional_text(segment.eef_event),
                motion_type=_clean_optional_text(segment.motion_type),
                manipulated_object=_clean_optional_text(segment.manipulated_object),
                target_object=_clean_optional_text(segment.target_object),
                target_role=_clean_optional_text(segment.target_role),
                requires_bimanual=bool(segment.requires_bimanual),
                requires_alignment=bool(segment.requires_alignment),
                role=_normalize_role(segment.role),
                confidence=segment.confidence,
                visual_evidence=_clean_optional_text(segment.visual_evidence),
                risk_flags=_clean_list(segment.risk_flags),
                method_note=_clean_optional_text(segment.method_note),
            )
        )

    normalized.sort(key=lambda item: (item.start_sec, item.end_sec))
    return NormalizedSegmentation(
        demonstration_method=_clean_list(parsed.demonstration_method),
        quality_warnings=_clean_list(parsed.quality_warnings),
        segments=normalized,
    )


def normalize_segments(raw: Any, *, duration_sec: float | None = None) -> list[SegmentPrediction]:
    return normalize_segmentation(raw, duration_sec=duration_sec).segments


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value).strip())
    return cleaned or None


def _clean_list(values: list[Any] | None) -> list[str]:
    if values is None:
        return []
    cleaned: list[str] = []
    for value in values:
        item = _clean_optional_text(str(value))
        if item:
            cleaned.append(item)
    return cleaned


def _normalize_role(value: str | None) -> str:
    cleaned = _clean_optional_text(value)
    if cleaned is None:
        return "core"
    lowered = cleaned.lower().replace("-", "_").replace(" ", "_")
    if lowered in {"core", "assist", "cleanup", "uncertain"}:
        return lowered
    return "uncertain"


def overlap_warnings(segments: list[SegmentPrediction]) -> list[str]:
    warnings: list[str] = []
    previous: SegmentPrediction | None = None
    for segment in segments:
        if previous is not None and segment.start_sec < previous.end_sec:
            warnings.append(
                f"overlap: {previous.start_sec:.3f}-{previous.end_sec:.3f} "
                f"with {segment.start_sec:.3f}-{segment.end_sec:.3f}"
            )
        previous = segment
    return warnings
