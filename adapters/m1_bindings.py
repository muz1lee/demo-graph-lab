"""Thin bindings from the M1 Python policy runner to trusted adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from method.demo_graph import (
    ConstraintGraph,
    ControllerResult,
    Node,
    Observation,
    Provenance,
    ProvenanceSource,
    PythonNodePolicy,
)

from .contracts import EvidenceRef, MethodResult
from .method_broker import MethodBroker


class M1BindingError(RuntimeError):
    pass


class BrokerPolicyBindings:
    """Provide the three callables consumed by ``PythonNodePolicy``."""

    def __init__(self, broker: MethodBroker) -> None:
        self._broker = broker

    def observe(self, node: Node) -> Observation:
        result = self._broker.call(
            "perception.observe",
            {
                "node_id": node.node_id,
                "action": node.action,
                "goal": node.goal,
            },
        )
        value = _mapping_value(result, "perception.observe")
        revision = value.get("revision")
        payload = value.get("payload")
        if not isinstance(revision, str) or not revision:
            raise M1BindingError("perception.observe requires non-empty revision")
        if not isinstance(payload, Mapping):
            raise M1BindingError("perception.observe requires object payload")
        return Observation(
            revision=revision,
            payload=payload,
            provenance=_observation_provenance(result.evidence, revision=revision),
        )

    def goal_satisfied(self, node: Node, observation: Observation) -> bool:
        result = self._broker.call(
            "verification.goal_satisfied",
            {
                "node_id": node.node_id,
                "goal": node.goal,
                "observation_revision": observation.revision,
                "observation_digest": observation.digest,
                "observation": observation.payload,
            },
        )
        _observation_provenance(result.evidence, revision=observation.revision)
        value = _mapping_value(result, "verification.goal_satisfied")
        satisfied = value.get("satisfied")
        if not isinstance(satisfied, bool):
            raise M1BindingError(
                "verification.goal_satisfied requires boolean satisfied"
            )
        return satisfied

    def build_policy(
        self,
        graph: ConstraintGraph,
        controllers: Mapping[str, Callable[[Node, Observation], ControllerResult]],
    ) -> PythonNodePolicy:
        return PythonNodePolicy(
            graph=graph,
            observe=self.observe,
            goal_satisfied=self.goal_satisfied,
            controllers=controllers,
        )


def _mapping_value(result: MethodResult, method: str) -> Mapping[str, Any]:
    if not isinstance(result.value, Mapping):
        raise M1BindingError(f"{method} must return an object")
    return result.value


def _observation_provenance(
    evidence: tuple[EvidenceRef, ...],
    *,
    revision: str,
) -> Provenance:
    parents: list[Provenance] = []
    for item in evidence:
        if item.source not in {"runtime_perception", "robot_state"}:
            raise M1BindingError(
                "M1 observation must come from perception or robot-state evidence"
            )
        if (
            item.observation_revision is not None
            and item.observation_revision != revision
        ):
            raise M1BindingError(
                "M1 evidence revision does not match the observation"
            )
        source = (
            ProvenanceSource.RUNTIME_PERCEPTION
            if item.source == "runtime_perception"
            else ProvenanceSource.ROBOT_STATE
        )
        parents.append(
            Provenance(
                source=source,
                reference=f"{item.evidence_id} ({item.digest})",
            )
        )
    if len(parents) == 1:
        return parents[0]
    return Provenance.derived("combined method-visible observation", *parents)
