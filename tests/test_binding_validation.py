"""Typed-hole values must fail closed before physical planning checks."""

from __future__ import annotations

from demo_graph_lab.execution.planning_runtime import filter_stage_candidates
from demo_graph_lab.perception import ObjectObservation, ObservationPacket, Proprioception
from demo_graph_lab.selection.binding import validate_candidate_bindings
from demo_graph_lab.selection.candidates import (
    CandidateBundle,
    CheckCertificate,
    CheckStatus,
    HardCheck,
)


def _observation(*, objects: bool = True) -> ObservationPacket:
    perceived = (
        ObjectObservation(
            object_id="tube",
            frame="robot_base",
            pose=(0.4, 0.0, 0.8, 0.0, 0.0, 0.0, 1.0),
            evidence_refs=("objects/tube.json",),
        ),
    ) if objects else ()
    return ObservationPacket(
        observation_id="obs-1",
        captured_at_s=1.0,
        frame="robot_base",
        calibration_ref="calibration/head.json",
        sensor_refs=("pointcloud/head.npy",),
        robot_state=Proprioception(
            joint_positions=(0.0,) * 7,
            end_effector_frame="robot_base",
            evidence_ref="proprioception/obs-1.json",
        ),
        objects=perceived,
    )


def _stage() -> dict:
    return {
        "index": 0,
        "name": "grasp",
        "stage_objects": {"manipulated": "tube", "target": None},
        "holes": [
            {"name": "grasp_pose", "type": "pose_se3", "frame": "robot_base"},
            {"name": "stop", "type": "runtime_condition", "purpose": "lower_stop"},
        ],
    }


def _candidate(value, *, observation_id: str = "obs-1") -> CandidateBundle:
    return CandidateBundle(
        candidate_id="grasp-0",
        observation_id=observation_id,
        hole_values={"grasp_pose": value},
        evidence_refs=("candidates/grasp-0.json",),
    )


def _pose(
    *,
    frame: str = "robot_base",
    calibration: str = "calibration/head.json",
    object_id: str = "tube",
):
    return {
        "value": [0.4, 0.0, 0.8, 0.0, 0.0, 0.0, 1.0],
        "frame": frame,
        "calibration_ref": calibration,
        "object_id": object_id,
    }


def _multi_object_observation() -> ObservationPacket:
    observation = _observation()
    return ObservationPacket(
        observation_id=observation.observation_id,
        captured_at_s=observation.captured_at_s,
        frame=observation.frame,
        calibration_ref=observation.calibration_ref,
        sensor_refs=observation.sensor_refs,
        robot_state=observation.robot_state,
        objects=(
            *observation.objects,
            ObjectObservation(
                object_id="rack",
                frame="robot_base",
                pose=(0.6, 0.0, 0.7, 0.0, 0.0, 0.0, 1.0),
                evidence_refs=("objects/rack.json",),
            ),
        ),
    )


def test_geometry_requires_exact_frame_calibration_object_and_shape() -> None:
    observation = _observation()
    valid = validate_candidate_bindings(
        _candidate(_pose()), _stage(), observation, required_holes=("grasp_pose",)
    )
    wrong_frame = validate_candidate_bindings(
        _candidate(_pose(frame="world")),
        _stage(),
        observation,
        required_holes=("grasp_pose",),
    )
    wrong_calibration = validate_candidate_bindings(
        _candidate(_pose(calibration="calibration/other.json")),
        _stage(),
        observation,
        required_holes=("grasp_pose",),
    )

    assert valid.status is CheckStatus.PASS
    assert wrong_frame.status is CheckStatus.FAIL
    assert wrong_calibration.status is CheckStatus.FAIL


def test_unobserved_stage_object_is_unknown() -> None:
    result = validate_candidate_bindings(
        _candidate(_pose()),
        _stage(),
        _observation(objects=False),
        required_holes=("grasp_pose",),
    )

    assert result.status is CheckStatus.UNKNOWN
    assert any("object_not_observed" in reason for reason in result.reasons)


