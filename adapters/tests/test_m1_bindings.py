from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adapters.contracts import EvidenceRef, MethodResult
from adapters.m1_bindings import (
    BrokerPolicyBindings,
    M1BindingError,
)
from adapters.method_broker import MethodBroker, MethodSpec
from method.demo_graph import ConstraintGraph, ControllerResult


def test_m1_runner_uses_broker_observation_and_trusted_controllers() -> None:
    graph_path = (
        Path(__file__).parents[2]
        / "method"
        / "demo_graph"
        / "examples"
        / "m1_graph.json"
    )
    graph = ConstraintGraph.load_json(graph_path)
    state = {node.goal: False for node in graph.nodes}
    revision = 0

    def evidence(value: Any) -> EvidenceRef:
        return EvidenceRef.from_value(
            evidence_id=f"camera:frame-{revision}",
            source="runtime_perception",
            value=value,
            observation_revision=f"frame-{revision}",
        )

    def observe(params: dict[str, Any]) -> MethodResult:
        nonlocal revision
        revision += 1
        value = {"revision": f"frame-{revision}", "payload": dict(state)}
        return MethodResult(value=value, evidence=(evidence(value),))

    def goal(params: dict[str, Any]) -> MethodResult:
        assert params["observation"]["tube_attached"] == state["tube_attached"]
        value = {"satisfied": bool(state[params["goal"]])}
        return MethodResult(value=value, evidence=(evidence(value),))

    broker = MethodBroker(
        [
            MethodSpec("perception.observe", observe),
            MethodSpec("verification.goal_satisfied", goal),
        ]
    )
    bindings = BrokerPolicyBindings(broker)

    def controller(node, observation):
        del observation
        state[node.goal] = True
        if node.action == "pick":
            state["tube_upright"] = True
        return ControllerResult(ok=True)

    policy = bindings.build_policy(
        graph,
        {node.controller_ref: controller for node in graph.nodes},
    )
    result = policy.run()

    assert result.succeeded is True
    assert result.nodes[1].action == "reorient"
    assert result.nodes[1].status.value == "skipped"
    assert {record.method for record in broker.audit_records} == {
        "perception.observe",
        "verification.goal_satisfied",
    }


def test_observation_rejects_stale_evidence_revision() -> None:
    value = {"revision": "frame-2", "payload": {"done": False}}
    stale = EvidenceRef.from_value(
        evidence_id="camera:frame-1",
        source="runtime_perception",
        value=value,
        observation_revision="frame-1",
    )
    broker = MethodBroker(
        [
            MethodSpec(
                "perception.observe",
                lambda params: MethodResult(value=value, evidence=(stale,)),
            )
        ]
    )
    node = ConstraintGraph.load_json(
        Path(__file__).parents[2]
        / "method"
        / "demo_graph"
        / "examples"
        / "m1_graph.json"
    ).node("pick")

    with pytest.raises(M1BindingError, match="revision"):
        BrokerPolicyBindings(broker).observe(node)
