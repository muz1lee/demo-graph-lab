"""Non-privileged planning-only runtime for the first online baseline.

This runtime obtains a sensor observation, hard-filters supplied candidates,
applies deterministic demo preferences, and exposes only opaque hole handles to
the generated policy.  It contains no controller and every control primitive is
blocked explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Callable, Iterable, Mapping, Sequence

from ..perception import ObservationPacket
from ..policy import backchain
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
FutureCompatibility = Callable[
    [CandidateBundle, ObservationPacket, Mapping, Mapping, str, Mapping],
    CheckCertificate,
]
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
        future_compatibility: FutureCompatibility | None = None,
    ) -> None:
        required_holes_by_stage = None
        explicit_selection_stages: frozenset[int] = frozenset()
        if stage_program is not None:
            from ..policy.program import validate_program, wired_holes_by_stage

            violations = validate_program(stage_program, graph)
            if violations:
                raise ValueError(
                    f"stage_program is invalid for this graph: {violations[:3]}"
                )
            required_holes_by_stage = wired_holes_by_stage(stage_program)
            explicit_selection_stages = frozenset(
                stage["index"]
                for stage in stage_program["stages"]
                if isinstance(stage.get("selection"), dict)
            )

        self.graph = graph
        self._observation_provider = observation_provider
        self._candidate_provider = candidate_provider
        self._hard_checks = tuple(hard_checks)
        self._future_compatibility = future_compatibility
        self.decision_log_path = Path(decision_log_path)
        self.decisions: list[DecisionTrace] = []
        self._explicit_selection_stages = explicit_selection_stages

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
        self._accepted: tuple[CandidateBundle, ...] = ()
        self._selected: CandidateBundle | None = None
        self._handles: dict[str, OpaqueHandle] = {}
        self._handle_values: dict[OpaqueHandle, object] = {}
        self._constraint_table = backchain.constraint_table(graph)
        self._selection_hole: str | None = None
        self._selection_pool: tuple[CandidateBundle, ...] = ()
        self._selection_region: str | None = None
        self._selection_cone: str | None = None
        self._selection_current: list[str] = []
        self._selection_downstream: list[str] = []
        self._selection_survivors: list[dict] = []
        self._selection_checks: tuple[CandidateCheckTrace, ...] = ()

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
        self._accepted = ()
        self._selected = None
        self._handles.clear()
        self._handle_values.clear()
        self._selection_hole = None
        self._selection_pool = ()
        self._selection_region = None
        self._selection_cone = None
        self._selection_current = []
        self._selection_downstream = []
        self._selection_survivors = []
        self._selection_checks = ()

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
        self._active_stage_index = index
        self._active_observation = observation
        self._accepted = filtered.accepted
        self._selection_checks = filtered.traces
        if index in self._explicit_selection_stages:
            if not self._accepted:
                raise NoFeasibleCandidate(
                    f"stage {index} has no candidate that passed every hard check"
                )
            return

        # Legacy programs without an explicit selection block retain the old
        # deterministic default. New CaP programs make the choice in their handler.
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

        self._selected = selection.selected
        if self._selected is None:
            raise NoFeasibleCandidate(
                f"stage {index} has no candidate that passed every hard check"
            )

    @property
    def selected_candidate_id(self) -> str | None:
        return None if self._selected is None else self._selected.candidate_id

    def begin_candidates(self, grasp_hole: str) -> None:
        """Start the candidate dataflow written explicitly by the CaP program."""
        if self._active_stage_index is None or self._active_observation is None:
            raise NoFeasibleCandidate("begin_stage must run before begin_candidates")
        stage = self._stages[self._active_stage_index]
        holes = {
            hole["name"]: hole for hole in stage.get("holes", [])
            if isinstance(hole, dict) and isinstance(hole.get("name"), str)
        }
        hole = holes.get(grasp_hole)
        if (hole is None or hole.get("type") != "pose_se3"
                or hole.get("resolver") != "grasp_candidate"):
            raise ValueError(
                f"{grasp_hole!r} is not a grasp_candidate pose hole in "
                f"stage {self._active_stage_index}"
            )
        self._selection_hole = grasp_hole
        self._selection_pool = self._accepted
        self._selection_region = None
        self._selection_cone = None
        self._selection_current = []
        self._selection_downstream = []
        self._selection_survivors = []
        self._selected = None
        self._handles.clear()
        self._handle_values.clear()

    def _constraint(self, constraint_ref: str) -> tuple[int, dict]:
        try:
            return self._constraint_table[constraint_ref]
        except KeyError as error:
            raise ValueError(f"unknown constraint ref {constraint_ref!r}") from error

    def rank_by(self, constraint_ref: str) -> None:
        if self._selection_hole is None or self._active_stage_index is None:
            raise NoFeasibleCandidate("begin_candidates must run before rank_by")
        stage_index, constraint = self._constraint(constraint_ref)
        if stage_index != self._active_stage_index:
            raise ValueError(
                f"rank_by only accepts current-stage constraints; got {constraint_ref!r}"
            )
        args = constraint.get("args") or {}
        name = constraint.get("name")
        if name == "region_grasp":
            self._selection_region = args.get("region")
        elif name == "approach_direction":
            self._selection_cone = args.get("cone")
        else:
            raise ValueError(
                f"constraint {constraint_ref!r} is not a ranking preference"
            )
        self._selection_current.append(constraint_ref)

    def require_future(self, constraint_ref: str) -> None:
        if self._selection_hole is None or self._active_stage_index is None:
            raise NoFeasibleCandidate(
                "begin_candidates must run before require_future"
            )
        stage_index, constraint = self._constraint(constraint_ref)
        if stage_index <= self._active_stage_index:
            raise ValueError(
                f"require_future needs a later-stage constraint; got {constraint_ref!r}"
            )
        before = [item.candidate_id for item in self._selection_pool]
        kept = []
        outcomes = {}
        for candidate in self._selection_pool:
            started = time.perf_counter()
            if self._future_compatibility is None:
                certificate = CheckCertificate(
                    check=constraint_ref,
                    status=CheckStatus.UNKNOWN,
                    reason="future_compatibility_not_configured",
                )
            else:
                try:
                    certificate = self._future_compatibility(
                        candidate,
                        self._active_observation,
                        self._stages[self._active_stage_index],
                        self._stages[stage_index],
                        constraint_ref,
                        constraint,
                    )
                except Exception as error:
                    certificate = CheckCertificate(
                        check=constraint_ref,
                        status=CheckStatus.UNKNOWN,
                        reason=(
                            "future_compatibility_error:"
                            f"{type(error).__name__}:{error}"
                        ),
                    )
                if certificate.check != constraint_ref:
                    raise ValueError(
                        "future compatibility returned certificate for "
                        f"{certificate.check!r}, expected {constraint_ref!r}"
                    )
            outcome = certificate.to_record()
            outcome["elapsed_s"] = time.perf_counter() - started
            outcomes[candidate.candidate_id] = outcome
            if certificate.status is CheckStatus.PASS:
                kept.append(candidate)
        self._selection_pool = tuple(kept)
        self._selection_downstream.append(constraint_ref)
        self._selection_survivors.append({
            "constraint": constraint_ref,
            "before": before,
            "outcomes": outcomes,
            "after": [item.candidate_id for item in kept],
        })

    def choose(self, grasp_hole: str) -> OpaqueHandle:
        if self._selection_hole != grasp_hole:
            raise ValueError(
                f"choose hole {grasp_hole!r} does not match active candidate source "
                f"{self._selection_hole!r}"
            )
        selection = deterministic_select(
            self._selection_pool,
            region=self._selection_region,
            cone=self._selection_cone,
        )
        self._selected = selection.selected
        stage = self._stages[self._active_stage_index]
        trace = DecisionTrace(
            stage_index=self._active_stage_index,
            stage_name=str(stage["name"]),
            observation=self._active_observation,
            checks=self._selection_checks,
            ranking=tuple(item.candidate_id for item in selection.ranked),
            selected_candidate_id=self.selected_candidate_id,
            preferences={
                "region": self._selection_region,
                "cone": self._selection_cone,
            },
            ranking_meta={
                "region": selection.region_meta,
                "cone": selection.cone_meta,
                "cap_program": {
                    "current_constraints": list(self._selection_current),
                    "downstream_constraints": list(self._selection_downstream),
                    "future_filter": list(self._selection_survivors),
                },
            },
        )
        self._append_decision(trace)
        if self._selected is None:
            raise NoFeasibleCandidate(
                f"stage {self._active_stage_index} has no candidate satisfying "
                "the generated downstream constraints"
            )
        return self.solve(grasp_hole)

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

    def reorient_held_axis(self, obj, object_axis, target_direction) -> None:
        self._blocked("reorient_held_axis")

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
