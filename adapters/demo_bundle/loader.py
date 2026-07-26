"""加载演示 bundle，并拒绝含特权字段的工件进入主方法。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import assert_method_payload_safe
from .._json import content_digest


class DemoBundleError(ValueError):
    pass


_PRIVILEGED_MARKERS = (
    "privileged_oracle",
    "exact_pose",
    "entity_id",
    "scene_asset",
    "target_binding",
    "task_success",
)


@dataclass(frozen=True, slots=True)
class DemoBundle:
    """方法可见的演示证据摘要。"""

    path: str
    task_id: str
    task_class: str
    instruction: str
    segments: tuple[Mapping[str, Any], ...]
    keyframes: tuple[Mapping[str, Any], ...]
    objects: tuple[Mapping[str, Any], ...]
    segment_evidence: tuple[Mapping[str, Any], ...]
    evidence_gaps: tuple[str, ...]
    artifact_refs: Mapping[str, Any]
    summary: Mapping[str, Any]
    digest: str

    def assert_method_safe(self) -> None:
        assert_method_payload_safe(
            {
                "task_id": self.task_id,
                "task_class": self.task_class,
                "instruction": self.instruction,
                "segments": list(self.segments),
                "keyframes": list(self.keyframes),
                "objects": list(self.objects),
                "segment_evidence": list(self.segment_evidence),
                "evidence_gaps": list(self.evidence_gaps),
                "artifact_refs": dict(self.artifact_refs),
                "summary": dict(self.summary),
            }
        )


@dataclass(frozen=True, slots=True)
class RefinedTrace:
    """一个 refined 任务 trace 的只读、安全索引项。"""

    path: str
    task_id: str
    segments: tuple[Mapping[str, Any], ...]
    digest: str


def _as_mapping_tuple(value: Any, *, name: str) -> tuple[Mapping[str, Any], ...]:
    items = tuple(value or ())
    if items and not all(isinstance(item, Mapping) for item in items):
        raise DemoBundleError(f"{name} must contain objects")
    return tuple(dict(item) for item in items)


def _gap_text(value: Any) -> str:
    if isinstance(value, Mapping):
        capability = str(value.get("capability") or "unknown")
        reason = str(value.get("reason") or "unspecified")
        return f"{capability}: {reason}"
    return str(value)


def load_demo_bundle(path: str | Path) -> DemoBundle:
    """从 JSON 加载 demo bundle；发现特权标记则失败关闭。"""

    bundle_path = Path(path)
    try:
        raw = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoBundleError(f"cannot load demo bundle: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise DemoBundleError("demo bundle root must be an object")
    blob = json.dumps(raw, ensure_ascii=False).lower()
    for marker in _PRIVILEGED_MARKERS:
        if marker in blob:
            raise DemoBundleError(
                f"demo bundle contains privileged marker {marker!r}; refused"
            )
    assert_method_payload_safe(raw)
    trace = raw.get("trace") or {}
    if not isinstance(trace, Mapping):
        raise DemoBundleError("trace must be an object")
    task_id = str(raw.get("task_id") or raw.get("task") or bundle_path.stem)
    segments = _as_mapping_tuple(
        raw.get("segments")
        or raw.get("subtasks")
        or trace.get("segments"),
        name="segments",
    )
    keyframes = _as_mapping_tuple(
        raw.get("keyframes") or raw.get("key_frames"),
        name="keyframes",
    )
    objects = _as_mapping_tuple(raw.get("objects"), name="objects")
    segment_evidence = _as_mapping_tuple(
        raw.get("segment_evidence"),
        name="segment_evidence",
    )
    gaps = tuple(_gap_text(item) for item in (raw.get("evidence_gaps") or ()))
    artifact_refs = raw.get("artifact_refs") or {}
    summary = raw.get("summary") or {}
    if not isinstance(artifact_refs, Mapping):
        raise DemoBundleError("artifact_refs must be an object")
    if not isinstance(summary, Mapping):
        raise DemoBundleError("summary must be an object")
    bundle = DemoBundle(
        path=str(bundle_path),
        task_id=task_id,
        task_class=str(raw.get("task_class") or trace.get("task_class") or ""),
        instruction=str(raw.get("instruction") or trace.get("instruction") or ""),
        segments=segments,
        keyframes=keyframes,
        objects=objects,
        segment_evidence=segment_evidence,
        evidence_gaps=gaps,
        artifact_refs=dict(artifact_refs),
        summary=dict(summary),
        digest=content_digest(raw),
    )
    bundle.assert_method_safe()
    return bundle


def load_refined_traces(root: str | Path) -> tuple[RefinedTrace, ...]:
    """索引 refined 根目录中的任务 trace，不读取图片或模型原始回复。"""

    refined_root = Path(root)
    if not refined_root.is_dir():
        raise DemoBundleError(f"refined trace root is not a directory: {refined_root}")
    traces: list[RefinedTrace] = []
    for trace_path in sorted(refined_root.glob("*/*/trace.json")):
        try:
            raw = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DemoBundleError(f"cannot load refined trace {trace_path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise DemoBundleError(f"refined trace root must be an object: {trace_path}")
        assert_method_payload_safe(raw)
        segments = _as_mapping_tuple(raw.get("segments"), name="segments")
        traces.append(
            RefinedTrace(
                path=str(trace_path),
                task_id=str(raw.get("task_id") or trace_path.parent.name),
                segments=segments,
                digest=content_digest(raw),
            )
        )
    if not traces:
        raise DemoBundleError(f"no refined traces found under {refined_root}")
    return tuple(traces)
