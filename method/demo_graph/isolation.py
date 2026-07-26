"""生成策略隔离执行：无网络、无特权 API，仅经 Broker 通道调用。

完整生产部署应再叠加 OS 级 sandbox（无网 namespace、无 data 挂载、无密钥）。
本模块提供进程内/子进程可测的契约层，阻止明显违规。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class IsolationViolation(RuntimeError):
    """策略试图越权访问网络、文件系统或特权 API。"""


_ALLOWED_METHOD_PREFIXES = (
    "perception.",
    "robot.",
    "grasp.",
    "geometry.",
    "controller.",
    "verification.",
)

_FORBIDDEN_METHOD_FRAGMENTS = (
    "/state",
    "session.reset",
    "session.finalize",
    "oracle",
    "predicate",
    "task_success",
    "target_binding",
    "scene_asset",
    "list_scene",
    "eval.",
)

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "socket",
        "ssl",
        "http",
        "urllib",
        "requests",
        "aiohttp",
        "httpx",
        "ftplib",
        "smtplib",
        "telnetlib",
        "websocket",
    }
)


BrokerHandler = Callable[[str, dict[str, Any]], Any]


def assert_method_allowed(method: str) -> None:
    if not isinstance(method, str) or not method.strip():
        raise IsolationViolation("empty Method API name")
    lowered = method.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_METHOD_FRAGMENTS):
        raise IsolationViolation(f"privileged Method API is forbidden: {method}")
    if not any(method.startswith(prefix) for prefix in _ALLOWED_METHOD_PREFIXES):
        raise IsolationViolation(f"method is not allowlisted: {method}")


def scan_policy_source(source: str, *, allow_network: bool = False) -> None:
    """静态扫描策略源码，拒绝网络相关导入与明显特权路径。"""

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise IsolationViolation(f"policy syntax error: {exc}") from exc
    if allow_network:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    raise IsolationViolation(
                        f"network import is forbidden in isolated policy: {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in _FORBIDDEN_IMPORT_ROOTS:
                raise IsolationViolation(
                    f"network import is forbidden in isolated policy: {node.module}"
                )


class IsolatedPolicyWorker:
    """策略侧 Broker 客户端：只能调用 allowlisted Method API。"""

    def __init__(self, broker_handler: BrokerHandler) -> None:
        self._handler = broker_handler
        self._closed = False

    def __enter__(self) -> "IsolatedPolicyWorker":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if self._closed:
            raise IsolationViolation("worker is closed")
        assert_method_allowed(method)
        if params is not None and not isinstance(params, Mapping):
            raise IsolationViolation("params must be a mapping")
        try:
            return self._handler(method, dict(params or {}))
        except IsolationViolation:
            raise
        except Exception as exc:
            raise IsolationViolation(
                f"broker call failed: {type(exc).__name__}: {exc}"
            ) from exc

    @classmethod
    def run_policy_file(
        cls,
        path: str | Path,
        *,
        broker_handler: BrokerHandler,
        allow_network: bool = False,
        timeout_s: float = 5.0,
    ) -> dict[str, Any]:
        """在子进程中执行策略文件；默认禁止网络导入。"""

        policy_path = Path(path)
        source = policy_path.read_text(encoding="utf-8")
        scan_policy_source(source, allow_network=allow_network)
        # 子进程仅执行用户策略主体；Broker 调用经 stdin/stdout JSON 线路。
        launcher = textwrap.dedent(
            """
            import json, pathlib, runpy, sys
            policy = pathlib.Path(sys.argv[1])
            # 默认无网：在 import 阶段即阻断 socket 族。
            if sys.argv[2] != "1":
                import builtins
                real_import = builtins.__import__
                blocked = {
                    "socket","ssl","http","urllib","requests","aiohttp","httpx",
                    "ftplib","smtplib","telnetlib","websocket",
                }
                def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                    root = name.split(".", 1)[0]
                    if root in blocked:
                        raise RuntimeError(f"network import blocked: {name}")
                    return real_import(name, globals, locals, fromlist, level)
                builtins.__import__ = guarded_import
            try:
                runpy.run_path(str(policy), run_name="__main__")
                print(json.dumps({"ok": True, "result": None}), flush=True)
            except Exception as exc:
                print(json.dumps({
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }), flush=True)
                raise SystemExit(2)
            """
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                launcher,
                str(policy_path),
                "1" if allow_network else "0",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={
                "PATH": "",
                "PYTHONPATH": "",
                "HOME": str(policy_path.parent),
                "OPENAI_API_KEY": "",
                "QWEN_API_KEY": "",
            },
            check=False,
        )
        # 静态扫描已拒绝网络导入；子进程失败也统一升格为隔离违规。
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "policy failed").strip()
            raise IsolationViolation(detail[:500])
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            return {"ok": True}
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise IsolationViolation("policy worker returned non-JSON status") from exc
        if not payload.get("ok"):
            raise IsolationViolation(str(payload.get("error") or "policy failed"))
        # broker_handler 留给宿主侧 MethodBroker；此入口先保证违规不可执行。
        _ = broker_handler
        return payload
