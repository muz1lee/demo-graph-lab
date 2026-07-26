"""Direct Python node-policy runner for the M1 vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from ._json import JsonValue, content_digest, freeze_json
from .models import ConstraintGraph, Node
from .provenance import Provenance, assert_method_safe


@dataclass(frozen=True, slots=True)
class Observation:
    revision: str
    payload: Mapping[str, JsonValue]
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.revision.strip():
            raise ValueError("observation revision must not be empty")
        object.__setattr__(
            self,
            "payload",
            freeze_json(self.payload, path=f"observation[{self.revision}]"),
        )
        assert_method_safe(self)

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True, slots=True)
class ControllerResult:
    """One bounded result from a trusted controller callable.

    For insertion, the controller may run its own high-frequency servo loop;
    the generated policy sees only this terminal bounded result.
    """

    ok: bool
    reason: str = ""
    recoverable: bool = False
    constraint_id: str | None = None

    def __post_init__(self) -> None:
        if self.ok and self.recoverable:
            raise ValueError("a successful controller result cannot be recoverable")
        if not self.ok and not self.reason.strip():
            raise ValueError("a failed controller result requires a reason")


Observe = Callable[[Node], Observation]
GoalSatisfied = Callable[[Node, Observation], bool]
TrustedController = Callable[[Node, Observation], ControllerResult]


class NodeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NodeRunResult:
    node_id: str
    action: str
    status: NodeStatus
    attempts: int
    observed_revisions: tuple[str, ...]
    failure_constraint_id: str | None = None
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {NodeStatus.SUCCEEDED, NodeStatus.SKIPPED}


@dataclass(frozen=True, slots=True)
class PolicyRunResult:
    graph_digest: str
    succeeded: bool
    nodes: tuple[NodeRunResult, ...]
    reason: str = ""


@dataclass(slots=True)
class PythonNodePolicy:
    """Execute graph nodes directly with fresh observations and bounded retry."""

    graph: ConstraintGraph
    observe: Observe
    goal_satisfied: GoalSatisfied
    controllers: Mapping[str, TrustedController]
    max_node_visits: int = 32

    def __post_init__(self) -> None:
        if self.max_node_visits < 1:
            raise ValueError("max_node_visits must be >= 1")
        missing = {
            node.controller_ref
            for node in self.graph.nodes
            if node.controller_ref not in self.controllers
        }
        if missing:
            raise ValueError(f"missing trusted controllers: {sorted(missing)}")

    def run(self) -> PolicyRunResult:
        results: list[NodeRunResult] = []
        current: str | None = self.graph.entry_node

        for _ in range(self.max_node_visits):
            if current is None:
                return PolicyRunResult(
                    graph_digest=self.graph.digest,
                    succeeded=True,
                    nodes=tuple(results),
                )
            node = self.graph.node(current)
            result = self._run_node(node)
            results.append(result)
            if not result.succeeded:
                return PolicyRunResult(
                    graph_digest=self.graph.digest,
                    succeeded=False,
                    nodes=tuple(results),
                    reason=result.reason,
                )
            current = node.next_node

        return PolicyRunResult(
            graph_digest=self.graph.digest,
            succeeded=False,
            nodes=tuple(results),
            reason="graph exceeded max_node_visits",
        )

    def _run_node(self, node: Node) -> NodeRunResult:
        revisions: list[str] = []
        last_reason = ""
        last_constraint: str | None = None

        for attempt in range(1, node.max_attempts + 1):
            observation, error = self._safe_observe(node)
            if error is not None:
                return self._failure(
                    node, attempt - 1, revisions, f"observe failed: {error}"
                )
            assert observation is not None
            revisions.append(observation.revision)

            goal, error = self._safe_goal(node, observation)
            if error is not None:
                return self._failure(
                    node, attempt - 1, revisions, f"goal check failed: {error}"
                )
            if goal:
                return NodeRunResult(
                    node_id=node.node_id,
                    action=node.action,
                    status=NodeStatus.SKIPPED,
                    attempts=attempt - 1,
                    observed_revisions=tuple(revisions),
                )

            controller = self.controllers[node.controller_ref]
            try:
                controlled = controller(node, observation)
            except Exception as error:
                controlled = ControllerResult(
                    ok=False,
                    reason=f"{type(error).__name__}: {error}",
                    recoverable=True,
                )
            if not isinstance(controlled, ControllerResult):
                return self._failure(
                    node,
                    attempt,
                    revisions,
                    "trusted controller did not return ControllerResult",
                    constraint_id=f"contract:{node.node_id}",
                )
            if controlled.constraint_id is not None:
                if controlled.constraint_id not in node.attributable_ids:
                    return self._failure(
                        node,
                        attempt,
                        revisions,
                        "controller attributed failure to an unknown constraint",
                        constraint_id=f"contract:{node.node_id}",
                    )
                last_constraint = controlled.constraint_id
            if not controlled.ok:
                last_reason = controlled.reason
                if controlled.recoverable and attempt < node.max_attempts:
                    continue
                return self._failure(
                    node,
                    attempt,
                    revisions,
                    controlled.reason,
                    constraint_id=last_constraint,
                )

            post_observation, error = self._safe_observe(node)
            if error is not None:
                return self._failure(
                    node, attempt, revisions, f"post-observe failed: {error}"
                )
            assert post_observation is not None
            revisions.append(post_observation.revision)
            goal, error = self._safe_goal(node, post_observation)
            if error is not None:
                return self._failure(
                    node, attempt, revisions, f"postcondition check failed: {error}"
                )
            if goal:
                return NodeRunResult(
                    node_id=node.node_id,
                    action=node.action,
                    status=NodeStatus.SUCCEEDED,
                    attempts=attempt,
                    observed_revisions=tuple(revisions),
                )
            last_reason = "goal not satisfied after controller"
            last_constraint = node.constraints[0].constraint_id

        return self._failure(
            node,
            node.max_attempts,
            revisions,
            last_reason or "attempt budget exhausted",
            constraint_id=last_constraint,
        )

    def _safe_observe(
        self, node: Node
    ) -> tuple[Observation | None, str | None]:
        try:
            observation = self.observe(node)
            if not isinstance(observation, Observation):
                return None, "observe did not return Observation"
            return observation, None
        except Exception as error:
            return None, f"{type(error).__name__}: {error}"

    def _safe_goal(
        self, node: Node, observation: Observation
    ) -> tuple[bool, str | None]:
        try:
            result = self.goal_satisfied(node, observation)
            if not isinstance(result, bool):
                return False, "goal_satisfied did not return bool"
            return result, None
        except Exception as error:
            return False, f"{type(error).__name__}: {error}"

    @staticmethod
    def _failure(
        node: Node,
        attempts: int,
        revisions: list[str],
        reason: str,
        *,
        constraint_id: str | None = None,
    ) -> NodeRunResult:
        return NodeRunResult(
            node_id=node.node_id,
            action=node.action,
            status=NodeStatus.FAILED,
            attempts=attempts,
            observed_revisions=tuple(revisions),
            failure_constraint_id=(
                constraint_id or node.constraints[0].constraint_id
            ),
            reason=reason,
        )
