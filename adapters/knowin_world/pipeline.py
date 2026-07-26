"""开发模式：对接已运行的 pipeline ``/run`` 端点（默认 8000）。"""

from __future__ import annotations

import ast
import json
import urllib.parse
import urllib.request
from typing import Any, Mapping


class PipelineError(RuntimeError):
    pass


def wire_value(value: Any) -> Any:
    """把 pipeline 常见字符串化返回值还原为 Python 对象。"""

    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(stripped)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            pass
    return value


class PipelineClient:
    """极薄 HTTP 客户端：只走 ``/run``，不发现、不读 ``/state``。"""

    def __init__(self, base_url: str, timeout_s: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def call(self, action: str, name: str, kwargs: Mapping[str, Any]) -> Any:
        query = urllib.parse.urlencode(
            {"action": action, "name": name, "kwargs": json.dumps(dict(kwargs))}
        )
        request = urllib.request.Request(f"{self.base_url}/run?{query}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read())
        except Exception as exc:
            raise PipelineError(f"{action}:{name} transport failed: {exc}") from exc
        if payload.get("ok") is not True:
            raise PipelineError(f"{action}:{name} failed: {payload.get('error')}")
        return wire_value(payload.get("result"))

    def reasoning(self, name: str, **kwargs: Any) -> Mapping[str, Any]:
        value = self.call("reasoning", name, kwargs)
        if not isinstance(value, Mapping):
            raise PipelineError(f"reasoning:{name} returned {type(value).__name__}")
        return value

    def info(self, name: str, **kwargs: Any) -> Any:
        return self.call("info", name, kwargs)

    def ctrl(self, name: str, **kwargs: Any) -> None:
        if self.call("ctrl", name, kwargs) is not True:
            raise PipelineError(f"ctrl:{name} was not accepted")
