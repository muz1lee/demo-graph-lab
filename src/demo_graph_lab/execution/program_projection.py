"""Project frozen optical hole values into ``robot_base`` candidate bindings.

This is the local, zero-network step between ``planning-record --step programs``
and ``PlanningOnlyRuntime``.  It takes the optical-frame envelopes that the
PerceptionProgram executor published, moves them through the measured camera
extrinsics, and emits values a candidate provider may carry.

Three refusals are load-bearing here, and each one is mechanical rather than a
convention someone has to remember:

* **Lift**: the head camera rides a prismatic ``lifting_link``, so a value is
  only transformable together with the ``q_lift`` that held during the very
  observation it came from.  It is read from that observation's own
  proprioception artifact; when it is missing the hole becomes ``UNKNOWN``.
* **Identity**: the executor publishes ``MODEL_PROPOSED`` anchors.  A projected
  value therefore keeps that status and stays out of every candidate until a
  separate acceptance record — written by a named person, with a stated basis —
  exists for that ``(program, object_id)`` pair.  Acceptance is additive: it can
  never resurrect a hole the chain or the frame math already refused.
* **Centroid**: a point-cloud centroid is not a part center.  Only resolvers
  bound to a fitted geometric center may fill a ``point_3d`` hole.

Failure is per hole, not per document: one unusable hole must not hide the
others, so refusals are recorded as ``UNKNOWN`` envelopes with a reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..perception.adapters import observation_from_record
from ..perception.frames import (
    BASE_FRAME,
    OPTICAL_FRAME,
    PASS,
    UNKNOWN,
    CameraExtrinsics,
    direction_to_base,
    load_camera_extrinsics,
    point_to_base,
)
from ..perception.observations import ObservationPacket
from ..perception.program import RESOLVER_BINDINGS
from ..selection.candidates import CandidateBundle
from .object_record import _complete, _frozen_observation
from .planning_record import (
    _load_manifest,
    _one_stage,
    _read_json,
    _required_string,
    _utc_now,
    _write_json,
)


BASE_VALUES_SCHEMA = "demo_graph_lab.base_frame_hole_values.v1"
IDENTITY_ACCEPTANCE_SCHEMA = "demo_graph_lab.identity_acceptance.v1"

_RESULTS_SCHEMA = "demo_graph_lab.perception_program_results.v1"
_IDENTITY_STATUS = "MODEL_PROPOSED"
_PROJECTED_STATUS = "BASE_VALUES_PROJECTED"
_PROGRAMS_STATUS = "PROGRAMS_RECORDED"

_BASE_VALUES_FILE = "base_frame_values.json"
_ACCEPTANCE_FILE = "identity_acceptance.json"
_ARTIFACTS = {
    "base_frame_values": _BASE_VALUES_FILE,
    "identity_acceptance": _ACCEPTANCE_FILE,
}

# 只有拟合几何的中心可以填 point_3d。crop_points 的点云质心是"可见表面的重心"
# 而不是部件中心:8/6 实测它比实体中心偏向相机约一个半径(共模 x≈−10.7mm、
# z≈+12.9mm),把它接到 point_3d 等于把这个偏置直接变成插入点误差。v1 里
# `fit_axis` 只发布 axis,所以这条路径目前根本不存在——这份名单的作用是让将来
# 有人接上 POINTS 质心时必须先在这里显式改名,而不是靠类型相同就混进来。
_POINT_RESOLVERS = {"part_center"}
_AXIS_RESOLVERS = {"part_axis", "principal_axis"}
_HOLE_TYPE_RESOLVERS = {
    "point_3d": _POINT_RESOLVERS,
    "axis_3d": _AXIS_RESOLVERS,
}


@dataclass(frozen=True)
class BaseFrameSources:
    """Providers for one frozen record, in the frame the graph actually asks for.

    ``PlanningOnlyRuntime`` takes an observation provider and a candidate
    provider; these two bound methods are exactly those callables, so wiring the
    runtime to a record needs no adapter class of its own.
    """

    observation: ObservationPacket
    document: Mapping[str, Any]

    def observation_provider(self, stage: Mapping[str, Any]) -> ObservationPacket:
        return self.observation

    def candidate_provider(
        self, stage: Mapping[str, Any], observation: ObservationPacket
    ) -> tuple[CandidateBundle, ...]:
        """Build at most one candidate for ``stage`` from accepted base values.

        Only ``PASS`` values whose identity has been accepted are carried.  A
        hole that was refused anywhere upstream is simply absent, which the
        typed-hole validation then reports as a missing required value — the
        stage fails closed instead of binding a hole nobody vouched for.
        """

        if observation.observation_id != self.observation.observation_id:
            raise ValueError(
                "candidate provider was asked for a different observation"
            )
        index = stage.get("index")
        prefix = f"s{index}."
        hole_values: dict[str, Any] = {}
        evidence: list[str] = []
        programs: dict[str, str] = {}
        for qualified, envelope in self.document["holes"].items():
            if not qualified.startswith(prefix):
                continue
            if envelope["status"] != PASS or not envelope["identity_accepted"]:
                continue
            name = qualified[len(prefix):]
            hole_values[name] = {
                "value": list(envelope["value"]),
                "frame": envelope["frame"],
                "calibration_ref": envelope["calibration_ref"],
                "object_id": envelope["object_id"],
            }
            programs[name] = envelope["program"]
            evidence.extend(envelope["evidence_refs"])
        if not hole_values:
            return ()
        evidence.extend(
            ref for ref in (
                self.document["program_results_ref"],
                self.document["extrinsics_ref"],
                self.document["identity_acceptance_ref"],
            ) if ref
        )
        return (
            CandidateBundle(
                candidate_id=f"program_projection_s{index}",
                observation_id=observation.observation_id,
                hole_values=hole_values,
                features={},
                provenance={
                    # identity_status 留在 provenance 而不是 hole 值里:几何洞的
                    # 值形状是 {value, frame, calibration_ref, object_id} 闭集,
                    # 多一个键会被 typed-hole 校验判为 invalid_value_envelope。
                    "identity_status": _IDENTITY_STATUS,
                    "identity_acceptance_ref": self.document[
                        "identity_acceptance_ref"
                    ],
                    "extrinsics_ref": self.document["extrinsics_ref"],
                    "source_frame": self.document["source_frame"],
                    "q_lift_m": self.document["q_lift"],
                    "q_lift_source": self.document["q_lift_source"],
                    "programs": programs,
                },
                evidence_refs=tuple(dict.fromkeys(evidence)),
            ),
        )


def _acceptance_path(root: Path) -> Path:
    return (root / _ACCEPTANCE_FILE).resolve()


def _program_summaries(results: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    summaries = results.get("programs")
    if not isinstance(summaries, list):
        raise ValueError("program_results.programs must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for summary in summaries:
        if not isinstance(summary, Mapping):
            raise ValueError("program summary must be an object")
        by_id[_required_string(summary.get("program"), "program summary id")] = summary
    return by_id


def _validated_results(root: Path, observation: ObservationPacket) -> Mapping[str, Any]:
    results = _read_json(root / "program_results.json")
    if not isinstance(results, Mapping) or results.get("schema") != _RESULTS_SCHEMA:
        raise ValueError("record directory has no supported program results")
    if results.get("observation_id") != observation.observation_id:
        raise ValueError("program results belong to another observation")
    if results.get("frame") != observation.frame:
        raise ValueError("program results were not measured in the observation frame")
    if results.get("calibration_ref") != observation.calibration_ref:
        raise ValueError("program results do not name this observation calibration")
    if not isinstance(results.get("holes"), Mapping):
        raise ValueError("program_results.holes must be an object")
    return results


def _read_acceptances(root: Path, results: Mapping[str, Any]) -> tuple[
    frozenset[tuple[str, str]], str | None
]:
    path = _acceptance_path(root)
    if not path.is_file():
        return frozenset(), None
    record = _read_json(path)
    if (not isinstance(record, Mapping)
            or record.get("schema") != IDENTITY_ACCEPTANCE_SCHEMA):
        raise ValueError("identity acceptance record is invalid")
    if record.get("observation_id") != results["observation_id"]:
        raise ValueError("identity acceptance belongs to another observation")
    entries = record.get("acceptances")
    if not isinstance(entries, list) or not entries:
        raise ValueError("identity acceptance record must list acceptances")
    accepted = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("identity acceptance entry must be an object")
        accepted.add((
            _required_string(entry.get("program"), "acceptance.program"),
            _required_string(entry.get("object_id"), "acceptance.object_id"),
        ))
        _required_string(entry.get("accepted_by"), "acceptance.accepted_by")
        _required_string(entry.get("basis"), "acceptance.basis")
    return frozenset(accepted), str(path)


def accept_identity(
    record_dir: str | Path,
    *,
    program: str,
    object_id: str,
    accepted_by: str,
    basis: str,
) -> dict[str, Any]:
    """Record one explicit human acceptance of a program's object identity.

    Grounding gives a box and segmentation gives a mask; neither proves the box
    is the graph object the anchor named.  This step is where a person takes
    that responsibility, on the record, for one ``(program, object_id)`` pair.
    It is deliberately not derivable from any model output, and it is refused
    for a program whose own result is ``UNKNOWN`` — accepting an identity must
    never be a way to overturn the executor's or the guard's refusal.
    """

    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") not in {_PROGRAMS_STATUS, _PROJECTED_STATUS}:
        raise ValueError("identity-accept requires recorded perception programs")
    program = _required_string(program, "program")
    object_id = _required_string(object_id, "object_id")
    accepted_by = _required_string(accepted_by, "accepted_by")
    basis = _required_string(basis, "basis")

    _, observation = _frozen_observation(root)
    results = _validated_results(root, observation)
    summaries = _program_summaries(results)
    summary = summaries.get(program)
    if summary is None:
        raise ValueError(f"program {program!r} is absent from program_results.json")
    anchor_object = summary["anchor"]["object_id"]
    if anchor_object != object_id:
        raise ValueError(
            f"program {program!r} is anchored on {anchor_object!r}, "
            f"not {object_id!r}"
        )
    if summary["status"] != PASS:
        raise ValueError(
            f"program {program!r} published {summary['status']} "
            f"({summary['reason']}); identity acceptance cannot revive it"
        )

    path = _acceptance_path(root)
    if path.is_file():
        record = _read_json(path)
        if (not isinstance(record, Mapping)
                or record.get("schema") != IDENTITY_ACCEPTANCE_SCHEMA):
            raise ValueError("identity acceptance record is invalid")
        entries = list(record["acceptances"])
    else:
        entries = []
    if any(item["program"] == program for item in entries):
        raise ValueError(f"program {program!r} already has an acceptance record")
    entries.append({
        "program": program,
        "object_id": object_id,
        "accepted_by": accepted_by,
        "basis": basis,
        "accepted_at": _utc_now(),
        "bbox_pixel": list(summary["bbox_pixel"]),
        "evidence_dir": summary["artifact_dir"],
    })
    _write_json(path, {
        "schema": IDENTITY_ACCEPTANCE_SCHEMA,
        "observation_id": observation.observation_id,
        "program_results_ref": str((root / "program_results.json").resolve()),
        "acceptances": entries,
    })
    return _complete(root, manifest, status=manifest["status"], artifacts=_ARTIFACTS)


def _lift_reading(observation: ObservationPacket) -> tuple[float | None, str, str]:
    """Read the lift position recorded with this very observation."""

    reference = observation.robot_state.evidence_ref
    path = Path(reference)
    if not path.is_file():
        return None, "proprioception_artifact_missing", reference
    record = _read_json(path)
    if not isinstance(record, Mapping) or "lift_position_m" not in record:
        return None, "lift_unrecorded_in_proprioception", reference
    value = record["lift_position_m"]
    if value is None:
        source = record.get("lift_source")
        return None, (
            source if isinstance(source, str) and source.strip()
            else "lift_unrecorded_in_proprioception"
        ), reference
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "lift_reading_malformed", reference
    return float(value), _required_string(
        record.get("lift_source"), "proprioception.lift_source"
    ), reference


def _projected_value(
    hole: Mapping[str, Any],
    envelope: Mapping[str, Any],
    summary: Mapping[str, Any],
    extrinsics: CameraExtrinsics,
    q_lift: float | None,
):
    """Move one optical envelope into ``robot_base`` or say why it cannot move."""

    if envelope["status"] != PASS:
        # 上游的 reason 更具体(链断在哪一步、身份是否撞框),原样传下去。
        return UNKNOWN, None, envelope["reason"]
    hole_type = hole["type"]
    allowed = _HOLE_TYPE_RESOLVERS.get(hole_type)
    if allowed is None:
        return UNKNOWN, None, f"hole_type_not_projected:{hole_type}"
    resolver = hole.get("resolver")
    if resolver not in allowed:
        return UNKNOWN, None, f"resolver_may_not_fill_{hole_type}:{resolver}"
    terminal = summary["chain"][-1]
    if RESOLVER_BINDINGS[resolver][0] != terminal:
        return UNKNOWN, None, f"chain_terminal_does_not_match_resolver:{terminal}"
    if hole_type == "point_3d":
        result = point_to_base(envelope["value"], extrinsics, q_lift)
    else:
        result = direction_to_base(envelope["value"], extrinsics)
    return result.status, result.value, result.reason


def project_base_values(
    record_dir: str | Path,
    *,
    extrinsics_path: str | Path,
) -> dict[str, Any]:
    """Publish ``robot_base`` values for one recorded PerceptionProgram run.

    Local computation only: it reads frozen artifacts, one calibration record and
    the acceptance record, and opens no socket.  Re-running is allowed because
    accepting one more identity must be able to produce one more candidate value
    without re-doing the live capture.
    """

    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") not in {_PROGRAMS_STATUS, _PROJECTED_STATUS}:
        raise ValueError("project-base requires manifest status PROGRAMS_RECORDED")
    _, observation = _frozen_observation(root)
    results = _validated_results(root, observation)
    if observation.frame != OPTICAL_FRAME:
        raise ValueError(f"projection expects a {OPTICAL_FRAME} observation")
    extrinsics = load_camera_extrinsics(extrinsics_path)
    if extrinsics.frame_from != observation.frame:
        raise ValueError("camera extrinsics do not start in the observation frame")

    graph = _read_json(_required_string(results.get("graph_ref"), "graph_ref"))
    if not isinstance(graph, Mapping):
        raise ValueError("program results graph_ref must contain an object")
    summaries = _program_summaries(results)
    accepted, acceptance_ref = _read_acceptances(root, results)
    q_lift, q_lift_source, q_lift_ref = _lift_reading(observation)

    holes: dict[str, Any] = {}
    for qualified, envelope in sorted(results["holes"].items()):
        stage_index, _, hole_name = qualified.partition(".")
        stage = _one_stage(graph, int(stage_index[1:]))
        matches = [item for item in stage["holes"] if item["name"] == hole_name]
        if len(matches) != 1:
            raise ValueError(f"graph has no unique hole for {qualified!r}")
        hole = matches[0]
        summary = summaries[envelope["program"]]
        status, value, reason = _projected_value(
            hole, envelope, summary, extrinsics, q_lift
        )
        holes[qualified] = {
            "value": None if value is None else list(value),
            "frame": BASE_FRAME,
            # 变换后这个数值的有效性由外参决定,所以 calibration_ref 指向外参记录;
            # 产生相机系数值的内参记录留在 source_calibration_ref 与证据里。
            "calibration_ref": extrinsics.ref,
            "object_id": envelope["object_id"],
            "identity_status": envelope["identity_status"],
            "identity_accepted": (envelope["program"], envelope["object_id"]) in accepted,
            "status": status,
            "reason": reason,
            "hole_type": hole["type"],
            "resolver": hole.get("resolver"),
            "program": envelope["program"],
            "source_frame": envelope["frame"],
            "source_value": envelope["value"],
            "source_calibration_ref": envelope["calibration_ref"],
            "evidence_refs": list(envelope["evidence_refs"]),
        }

    document = {
        "schema": BASE_VALUES_SCHEMA,
        "observation_id": observation.observation_id,
        "program_results_ref": str((root / "program_results.json").resolve()),
        "graph_ref": results["graph_ref"],
        "extrinsics_ref": extrinsics.ref,
        "identity_acceptance_ref": acceptance_ref,
        "source_frame": observation.frame,
        "source_calibration_ref": observation.calibration_ref,
        "frame": BASE_FRAME,
        "q_lift": q_lift,
        "q_lift_source": q_lift_source,
        "q_lift_ref": q_lift_ref,
        "holes": holes,
    }
    _write_json(root / _BASE_VALUES_FILE, document)
    _complete(root, manifest, status=_PROJECTED_STATUS, artifacts=_ARTIFACTS)
    return document


def base_frame_sources(record_dir: str | Path) -> BaseFrameSources:
    """Load one projected record as runtime observation and candidate providers.

    The observation keeps its original ``observation_id`` — this is the same
    capture, re-expressed — while its frame and ``calibration_ref`` become the
    base frame and the extrinsics the values now depend on, so typed-hole
    validation compares like with like.  Only objects with an accepted identity
    and at least one usable value are listed as observed.
    """

    root = Path(record_dir).resolve()
    document = _read_json(root / _BASE_VALUES_FILE)
    if not isinstance(document, Mapping) or document.get("schema") != BASE_VALUES_SCHEMA:
        raise ValueError("record directory has no projected base-frame values")
    record, observation = _frozen_observation(root)
    if document["observation_id"] != observation.observation_id:
        raise ValueError("projected values belong to another observation")

    evidence: dict[str, list[str]] = {}
    for envelope in document["holes"].values():
        if envelope["status"] != PASS or not envelope["identity_accepted"]:
            continue
        refs = evidence.setdefault(envelope["object_id"], [])
        refs.extend(envelope["evidence_refs"])
    base_record = {
        **dict(record),
        "frame": BASE_FRAME,
        "calibration_ref": document["extrinsics_ref"],
        "sensor_refs": list(dict.fromkeys([
            *record["sensor_refs"],
            document["extrinsics_ref"],
            document["program_results_ref"],
        ])),
        # 只声明"观测到"身份已被接受、且确有可用数值的对象;pose/axis 留空,因为
        # 这一步没有测量对象位姿,只是把已发布的几何换了个坐标系。
        "objects": [
            {
                "object_id": object_id,
                "frame": BASE_FRAME,
                "pose": None,
                "axis": None,
                "extent": None,
                "evidence_refs": list(dict.fromkeys(refs)),
            }
            for object_id, refs in sorted(evidence.items())
        ],
    }
    return BaseFrameSources(
        observation=observation_from_record(base_record),
        document=document,
    )
