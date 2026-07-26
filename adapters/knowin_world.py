"""Trusted client for the Knowin World interactive EvalServer.

The actual server performs queue submission and polling *inside* ``POST
/skill``.  A successful transport response is therefore still rejected unless
it carries an exact queue identity and ``quiescence_confirmed is True``.

The server embeds simulator ground truth in the ``state`` fields returned by
``/session/reset`` and ``/skill``.  This adapter deliberately discards those
fields.  ``finalize`` returns an explicitly privileged record intended only
for the trusted evaluation harness; it must never be registered with
``MethodBroker``.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ._json import content_digest, jsonable, require_mapping


_SKILL_PATH_RE = re.compile(r"^knowin_skills/[A-Za-z0-9_./-]+\.ya?ml$")
_VALID_SKILL_STATUSES = frozenset({"succeeded", "failed"})
_INFRA_FAILURE_STATUSES = frozenset(
    {
        "submit_failed",
        "status_failed",
        "timeout",
        "unknown_result",
        "unknown",
        "skill_execution_crashed",
        "pipeline_reset_failed",
        "reset_failed",
        "cancel_failed",
    }
)


class EvalProtocolError(RuntimeError):
    """The EvalServer response did not prove the required lifecycle state."""


class EvalTransportError(RuntimeError):
    """The EvalServer request failed before a valid response was received."""


class JsonTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class UrllibJsonTransport:
    """Minimal JSON transport with no endpoint discovery or `/state` API."""

    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use http:// or https://")
        self._base_url = base_url.rstrip("/")
        self._timeout_s = float(timeout_s)

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if path == "/state" or path.startswith("/state?"):
            raise ValueError("the privileged /state endpoint is intentionally unavailable")
        encoded = None
        headers = {"Accept": "application/json"}
        if body is not None:
            encoded = json.dumps(jsonable(body), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=encoded,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise EvalTransportError(f"EvalServer HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise EvalTransportError(f"EvalServer request failed: {exc.reason}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvalTransportError("EvalServer returned non-JSON content") from exc
        return require_mapping(payload, name=f"{method.upper()} {path} response")


@dataclass(frozen=True, slots=True)
class SessionReceipt:
    session_id: str
    run_id: str
    task_id: str
    mode: str
    response_digest: str


@dataclass(frozen=True, slots=True)
class SkillReceipt:
    run_id: str
    path: str
    status: str
    duration_s: float | None
    error: str | None
    queue_id: str
    quiescence_confirmed: bool
    response_digest: str


@dataclass(frozen=True, slots=True)
class OracleFinalRecord:
    """Privileged evaluator output; never pass this object to generated code."""

    session_id: str
    run_id: str
    execution_success: bool | None
    task_success: bool | None
    run_success: bool | None
    payload: Mapping[str, Any]
    response_digest: str
    provenance: str = "privileged_oracle"


class KnowinWorldAdapter:
    """Stateful trusted facade for one reset/skill*/finalize episode."""

    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport
        self._session: SessionReceipt | None = None
        self._poisoned = False

    @property
    def active_session(self) -> SessionReceipt | None:
        return self._session

    def reset(self, task_id: str, *, mode: str = "skill") -> SessionReceipt:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        if mode != "skill":
            raise ValueError("interactive adapter supports mode='skill' only")
        payload = require_mapping(
            self._transport.request(
                "POST",
                "/session/reset",
                {"task_id": task_id, "mode": mode},
            ),
            name="POST /session/reset response",
        )
        session_id = _required_string(payload, "session_id")
        run_id = _required_string(payload, "run_id")
        returned_task = _required_string(payload, "task_id")
        returned_mode = _required_string(payload, "mode")
        if returned_task != task_id or returned_mode != mode:
            raise EvalProtocolError("reset response identity does not match the request")
        receipt = SessionReceipt(
            session_id=session_id,
            run_id=run_id,
            task_id=returned_task,
            mode=returned_mode,
            response_digest=_method_response_digest(payload),
        )
        self._session = receipt
        self._poisoned = False
        return receipt

    def execute_skill(
        self,
        path: str,
        args: Mapping[str, Any] | None = None,
    ) -> SkillReceipt:
        session = self._require_active()
        _validate_skill_path(path)
        if args is not None and not isinstance(args, Mapping):
            raise TypeError("skill args must be a JSON object")
        payload = require_mapping(
            self._transport.request(
                "POST",
                "/skill",
                {"path": path, "args": jsonable(args or {})},
            ),
            name="POST /skill response",
        )
        run_id = _required_string(payload, "run_id")
        returned_path = _required_string(payload, "path")
        status = _required_string(payload, "status")
        if run_id != session.run_id or returned_path != path:
            self._poisoned = True
            raise EvalProtocolError("skill response identity does not match the active session/request")
        try:
            queue_id = _normalize_queue_id(payload.get("queue_id"))
        except EvalProtocolError:
            self._poisoned = True
            raise
        quiescence = payload.get("quiescence_confirmed")
        if quiescence is not True:
            self._poisoned = True
            raise EvalProtocolError(
                f"skill queue_id={queue_id} did not prove pipeline quiescence"
            )
        if status in _INFRA_FAILURE_STATUSES or status not in _VALID_SKILL_STATUSES:
            self._poisoned = True
            raise EvalProtocolError(
                f"skill queue_id={queue_id} returned non-terminal/infra status {status!r}"
            )
        duration = payload.get("duration_s")
        if duration is not None and not isinstance(duration, int | float):
            raise EvalProtocolError("skill duration_s must be numeric or null")
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            raise EvalProtocolError("skill error must be a string or null")
        return SkillReceipt(
            run_id=run_id,
            path=returned_path,
            status=status,
            duration_s=float(duration) if duration is not None else None,
            error=error,
            queue_id=queue_id,
            quiescence_confirmed=True,
            response_digest=_method_response_digest(payload),
        )

    def finalize(self) -> OracleFinalRecord:
        session = self._require_active()
        if self._poisoned:
            raise EvalProtocolError(
                "cannot finalize: a skill result left pipeline state unconfirmed"
            )
        payload = require_mapping(
            self._transport.request("POST", "/session/finalize", {}),
            name="POST /session/finalize response",
        )
        run_id = _required_string(payload, "run_id")
        returned_session = _required_string(payload, "session_id")
        if run_id != session.run_id or returned_session != session.session_id:
            raise EvalProtocolError("finalize response identity does not match the active session")
        record = OracleFinalRecord(
            session_id=returned_session,
            run_id=run_id,
            execution_success=_optional_bool(payload, "execution_success"),
            task_success=_optional_bool(payload, "task_success"),
            run_success=_optional_bool(payload, "run_success"),
            payload=jsonable(payload),
            response_digest=content_digest(payload),
        )
        self._session = None
        return record

    def _require_active(self) -> SessionReceipt:
        if self._session is None:
            raise EvalProtocolError("no active session; call reset first")
        return self._session


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvalProtocolError(f"response is missing non-empty string {key}")
    return value


def _method_response_digest(payload: Mapping[str, Any]) -> str:
    """Hash lifecycle evidence without deriving an identity from GT state."""

    return content_digest({key: value for key, value in payload.items() if key != "state"})


def _optional_bool(payload: Mapping[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        raise EvalProtocolError(f"response field {key} must be boolean or null")
    return value


def _normalize_queue_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise EvalProtocolError("skill response is missing an exact queue_id")
    normalized = str(value).strip()
    if not normalized or normalized.lower() in {"none", "null", "unknown"}:
        raise EvalProtocolError("skill response contains a placeholder queue_id")
    return normalized


def _validate_skill_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not _SKILL_PATH_RE.fullmatch(path)
        or ".." in path.split("/")
    ):
        raise ValueError(
            "skill path must be a relative YAML path beneath knowin_skills/"
        )
