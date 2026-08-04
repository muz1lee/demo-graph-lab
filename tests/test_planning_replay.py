"""Offline fixed-replay tests for planning-only candidate selection."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from demo_graph_lab import cli
from demo_graph_lab.execution import planning_replay


FIXTURES = Path(__file__).parent / "fixtures" / "planning"
GRAPH_PATH = FIXTURES / "grasp_graph.json"
REPLAY_PATH = FIXTURES / "grasp_replay.json"


def _graph() -> dict:
    return json.loads(GRAPH_PATH.read_text("utf-8"))


def _raw_replay() -> dict:
    return json.loads(REPLAY_PATH.read_text("utf-8"))


def test_load_replay_builds_typed_read_only_inputs() -> None:
    case = planning_replay.load_replay(REPLAY_PATH, _graph())

    assert case.source_kind == "synthetic_contract_fixture"
    assert case.observation.frame == "robot_base"
    assert not hasattr(case, "required_holes")
    assert [candidate.observation_id for candidate in case.candidates] == [
        "obs-synthetic-001",
    ] * 4
    assert [
        certificate.check
        for certificate in case.candidate_certificates[0].certificates
    ] == [
        "reachability", "collision_free", "gripper_width",
    ]
    assert not hasattr(case, "hard_checks")


def test_compare_filters_once_and_shares_the_accepted_set(monkeypatch) -> None:
    graph = _graph()
    case = planning_replay.load_replay(REPLAY_PATH, graph)
    original = planning_replay.filter_stage_candidates
    calls = []

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(planning_replay, "filter_stage_candidates", counted)
    result = planning_replay.compare_replay(graph, case)

    assert len(calls) == 1
    assert result["shared"]["accepted_candidate_ids"] == ["c00", "c01", "c02"]
    assert result["no_demo"]["name"] == "candidate_id_baseline"
    assert result["no_demo"]["ranking"] == ["c00", "c01", "c02"]
    assert result["demo"]["ranking"] == ["c01", "c02", "c00"]
    assert result["no_demo"]["selected_candidate_id"] == "c00"
    assert result["demo"]["selected_candidate_id"] == "c01"
    assert result["comparison"]["top1_changed"] is True
    assert result["status"] == "OFFLINE_SELECTION_REPLAY"
    assert result["backend_model_enabled"] is False
    assert result["execution_enabled"] is False
    assert result["hard_checks_source"] == "synthetic_fixture_certificates"
    rejected = {
        item["candidate"]["candidate_id"]: item
        for item in result["shared"]["candidate_checks"]
    }
    assert rejected["c03"]["accepted"] is False
    assert any(certificate["status"] == "FAIL"
               for certificate in rejected["c03"]["certificates"])
    json.dumps(result, allow_nan=False)


def test_ranking_is_independent_of_fixture_candidate_order() -> None:
    graph = _graph()
    case = planning_replay.load_replay(REPLAY_PATH, graph)
    forward = planning_replay.compare_replay(graph, case)
    backward = planning_replay.compare_replay(
        graph, replace(case, candidates=tuple(reversed(case.candidates))))

    assert backward["no_demo"]["ranking"] == forward["no_demo"]["ranking"]
    assert backward["demo"]["ranking"] == forward["demo"]["ranking"]


def test_compare_rejects_source_kind_bypass() -> None:
    graph = _graph()
    case = planning_replay.load_replay(REPLAY_PATH, graph)

    with pytest.raises(ValueError, match="synthetic_contract_fixture"):
        planning_replay.compare_replay(
            graph,
            replace(case, source_kind="recorded_real"),
        )


def test_compare_rejects_callable_injection_as_certificate_data() -> None:
    graph = _graph()
    case = planning_replay.load_replay(REPLAY_PATH, graph)
    injected = replace(
        case,
        candidate_certificates=(
            planning_replay.CandidateCertificates(
                candidate_id=case.candidates[0].candidate_id,
                certificates=(lambda: None,),
            ),
        ),
    )

    with pytest.raises(TypeError, match="CheckCertificate"):
        planning_replay.compare_replay(graph, injected)


def test_compare_recomputes_required_holes_from_graph() -> None:
    graph = _graph()
    case = planning_replay.load_replay(REPLAY_PATH, graph)
    empty = replace(case.candidates[0], hole_values={})
    bypass = replace(
        case,
        candidates=(empty,),
        candidate_certificates=(case.candidate_certificates[0],),
    )

    result = planning_replay.compare_replay(graph, bypass)

    assert result["shared"]["accepted_candidate_ids"] == []
    typed = result["shared"]["candidate_checks"][0]["certificates"][0]
    assert typed["check"] == "typed_hole_values"
    assert typed["status"] != "PASS"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda replay: replay["candidates"][0]["hard_checks"].pop(),
         "hard_checks must be exactly"),
        (lambda replay: replay["candidates"][0].update(observation_id="wrong"),
         "observation_id must match"),
        (lambda replay: replay["candidates"][0]["hole_values"].update(
            unknown={
                "value": [0.0], "frame": "robot_base",
                "calibration_ref": "calibration/synthetic.json", "object_id": "object",
            }), "unknown_candidate_hole"),
        (lambda replay: replay["candidates"][0]["hole_values"]["grasp_pose"].update(
            frame="world"), "frame_mismatch"),
        (lambda replay: replay.update(source_kind="claimed_live"), "source_kind"),
        (lambda replay: replay["candidates"][0]["hard_checks"][0].update(
            evidence_refs="checks/not-a-list.json"), "evidence_refs"),
    ],
)
def test_loader_rejects_incomplete_or_mixed_replay(tmp_path, mutate, message) -> None:
    replay = _raw_replay()
    mutate(replay)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(replay), encoding="utf-8")

    with pytest.raises((TypeError, ValueError), match=message):
        planning_replay.load_replay(path, _graph())


def test_all_rejected_is_a_valid_offline_comparison(tmp_path) -> None:
    replay = _raw_replay()
    for candidate in replay["candidates"]:
        for certificate in candidate["hard_checks"]:
            if certificate["check"] == "collision_free":
                certificate["status"] = "FAIL"
                certificate["reason"] = "synthetic_collision"
    path = tmp_path / "none.json"
    path.write_text(json.dumps(replay), encoding="utf-8")
    graph = _graph()

    result = planning_replay.compare_replay(
        graph, planning_replay.load_replay(path, graph))

    assert result["status"] == "NO_FEASIBLE_CANDIDATE"
    assert result["shared"]["accepted_candidate_ids"] == []
    assert result["no_demo"]["selected_candidate_id"] is None
    assert result["demo"]["selected_candidate_id"] is None


def test_cli_writes_one_offline_comparison_without_loading_backend_env(
    tmp_path, monkeypatch,
) -> None:
    output = tmp_path / "comparison.json"

    def unexpected_env_load():
        raise AssertionError("planning replay must not load backend environment")

    monkeypatch.setattr(cli.artifacts, "load_env", unexpected_env_load)
    assert cli.main([
        "planning-replay",
        "--graph", str(GRAPH_PATH),
        "--replay", str(REPLAY_PATH),
        "--output", str(output),
    ]) == 0

    assert [path.name for path in tmp_path.iterdir()] == ["comparison.json"]
    record = json.loads(output.read_text("utf-8"))
    assert record["status"] == "OFFLINE_SELECTION_REPLAY"
    assert record["no_demo"]["name"] == "candidate_id_baseline"


def test_replay_source_has_no_online_dependencies() -> None:
    source = (Path(planning_replay.__file__).read_text("utf-8"))
    forbidden = (
        "common.llm", "openai", "oracle_runtime", "PipelineClient", "robot_api",
        "run_policy", '"/state"', "'/state'",
    )
    for token in forbidden:
        assert token not in source
