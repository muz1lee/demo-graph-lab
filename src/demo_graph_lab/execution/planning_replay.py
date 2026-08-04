"""Offline replay of demo-conditioned candidate selection.

The replay owns no sensor, model, policy, or controller.  It loads one fixed
observation with candidate certificates, runs the physical hard filter once,
and compares deterministic selection with and without demo preferences.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ..perception import ObservationPacket
from ..perception.adapters import (
    candidate_from_record,
    observation_from_record,
)
from ..selection.binding import validate_candidate_bindings
from ..selection.candidates import (
    REQUIRED_HARD_CHECKS,
    CandidateBundle,
    CheckCertificate,
    CheckStatus,
    HardCheck,
    deterministic_select,
)
from .planning_runtime import filter_stage_candidates, stage_preferences


_SYNTHETIC_SOURCE = "synthetic_contract_fixture"


@dataclass(frozen=True)
class CandidateCertificates:
    candidate_id: str
    certificates: tuple[CheckCertificate, ...]


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    source_kind: str
    stage_index: int
    observation: ObservationPacket
    candidates: tuple[CandidateBundle, ...]
    candidate_certificates: tuple[CandidateCertificates, ...]


def _read_json(path: str | Path):
    def reject_constant(value: str):
        raise ValueError(f"non-finite JSON number {value!r} is not allowed")

    return json.loads(Path(path).read_text("utf-8"), parse_constant=reject_constant)


def _exact_fields(value: dict, expected: set[str], path: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be an object")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ValueError(f"{path} fields mismatch: missing={missing} extra={extra}")


def _stage(graph: dict, index: int) -> dict:
    stages = [stage for stage in graph.get("stages", []) if stage.get("index") == index]
    if len(stages) != 1:
        raise ValueError(f"graph must contain exactly one stage with index {index}")
    return stages[0]


def _required_geometry_holes(stage: dict) -> tuple[str, ...]:
    required = tuple(
        hole["name"]
        for hole in stage.get("holes", [])
        if (isinstance(hole, dict)
            and isinstance(hole.get("name"), str)
            and hole.get("type") in {"pose_se3", "axis_3d", "point_3d"})
    )
    if not required:
        raise ValueError("planning replay stage must declare a geometric hole")
    return required


def _certificate(data: dict, path: str) -> CheckCertificate:
    _exact_fields(data, {"check", "status", "reason", "evidence_refs"}, path)
    try:
        status = CheckStatus(data["status"])
    except ValueError as error:
        raise ValueError(f"{path}.status is invalid: {data['status']!r}") from error
    evidence_refs = data["evidence_refs"]
    if (not isinstance(evidence_refs, list)
            or any(not isinstance(ref, str) or not ref.strip()
                   for ref in evidence_refs)
            or len(evidence_refs) != len(set(evidence_refs))):
        raise ValueError(
            f"{path}.evidence_refs must be a list of unique non-empty strings"
        )
    if status is not CheckStatus.UNKNOWN and not evidence_refs:
        raise ValueError(f"{path}.evidence_refs must not be empty for PASS/FAIL")
    return CheckCertificate(
        check=data["check"],
        status=status,
        reason=data["reason"],
        evidence_refs=tuple(evidence_refs),
    )


def load_replay(path: str | Path, graph: dict) -> ReplayCase:
    """Load and strictly validate one fixed planning replay case."""
    data = _read_json(path)
    _exact_fields(
        data, {
            "case_id", "source_kind", "stage_index", "observation", "candidates",
        },
        "replay",
    )
    if not isinstance(data["case_id"], str) or not data["case_id"]:
        raise ValueError("replay.case_id must be a non-empty string")
    if data["source_kind"] != _SYNTHETIC_SOURCE:
        raise ValueError(
            "only source_kind=synthetic_contract_fixture is supported; "
            "recorded-real provenance is not implemented"
        )
    if isinstance(data["stage_index"], bool) or not isinstance(data["stage_index"], int):
        raise ValueError("replay.stage_index must be an integer")

    stage = _stage(graph, data["stage_index"])
    required_holes = _required_geometry_holes(stage)
    observation = observation_from_record(data["observation"])

    candidates = []
    candidate_certificates = []
    for offset, item in enumerate(data["candidates"]):
        path_prefix = f"replay.candidates[{offset}]"
        _exact_fields(item, {
            "candidate_id", "observation_id", "hole_values", "features",
            "provenance", "evidence_refs", "hard_checks",
        }, path_prefix)
        if item["observation_id"] != observation.observation_id:
            raise ValueError(
                f"{path_prefix}.observation_id must match replay observation"
            )
        candidate = candidate_from_record(
            {key: value for key, value in item.items() if key != "hard_checks"}
        )
        binding = validate_candidate_bindings(
            candidate,
            stage,
            observation,
            required_holes=required_holes,
        )
        if binding.status is not CheckStatus.PASS:
            raise ValueError(
                f"{path_prefix} has invalid typed-hole values "
                f"({binding.status.value}): {';'.join(binding.reasons)}"
            )
        candidates.append(candidate)

        by_check = {}
        for check_offset, raw_check in enumerate(item["hard_checks"]):
            certificate = _certificate(
                raw_check, f"{path_prefix}.hard_checks[{check_offset}]",
            )
            if certificate.check in by_check:
                raise ValueError(
                    f"{path_prefix} contains duplicate hard check {certificate.check!r}"
                )
            by_check[certificate.check] = certificate
        if set(by_check) != set(REQUIRED_HARD_CHECKS):
            raise ValueError(
                f"{path_prefix}.hard_checks must be exactly "
                f"{list(REQUIRED_HARD_CHECKS)}"
            )
        candidate_certificates.append(CandidateCertificates(
            candidate_id=candidate.candidate_id,
            certificates=tuple(by_check[name] for name in REQUIRED_HARD_CHECKS),
        ))

    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("replay candidate_id values must be unique")
    if not candidates:
        raise ValueError("replay must contain at least one candidate")

    return ReplayCase(
        case_id=data["case_id"],
        source_kind=data["source_kind"],
        stage_index=data["stage_index"],
        observation=observation,
        candidates=tuple(candidates),
        candidate_certificates=tuple(candidate_certificates),
    )


def _hard_checks_for_case(case: ReplayCase) -> tuple[HardCheck, ...]:
    """Build inert check callables from validated certificate data only."""

    if case.source_kind != _SYNTHETIC_SOURCE:
        raise ValueError(
            "compare_replay only accepts source_kind=synthetic_contract_fixture"
        )
    if not isinstance(case.candidate_certificates, tuple):
        raise TypeError("candidate_certificates must be a tuple")

    candidate_ids = [candidate.candidate_id for candidate in case.candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("replay candidate_id values must be unique")
    certificate_ids = []
    table: dict[str, dict[str, CheckCertificate]] = {}
    for item in case.candidate_certificates:
        if not isinstance(item, CandidateCertificates):
            raise TypeError(
                "candidate_certificates must contain CandidateCertificates"
            )
        if not isinstance(item.candidate_id, str) or not item.candidate_id:
            raise ValueError("certificate candidate_id must be a non-empty string")
        certificate_ids.append(item.candidate_id)
        if not isinstance(item.certificates, tuple):
            raise TypeError("certificates must be a tuple")
        if any(not isinstance(value, CheckCertificate)
               for value in item.certificates):
            raise TypeError("certificates must contain CheckCertificate values")
        by_check = {certificate.check: certificate for certificate in item.certificates}
        if (len(by_check) != len(item.certificates)
                or tuple(by_check) != REQUIRED_HARD_CHECKS):
            raise ValueError(
                f"certificates for {item.candidate_id!r} must be exactly "
                f"{list(REQUIRED_HARD_CHECKS)} in that order"
            )
        table[item.candidate_id] = by_check
    if len(certificate_ids) != len(set(certificate_ids)):
        raise ValueError("certificate candidate_id values must be unique")
    if set(certificate_ids) != set(candidate_ids):
        raise ValueError("candidate certificates must match replay candidates")

    def make_check(name: str) -> HardCheck:
        def evaluate(candidate: CandidateBundle, current: ObservationPacket):
            if current.observation_id != case.observation.observation_id:
                raise ValueError("replay hard check received a different observation")
            return table[candidate.candidate_id][name]

        return HardCheck(name=name, evaluate=evaluate)

    return tuple(make_check(name) for name in REQUIRED_HARD_CHECKS)


def _selection_record(name: str, selection, region, cone) -> dict:
    return {
        "name": name,
        "preferences": {"region": region, "cone": cone},
        "ranking": [candidate.candidate_id for candidate in selection.ranked],
        "selected_candidate_id": (
            selection.selected.candidate_id if selection.selected is not None else None
        ),
        "ranking_meta": {
            "region": selection.region_meta,
            "cone": selection.cone_meta,
        },
    }


def compare_replay(graph: dict, case: ReplayCase) -> dict:
    """Compare fixed-ID and demo ranking over one shared hard-filter result."""
    if not isinstance(case, ReplayCase):
        raise TypeError("case must be ReplayCase")
    if not isinstance(case.case_id, str) or not case.case_id.strip():
        raise ValueError("case_id must be a non-empty string")
    if isinstance(case.stage_index, bool) or not isinstance(case.stage_index, int):
        raise ValueError("stage_index must be an integer")
    if not isinstance(case.observation, ObservationPacket):
        raise TypeError("observation must be ObservationPacket")
    if (not isinstance(case.candidates, tuple) or not case.candidates
            or any(not isinstance(candidate, CandidateBundle)
                   for candidate in case.candidates)):
        raise TypeError("candidates must be a non-empty CandidateBundle tuple")
    stage = _stage(graph, case.stage_index)
    required_holes = _required_geometry_holes(stage)
    hard_checks = _hard_checks_for_case(case)
    filtered = filter_stage_candidates(
        stage,
        case.observation,
        case.candidates,
        hard_checks,
        required_holes=required_holes,
    )
    no_demo = deterministic_select(filtered.accepted)
    region, cone = stage_preferences(stage)
    demo = deterministic_select(filtered.accepted, region=region, cone=cone)

    no_demo_ranks = {
        candidate.candidate_id: index
        for index, candidate in enumerate(no_demo.ranked, start=1)
    }
    demo_ranks = {
        candidate.candidate_id: index
        for index, candidate in enumerate(demo.ranked, start=1)
    }
    accepted_ids = [candidate.candidate_id for candidate in filtered.accepted]
    no_demo_record = _selection_record(
        "candidate_id_baseline", no_demo, None, None)
    demo_record = _selection_record(
        "demo_region_cone", demo, region, cone)
    return {
        "case_id": case.case_id,
        "source_kind": case.source_kind,
        "status": (
            "OFFLINE_SELECTION_REPLAY"
            if accepted_ids else "NO_FEASIBLE_CANDIDATE"
        ),
        "backend_model_enabled": False,
        "execution_enabled": False,
        "hard_checks_source": "synthetic_fixture_certificates",
        "stage": {"index": case.stage_index, "name": stage["name"]},
        "shared": {
            "observation": case.observation.to_record(),
            "input_candidate_ids": [item.candidate_id for item in case.candidates],
            "accepted_candidate_ids": accepted_ids,
            "candidate_checks": [trace.to_record() for trace in filtered.traces],
        },
        "no_demo": no_demo_record,
        "demo": demo_record,
        "comparison": {
            "top1_changed": (
                no_demo_record["selected_candidate_id"]
                != demo_record["selected_candidate_id"]
            ),
            "ranks": [
                {
                    "candidate_id": candidate_id,
                    "no_demo_rank": no_demo_ranks[candidate_id],
                    "demo_rank": demo_ranks[candidate_id],
                }
                for candidate_id in sorted(no_demo_ranks)
            ],
        },
    }


def run_replay(graph_path: str | Path, replay_path: str | Path) -> dict:
    """Convenience entry point for a future read-only CLI adapter."""
    graph = _read_json(graph_path)
    return compare_replay(graph, load_replay(replay_path, graph))
