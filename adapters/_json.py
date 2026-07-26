"""Small deterministic JSON helpers shared by adapter contracts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any


def jsonable(value: Any) -> Any:
    """Return a JSON-compatible, deterministically ordered projection."""

    if dataclasses.is_dataclass(value):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return jsonable(value.value)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, tuple | list):
        return [jsonable(item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted((jsonable(item) for item in value), key=canonical_json)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def content_digest(value: Any) -> str:
    payload = canonical_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def require_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object")
    return value


def require_jsonable(value: Any, *, name: str) -> Any:
    try:
        return jsonable(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be JSON-compatible: {exc}") from exc
