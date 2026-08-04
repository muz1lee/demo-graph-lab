"""Non-privileged planning-only runtime for the first online baseline.

This runtime obtains a sensor observation, hard-filters supplied candidates,
applies deterministic demo preferences, and exposes only opaque hole handles to
the generated policy.  It contains no controller and every control primitive is
blocked explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from ..perception import ObservationPacket
from ..selection.binding import validate_candidate_bindings
from ..selection.candidates import (
    CandidateBundle,
    CandidateCheckTrace,
    CheckCertificate,
    CheckStatus,
    DecisionTrace,
    HardCheck,
    HardFilterResult,
    REQUIRED_HARD_CHECKS,
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
TYPED_BINDING_CHECK = "typed_hole_values"


def stage_preferences(stage: Mapping) -> tuple[str | None, str | None]:
    """Read one unambiguous ``(region, cone)`` pair from a graph stage."""

    def preference(constraint_name: str, arg_name: str) -> str | None:
        values = {
            (constraint.get("args") or {}).get(arg_name)
            for constraint in stage.get("constraints", [])
            if constraint.get("name") == constraint_name
        }
        values.discard(None)
        if len(values) > 1:
            raise ValueError(
                f"stage {stage['index']} has conflicting "
                f"{constraint_name} preferences"
            )
        return next(iter(values), None)

    return (
        preference("region_grasp", "region"),
        preference("approach_direction", "cone"),
    )


def filter_stage_candidates(
    stage: Mapping,
    observation: ObservationPacket,
    candidates: Sequence[CandidateBundle],
    checks: Sequence[HardCheck],
    *,
    required_holes: Iterable[str] | None = None,
) -> HardFilterResult:
    """Validate typed bindings, then run physical checks only on valid values."""

    items = tuple(candidates)
    if any(not isinstance(item, CandidateBundle) for item in items):
        raise TypeError("candidates must contain CandidateBundle values")
    ids = [item.candidate_id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate_id values must be unique")

    check_items = tuple(checks)
    check_names = [item.name for item in check_items]
    if len(check_names) != len(set(check_names)):
        raise ValueError("hard check names must be unique")
    if TYPED_BINDING_CHECK in check_names:
        raise ValueError(
            f"hard check name {TYPED_BINDING_CHECK!r} is reserved"
        )
    ordered_check_names = list(REQUIRED_HARD_CHECKS)
    ordered_check_names.extend(
        sorted(set(check_names) - set(REQUIRED_HARD_CHECKS))
    )

    validations = {
        item.candidate_id: validate_candidate_bindings(
            item,
            stage,
            observation,
            required_holes=required_holes,
        )
        for item in items
    }
    valid = tuple(
        item
        for item in items
        if validations[item.candidate_id].status is CheckStatus.PASS
    )
    physical = hard_filter(valid, observation, check_items)
    physical_traces = {
        item.candidate.candidate_id: item
        for item in physical.traces
    }

    traces = []
    for candidate in items:
        validation = validations[candidate.candidate_id]
        binding_certificate = CheckCertificate(
            check=TYPED_BINDING_CHECK,
            status=validation.status,
            reason=";".join(validation.reasons),
            evidence_refs=tuple(dict.fromkeys(
                (
                    *candidate.evidence_refs,
                    observation.calibration_ref,
                    *observation.sensor_refs,
                    *(ref for item in observation.objects
                      for ref in item.evidence_refs),
                )
            )),
        )
        physical_trace = physical_traces.get(candidate.candidate_id)
        if physical_trace is not None:
            certificates = (binding_certificate, *physical_trace.certificates)
            accepted = physical_trace.accepted
        else:
            certificates = (
                binding_certificate,
                *(
                    CheckCertificate(
                        check=name,
                        status=CheckStatus.UNKNOWN,
                        reason=(
                            "not_run:typed_hole_values_"
                            f"{validation.status.value.lower()}"
                        ),
                    )
                    for name in ordered_check_names
                ),
            )
            accepted = False
        traces.append(CandidateCheckTrace(
            candidate=candidate,
            certificates=tuple(certificates),
            accepted=accepted,
        ))
    return HardFilterResult(accepted=physical.accepted, traces=tuple(traces))


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
        stage_program: dict | None = None,
    ) -> None:
        required_holes_by_stage = None
        if stage_program is not None:
            from ..policy.program import validate_program, wired_holes_by_stage

            violations = validate_program(stage_program, graph)
            if violations:
                raise ValueError(
                    f"stage_program is invalid for this graph: {violations[:3]}"
                )
            required_holes_by_stage = wired_holes_by_stage(stage_program)

        self.graph = graph
        self._observation_provider = observation_provider
        self._candidate_provider = candidate_provider
        self._hard_checks = tuple(hard_checks)
        self.decision_log_path = Path(decision_log_path)
        self.decisions: list[DecisionTrace] = []

        self._stages: dict[int, dict] = {}
        self._holes: dict[int, set[str]] = {}
        self._required_holes: dict[int, tuple[str, ...]] = {}
        for stage in graph.get("stages", []):
            index = stage.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError("stage index must be a non-negative integer")
            if index in self._stages:
                raise ValueError(f"duplicate stage index: {index}")
            names = [hole["name"] for hole in stage.get("holes", [])]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate hole name in stage {index}")
            self._stages[index] = stage
            self._holes[index] = set(names)
            default_required = tuple(
                hole["name"]
                for hole in stage.get("holes", [])
                if hole.get("type") in {"pose_se3", "axis_3d", "point_3d"}
            )
            configured = None if required_holes_by_stage is None else (
                required_holes_by_stage.get(index)
            )
            required = default_required if configured is None else tuple(configured)
            unknown = set(required) - self._holes[index]
            if unknown:
                raise ValueError(
                    f"stage {index} required unknown holes: {sorted(unknown)}"
                )
            if len(required) != len(set(required)):
                raise ValueError(f"stage {index} required holes must be unique")
            self._required_holes[index] = required

        if required_holes_by_stage is not None:
            unknown_stages = set(required_holes_by_stage) - set(self._stages)
            if unknown_stages:
                raise ValueError(
                    f"required holes reference unknown stages: {sorted(unknown_stages)}"
                )

        self._active_stage_index: int | None = None
        self._active_observation: ObservationPacket | None = None
        self._selected: CandidateBundle | None = None
        self._handles: dict[str, OpaqueHandle] = {}
        self._handle_values: dict[OpaqueHandle, object] = {}

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
        self._active_observation = None
        self._selected = None
        self._handles.clear()
        self._handle_values.clear()

        index = stage.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("stage index must be a non-negative integer")
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

        filtered = filter_stage_candidates(
            declared,
            observation,
            candidates,
            self._hard_checks,
            required_holes=self._required_holes[index],
        )
        region, cone = stage_preferences(declared)
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
        self._active_observation = observation
        self._selected = selection.selected
        if self._selected is None:
            raise NoFeasibleCandidate(
                f"stage {index} has no candidate that passed every hard check"
            )

    def solve(self, hole_name: str) -> OpaqueHandle:
        """Return an identity-only handle for a value on the selected candidate."""

        if (self._active_stage_index is None or self._selected is None
                or self._active_observation is None):
            raise NoFeasibleCandidate("begin_stage must select a candidate before solve")
        if hole_name not in self._holes[self._active_stage_index]:
            raise KeyError(
                f"hole {hole_name!r} is not declared in stage {self._active_stage_index}"
            )
        validation = validate_candidate_bindings(
            self._selected,
            self._stages[self._active_stage_index],
            self._active_observation,
            required_holes=(hole_name,),
        )
        if validation.status is not CheckStatus.PASS:
            raise NoFeasibleCandidate(
                f"candidate {self._selected.candidate_id!r} cannot bind "
                f"hole {hole_name!r}: {';'.join(validation.reasons)}"
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
