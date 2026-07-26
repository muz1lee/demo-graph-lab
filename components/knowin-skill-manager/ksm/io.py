from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def read_yaml(path: str | Path) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def write_yaml(path: str | Path, data: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def write_json(path: str | Path, data: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value).strip())
    cleaned = cleaned.strip("._")
    if not cleaned:
        raise ValueError("empty identifier")
    return cleaned


def resolve_path(base_dir: str | Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (Path(base_dir).expanduser().resolve() / path).resolve()


def assert_child_path(root: str | Path, relative_path: str | Path) -> Path:
    root_path = Path(root).expanduser().resolve()
    child = (root_path / Path(relative_path)).resolve()
    try:
        child.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"path escapes root: {relative_path}") from exc
    return child
