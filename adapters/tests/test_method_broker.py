from __future__ import annotations

from types import MappingProxyType

import pytest

from adapters.contracts import EvidenceRef, MethodResult
from adapters.method_broker import MethodBroker, MethodSpec


def _track_result(params: dict[str, object]) -> MethodResult:
    value = {
        "track_id": "tube-visible-1",
        "center_uv": [120.0, 80.0],
        "query": params["query"],
    }
    evidence = EvidenceRef.from_value(
        evidence_id="camera:frame-12:tube-visible-1",
        source="runtime_perception",
        value=value,
        observation_revision="frame-12",
    )
    return MethodResult(value=value, evidence=(evidence,))


def test_broker_allowlist_and_digest_only_call_log() -> None:
    ticks = iter((10, 11, 20, 21))
    broker = MethodBroker(
        [MethodSpec("perception.track", _track_result, max_calls=1)],
        clock_ns=lambda: next(ticks),
    )

    result = broker.call("perception.track", {"query": "tube"})

    assert result.value["track_id"] == "tube-visible-1"
    assert broker.audit_records[0].ok is True
    assert broker.audit_records[0].request_digest.startswith("sha256:")
    assert "tube" not in repr(broker.audit_records[0])
    with pytest.raises(Exception, match="budget"):
        broker.call("perception.track", {"query": "tube"})


@pytest.mark.parametrize(
    "name",
    [
        "verification.task_success",
        "perception.scene_asset",
        "controller.session.finalize",
    ],
)
def test_broker_refuses_privileged_method_names(name: str) -> None:
    with pytest.raises(ValueError):
        MethodBroker([MethodSpec(name, _track_result)])


def test_method_result_rejects_oracle_shaped_output() -> None:
    evidence = EvidenceRef.from_value(
        evidence_id="camera:frame-1",
        source="runtime_perception",
        value={"frame": 1},
    )
    with pytest.raises(ValueError, match="task_success"):
        MethodResult(
            value={"task_success": True, "provenance": "runtime_perception"},
            evidence=(evidence,),
        )
    with pytest.raises(ValueError, match="task_success"):
        MethodResult(
            value=MappingProxyType({"task_success": True}),
            evidence=(evidence,),
        )
