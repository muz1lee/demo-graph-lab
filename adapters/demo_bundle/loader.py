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
    segments: tuple[Mapping[str, Any], ...]
    keyframes: tuple[Mapping[str, Any], ...]
    evidence_gaps: tuple[str, ...]
    digest: str

    def assert_method_safe(self) -> None:
        assert_method_payload_safe(
            {
                "task_id": self.task_id,
                "segments": list(self.segments),
                "keyframes": list(self.keyframes),
                "evidence_gaps": list(self.evidence_gaps),
            }
        )


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
    task_id = str(raw.get("task_id") or raw.get("task") or bundle_path.stem)
    segments = tuple(raw.get("segments") or raw.get("subtasks") or ())
    keyframes = tuple(raw.get("keyframes") or raw.get("key_frames") or ())
    gaps = tuple(str(item) for item in (raw.get("evidence_gaps") or ()))
    if segments and not all(isinstance(item, Mapping) for item in segments):
        raise DemoBundleError("segments must be objects")
    if keyframes and not all(isinstance(item, Mapping) for item in keyframes):
        raise DemoBundleError("keyframes must be objects")
    bundle = DemoBundle(
        path=str(bundle_path),
        task_id=task_id,
        segments=tuple(dict(item) for item in segments),
        keyframes=tuple(dict(item) for item in keyframes),
        evidence_gaps=gaps,
        digest=content_digest(raw),
    )
    bundle.assert_method_safe()
    return bundle
