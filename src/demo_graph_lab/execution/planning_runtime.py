"""Non-privileged planning-only runtime for the first online baseline.

This runtime obtains a sensor observation, hard-filters supplied candidates,
applies deterministic demo preferences, and exposes only opaque hole handles to
the generated policy.  It contains no controller and every control primitive is
blocked explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Sequence

from ..perception import ObservationPacket
from ..selection.candidates import (
    CandidateBundle,
    DecisionTrace,
    HardCheck,
    deterministic_select,
    hard_filter,
)


class ExecutionDisabled(RuntimeError):
    """Raised when a planning-only runtime receives a control action."""


class NoFeasibleCandidate(RuntimeError):
    """Raised after a fail-closed decision leaves no candidate."""


class OpaqueHandle:
    """Identity-only policy value; numeric candidate data stays in the runtime."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<opaque-handle>"


ObservationProvider = Callable[[dict], ObservationPacket]
CandidateProvider = Callable[[dict, ObservationPacket], Sequence[CandidateBundle]]


class PlanningOnlyRuntime:
    """Prepare stage decisions without any generative backend or control path."""

    backend_model_enabled = False
    execution_enabled = False

    def __init__(
        self,
        graph: dict,
        observation_provider: ObservationProvider,
        candidate_provider: CandidateProvider,
        hard_checks: Sequence[HardCheck],
        decision_log_path: str | Path,
    ) -> None:
        self.graph = graph
        self._observation_provider = observation_provider
        self._candidate_provider = candidate_provider
        self._hard_checks = tuple(hard_checks)
        self.decision_log_path = Path(decision_log_path)
        self.decisions: list[DecisionTrace] = []

        self._stages: dict[int, dict] = {}
        self._holes: dict[int, set[str]] = {}
        for stage in graph.get("stages", []):
            index = int(stage["index"])
            if index in self._stages:
                raise ValueError(f"duplicate stage index: {index}")
            names = [hole["name"] for hole in stage.get("holes", [])]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate hole name in stage {index}")
            self._stages[index] = stage
            self._holes[index] = set(names)

        self._active_stage_index: int | None = None
        self._selected: CandidateBundle | None = None
        self._handles: dict[str, OpaqueHandle] = {}
        self._handle_values: dict[OpaqueHandle, object] = {}

    @staticmethod
    def _preference(stage: dict, constraint_name: str, arg_name: str) -> str | None:
        values = {
            (constraint.get("args") or {}).get(arg_name)
            for constraint in stage.get("constraints", [])
            if constraint.get("name") == constraint_name
        }
        values.discard(None)
        if len(values) > 1:
            raise ValueError(
                f"stage {stage['index']} has conflicting {constraint_name} preferences"
            )
        return next(iter(values), None)

    def _append_decision(self, trace: DecisionTrace) -> None:
        record = trace.to_record()
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.decision_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.decision_log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        self.decisions.append(trace)

    def begin_stage(self, stage: dict) -> None:
        """Observe and select once for a declared stage; never execute an action."""

        # Fail closed across stages: no provider, checker, selector, or log error
        # may leave the previous selection reachable through solve().
        self._active_stage_index = None
        self._selected = None
        self._handles.clear()
        self._handle_values.clear()

        index = int(stage["index"])
        declared = self._stages.get(index)
        if declared is None:
            raise ValueError(f"stage {index} is not declared in graph")
        if stage is not declared and stage != declared:
            raise ValueError(f"stage {index} does not match the graph declaration")

        observation = self._observation_provider(declared)
        if not isinstance(observation, ObservationPacket):
            raise TypeError("observation_provider must return ObservationPacket")
        candidates = tuple(self._candidate_provider(declared, observation))
        if any(not isinstance(item, CandidateBundle) for item in candidates):
            raise TypeError("candidate_provider must return CandidateBundle values")

        filtered = hard_filter(candidates, observation, self._hard_checks)
        region = self._preference(declared, "region_grasp", "region")
        cone = self._preference(declared, "approach_direction", "cone")
        selection = deterministic_select(filtered.accepted, region=region, cone=cone)
        trace = DecisionTrace(
            stage_index=index,
            stage_name=str(declared["name"]),
            observation=observation,
            checks=filtered.traces,
            ranking=tuple(item.candidate_id for item in selection.ranked),
            selected_candidate_id=(
                selection.selected.candidate_id if selection.selected is not None else None
            ),
            preferences={"region": region, "cone": cone},
            ranking_meta={
                "region": selection.region_meta,
                "cone": selection.cone_meta,
            },
        )
        self._append_decision(trace)

        self._active_stage_index = index
        self._selected = selection.selected
        if self._selected is None:
            raise NoFeasibleCandidate(
                f"stage {index} has no candidate that passed every hard check"
            )

    def solve(self, hole_name: str) -> OpaqueHandle:
        """Return an identity-only handle for a value on the selected candidate."""

        if self._active_stage_index is None or self._selected is None:
            raise NoFeasibleCandidate("begin_stage must select a candidate before solve")
        if hole_name not in self._holes[self._active_stage_index]:
            raise KeyError(
                f"hole {hole_name!r} is not declared in stage {self._active_stage_index}"
            )
        if (hole_name not in self._selected.hole_values
                or self._selected.hole_values[hole_name] is None):
            raise NoFeasibleCandidate(
                f"candidate {self._selected.candidate_id!r} has no usable value "
                f"for hole {hole_name!r}"
            )
        if hole_name not in self._handles:
            handle = OpaqueHandle()
            self._handles[hole_name] = handle
            self._handle_values[handle] = self._selected.hole_values[hole_name]
        return self._handles[hole_name]

    @staticmethod
    def _blocked(operation: str) -> None:
        raise ExecutionDisabled(
            f"{operation} is disabled: PlanningOnlyRuntime stops before execution"
        )

    def approach(self, target, cone=None) -> None:
        self._blocked("approach")

    def grasp_at(self, grasp_pose, axis=None) -> None:
        self._blocked("grasp_at")

    def lift(self, obj) -> None:
        self._blocked("lift")

    def transport(self, obj, target) -> None:
        self._blocked("transport")

    def align(self, obj, target, axis=None) -> None:
        self._blocked("align")

    def lower_until(self, stop_condition) -> None:
        self._blocked("lower_until")

    def release(self) -> None:
        self._blocked("release")

    def retreat(self, target) -> None:
        self._blocked("retreat")