def test_multi_object_stage_needs_structured_hole_anchor() -> None:
    stage = _stage()
    stage["stage_objects"]["target"] = "rack"

    result = validate_candidate_bindings(
        _candidate(_pose()),
        stage,
        _multi_object_observation(),
        required_holes=("grasp_pose",),
    )

    assert result.status is CheckStatus.UNKNOWN
    assert any("hole_object_anchor_ambiguous" in reason for reason in result.reasons)


def test_multi_object_stage_uses_anchor_object_for_candidate_binding() -> None:
    stage = _stage()
    stage["stage_objects"]["target"] = "rack"
    stage["holes"][0].update({
        "resolver": "grasp_candidate",
        "anchor": {"object_id": "tube", "part": "body"},
    })
    observation = _multi_object_observation()

    valid = validate_candidate_bindings(
        _candidate(_pose()), stage, observation, required_holes=("grasp_pose",))
    wrong_object = validate_candidate_bindings(
        _candidate(_pose(object_id="rack")),
        stage,
        observation,
        required_holes=("grasp_pose",),
    )

    assert valid.status is CheckStatus.PASS
    assert wrong_object.status is CheckStatus.FAIL
    assert any("object_anchor_mismatch:rack!=tube" in reason
               for reason in wrong_object.reasons)

    stage["holes"][0]["anchor"] = {}
    malformed = validate_candidate_bindings(
        _candidate(_pose()), stage, observation, required_holes=("grasp_pose",))
    assert malformed.status is CheckStatus.FAIL
    assert any("hole_object_anchor_invalid" in reason
               for reason in malformed.reasons)


def test_invalid_binding_skips_every_physical_checker() -> None:
    calls: list[str] = []

    def checker(name: str) -> HardCheck:
        def evaluate(candidate, observation):
            calls.append(name)
            return CheckCertificate(
                name,
                CheckStatus.PASS,
                "checked",
                (f"checks/{name}.json",),
            )

        return HardCheck(name, evaluate)

    checks = tuple(checker(name) for name in (
        "reachability", "collision_free", "gripper_width",
    ))
    result = filter_stage_candidates(
        _stage(),
        _observation(),
        (_candidate(_pose(), observation_id="stale-observation"),),
        checks,
        required_holes=("grasp_pose",),
    )

    assert calls == []
    assert result.accepted == ()
    certificates = {
        item.check: item for item in result.traces[0].certificates
    }
    assert certificates["typed_hole_values"].status is CheckStatus.FAIL
    assert certificates["reachability"].status is CheckStatus.UNKNOWN


def test_candidate_provider_cannot_bind_runtime_condition() -> None:
    candidate = CandidateBundle(
        candidate_id="condition-0",
        observation_id="obs-1",
        hole_values={
            "stop": {
                "value": [0.0],
                "frame": "robot_base",
                "calibration_ref": "calibration/head.json",
                "object_id": "tube",
            }
        },
        evidence_refs=("candidates/condition-0.json",),
    )

    result = validate_candidate_bindings(
        candidate,
        _stage(),
        _observation(),
        required_holes=("stop",),
    )

    assert result.status is CheckStatus.FAIL
    assert any("candidate_source_forbidden" in reason for reason in result.reasons)


def test_default_binding_requires_all_declared_geometry() -> None:
    candidate = CandidateBundle(
        candidate_id="empty",
        observation_id="obs-1",
        hole_values={},
        evidence_refs=("candidates/empty.json",),
    )

    result = validate_candidate_bindings(candidate, _stage(), _observation())

    assert result.status is CheckStatus.FAIL
    assert "grasp_pose:missing_required_value" in result.reasons


def test_physical_checker_cannot_shadow_typed_binding_certificate() -> None:
    reserved = HardCheck(
        "typed_hole_values",
        lambda candidate, observation: CheckCertificate(
            "typed_hole_values",
            CheckStatus.PASS,
            "forged",
            ("checks/forged.json",),
        ),
    )

    try:
        filter_stage_candidates(
            _stage(),
            _observation(),
            (_candidate(_pose()),),
            (reserved,),
        )
    except ValueError as error:
        assert "reserved" in str(error)
    else:
        raise AssertionError("reserved binding check name was accepted")
