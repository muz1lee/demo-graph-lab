"""Candidate hard filtering and deterministic demo-conditioned selection.

Hard checks decide whether a candidate may proceed.  Qualitative region and
approach preferences only reorder candidates that passed every hard check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from ..perception import ObservationPacket
from . import regions


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


REQUIRED_HARD_CHECKS = ("reachability", "collision_free", "gripper_width")


def _freeze_json(value, path: str):
    """Normalize provider data to immutable, finite, JSON-safe values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise TypeError(f"{path} keys must be non-empty strings")
            normalized[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(normalized)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise TypeError(f"{path} contains non-JSON value {type(value).__name__}")


def _to_json(value):
    if isinstance(value, Mapping):
        return {key: _to_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_json(item) for item in value]
    return value


@dataclass(frozen=True)
class CandidateBundle:
    """One trusted candidate and the values it could bind to typed holes."""

    candidate_id: str
    hole_values: Mapping[str, Any]
    features: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        if "id" in self.features:
            raise ValueError("candidate feature name 'id' is reserved")
        object.__setattr__(self, "hole_values", _freeze_json(
            self.hole_values, f"candidate[{self.candidate_id}].hole_values"))
        object.__setattr__(self, "features", _freeze_json(
            self.features, f"candidate[{self.candidate_id}].features"))
        if (not isinstance(self.evidence_refs, tuple)
                or any(not isinstance(ref, str) or not ref for ref in self.evidence_refs)
                or len(self.evidence_refs) != len(set(self.evidence_refs))):
            raise ValueError("candidate evidence_refs must be a tuple of unique strings")

    def to_record(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "hole_values": _to_json(self.hole_values),
            "features": _to_json(self.features),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class CheckCertificate:
    """Auditable result from one physical hard check."""

    check: str
    status: CheckStatus
    reason: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.check:
            raise ValueError("check name must not be empty")
        if not isinstance(self.status, CheckStatus):
            raise TypeError("status must be a CheckStatus")
        if not self.reason:
            raise ValueError("check certificate must include a reason")

    def to_record(self) -> dict:
        return {
            "check": self.check,
            "status": self.status.value,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


CheckFunction = Callable[[CandidateBundle, ObservationPacket], CheckCertificate]


@dataclass(frozen=True)
class HardCheck:
    name: str
    evaluate: CheckFunction

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("hard check name must not be empty")


@dataclass(frozen=True)
class CandidateCheckTrace:
    candidate: CandidateBundle
    certificates: tuple[CheckCertificate, ...]
    accepted: bool

    def to_record(self) -> dict:
        return {
            "candidate": self.candidate.to_record(),
            "accepted": self.accepted,
            "certificates": [item.to_record() for item in self.certificates],
        }


@dataclass(frozen=True)
class HardFilterResult:
    accepted: tuple[CandidateBundle, ...]
    traces: tuple[CandidateCheckTrace, ...]


@dataclass(frozen=True)
class SelectionResult:
    ranked: tuple[CandidateBundle, ...]
    selected: CandidateBundle | None
    region_meta: Mapping[str, Any] | None
    cone_meta: Mapping[str, Any] | None


@dataclass(frozen=True)
class DecisionTrace:
    stage_index: int
    stage_name: str
    observation: ObservationPacket
    checks: tuple[CandidateCheckTrace, ...]
    ranking: tuple[str, ...]
    selected_candidate_id: str | None
    preferences: Mapping[str, str | None]
    ranking_meta: Mapping[str, Any]

    def to_record(self) -> dict:
        return {
            "stage_index": self.stage_index,
            "stage_name": self.stage_name,
            "status": "SELECTED" if self.selected_candidate_id else "NO_FEASIBLE_CANDIDATE",
            "observation": self.observation.to_record(),
            "candidates": [item.to_record() for item in self.checks],
            "ranking": list(self.ranking),
            "selected_candidate_id": self.selected_candidate_id,
            "preferences": dict(self.preferences),
            "ranking_meta": _to_json(self.ranking_meta),
        }


def _unique_candidates(candidates: Sequence[CandidateBundle]) -> tuple[CandidateBundle, ...]:
    items = tuple(candidates)
    ids = [item.candidate_id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate_id values must be unique")
    return items


def hard_filter(
    candidates: Sequence[CandidateBundle],
    observation: ObservationPacket,
    checks: Sequence[HardCheck],
) -> HardFilterResult:
    """Keep a candidate only when every required hard check returns ``PASS``.

    Missing checks, checker exceptions, and explicit ``UNKNOWN`` results are
    recorded and rejected.  This is fail-closed: none of them can become a
    feasible candidate by default.
    """

    items = _unique_candidates(candidates)
    by_name = {check.name: check for check in checks}
    if len(by_name) != len(checks):
        raise ValueError("hard check names must be unique")

    ordered_names = list(REQUIRED_HARD_CHECKS)
    ordered_names.extend(sorted(set(by_name) - set(REQUIRED_HARD_CHECKS)))
    accepted = []
    traces = []
    for candidate in items:
        certificates = []
        for name in ordered_names:
            check = by_name.get(name)
            if check is None:
                certificate = CheckCertificate(
                    check=name,
                    status=CheckStatus.UNKNOWN,
                    reason="check_not_configured",
                )
            else:
                try:
                    certificate = check.evaluate(candidate, observation)
                except Exception as error:  # error stays visible in the decision trace
                    certificate = CheckCertificate(
                        check=name,
                        status=CheckStatus.UNKNOWN,
                        reason=f"checker_error:{type(error).__name__}:{error}",
                    )
                if certificate.check != name:
                    raise ValueError(
                        f"hard check {name!r} returned certificate for {certificate.check!r}"
                    )
            certificates.append(certificate)

        survived = all(item.status is CheckStatus.PASS for item in certificates)
        if survived:
            accepted.append(candidate)
        traces.append(
            CandidateCheckTrace(
                candidate=candidate,
                certificates=tuple(certificates),
                accepted=survived,
            )
        )
    return HardFilterResult(accepted=tuple(accepted), traces=tuple(traces))


def deterministic_select(
    candidates: Sequence[CandidateBundle],
    *,
    region: str | None = None,
    cone: str | None = None,
) -> SelectionResult:
    """Select by fixed ID, cone, then region ordering; never call a model.

    Region is the primary preference and cone breaks region ties.  Candidate ID
    is the final stable tie-break, so provider ordering cannot change the result.
    """

    items = _unique_candidates(candidates)
    by_id = {item.candidate_id: item for item in items}
    records = [
        {"id": item.candidate_id, **dict(item.features)}
        for item in sorted(items, key=lambda item: item.candidate_id)
    ]

    cone_meta = None
    if cone is not None:
        records, cone_meta = regions.rank_by_cone(records, cone, with_meta=True)

    region_meta = None
    if region is not None:
        records, region_meta = regions.rank_by_region(records, region, with_meta=True)

    ranked = tuple(by_id[item["id"]] for item in records)
    return SelectionResult(
        ranked=ranked,
        selected=ranked[0] if ranked else None,
        region_meta=region_meta,
        cone_meta=cone_meta,
    )
