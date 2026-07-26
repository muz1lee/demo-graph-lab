"""Allowlisted Method API broker with append-only provenance auditing."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ._json import content_digest, require_jsonable
from .contracts import MethodResult, assert_method_payload_safe


_METHOD_RE = re.compile(
    r"^(perception|robot|grasp|geometry|controller|verification)"
    r"\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
)
_FORBIDDEN_METHOD_FRAGMENTS = (
    "/state",
    "scene_asset",
    "list_scene",
    "oracle",
    "predicate",
    "task_success",
    "target_binding",
    "session.reset",
    "session.finalize",
    "eval.",
)


class BrokerError(RuntimeError):
    code = "broker_error"


class MethodNotAllowed(BrokerError):
    code = "method_not_allowed"


class MethodContractError(BrokerError):
    code = "method_contract_error"


@dataclass(frozen=True, slots=True)
class MethodSpec:
    name: str
    handler: Callable[[Mapping[str, Any]], MethodResult]
    max_calls: int | None = None


@dataclass(frozen=True, slots=True)
class ApiCallRecord:
    sequence: int
    request_id: str
    method: str
    request_digest: str
    response_digest: str | None
    evidence_digests: tuple[str, ...]
    started_ns: int
    finished_ns: int
    ok: bool
    error_code: str | None

    @property
    def digest(self) -> str:
        return content_digest(self)


class MethodBroker:
    """Dispatch only explicitly registered, method-visible operations.

    A handler must return ``MethodResult`` so perception/control data cannot
    cross the boundary without an evidence lineage.  Raw EvalServer lifecycle
    methods and evaluator outputs cannot be registered.
    """

    def __init__(
        self,
        specs: Sequence[MethodSpec] = (),
        *,
        clock_ns: Callable[[], int] = time.time_ns,
        audit_sink: Callable[[ApiCallRecord], None] | None = None,
    ) -> None:
        self._specs: dict[str, MethodSpec] = {}
        self._counts: dict[str, int] = {}
        self._records: list[ApiCallRecord] = []
        self._clock_ns = clock_ns
        self._audit_sink = audit_sink
        for spec in specs:
            self.register(spec)

    @property
    def allowed_methods(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    @property
    def audit_records(self) -> tuple[ApiCallRecord, ...]:
        return tuple(self._records)

    def register(self, spec: MethodSpec) -> None:
        lowered = spec.name.lower()
        if not _METHOD_RE.fullmatch(spec.name):
            raise ValueError(f"invalid Method API name: {spec.name!r}")
        if any(fragment in lowered for fragment in _FORBIDDEN_METHOD_FRAGMENTS):
            raise ValueError(f"privileged Method API name is forbidden: {spec.name!r}")
        if spec.name in self._specs:
            raise ValueError(f"duplicate Method API: {spec.name}")
        if spec.max_calls is not None and spec.max_calls < 1:
            raise ValueError("max_calls must be positive or null")
        self._specs[spec.name] = spec
        self._counts[spec.name] = 0

    def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> MethodResult:
        call_id = request_id or f"call-{len(self._records) + 1}"
        started = self._clock_ns()
        request_digest = content_digest({"method": method, "params": params or {}})
        response_digest: str | None = None
        evidence_digests: tuple[str, ...] = ()
        error: BrokerError | None = None
        try:
            spec = self._specs.get(method)
            if spec is None:
                raise MethodNotAllowed(f"method is not allowlisted: {method}")
            count = self._counts[method]
            if spec.max_calls is not None and count >= spec.max_calls:
                raise MethodNotAllowed(f"method call budget exhausted: {method}")
            normalized_params = require_jsonable(params or {}, name="Method API params")
            if not isinstance(normalized_params, dict):
                raise MethodContractError("Method API params must be a JSON object")
            assert_method_payload_safe(normalized_params)
            self._counts[method] = count + 1
            result = spec.handler(normalized_params)
            if not isinstance(result, MethodResult):
                raise MethodContractError(
                    f"handler {method} must return MethodResult, got {type(result).__name__}"
                )
            response_digest = content_digest(result.value)
            evidence_digests = tuple(item.digest for item in result.evidence)
            return result
        except BrokerError as exc:
            error = exc
            raise
        except Exception as exc:
            error = MethodContractError(f"handler {method} failed: {type(exc).__name__}: {exc}")
            raise error from exc
        finally:
            finished = self._clock_ns()
            record = ApiCallRecord(
                sequence=len(self._records) + 1,
                request_id=call_id,
                method=method,
                request_digest=request_digest,
                response_digest=response_digest,
                evidence_digests=evidence_digests,
                started_ns=started,
                finished_ns=finished,
                ok=error is None,
                error_code=error.code if error is not None else None,
            )
            self._records.append(record)
            if self._audit_sink is not None:
                self._audit_sink(record)
