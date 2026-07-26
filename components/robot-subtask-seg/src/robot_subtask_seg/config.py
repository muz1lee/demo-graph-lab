from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "provider": {
        "name": "fake",
        "model": "fake-segmenter",
        "temperature": 0.1,
        "max_retries": 4,
    },
    "segmentation": {
        "sample_sec": 0.5,
        "frame_width": 224,
        "frames_per_sheet": 20,
        "columns": 5,
        "jpeg_quality": 95,
        "prompt": "eef_object_role_trace_v2",
    },
    "labeling": {
        "enabled": False,
        "seeded": True,
        "frame_width": 336,
        "max_frames_per_segment": 5,
        "columns": 3,
        "jpeg_quality": 95,
        "temperature": 0.0,
    },
    "refinement": {
        "enabled": True,
        "frame_width": 336,
        "max_frames_per_segment": 5,
        "columns": 3,
        "jpeg_quality": 95,
        "include_context": True,
        "context_pad_sec": 0.0,
    },
    "operation_structure": {
        "sample_sec": 0.25,
        "frame_width": 320,
        "frames_per_sheet": 16,
        "columns": 4,
        "jpeg_quality": 95,
    },
    "output": {
        "save_contact_sheets": True,
        "save_raw_responses": True,
        "overwrite": False,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        return deepcopy(DEFAULT_CONFIG)
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping: {config_path}")
    return deep_merge(DEFAULT_CONFIG, data)


def set_provider(config: dict[str, Any], provider: str | None) -> dict[str, Any]:
    if provider is None:
        return config
    updated = deepcopy(config)
    updated.setdefault("provider", {})["name"] = provider
    if provider == "fake":
        updated["provider"].setdefault("model", "fake-segmenter")
    return updated
