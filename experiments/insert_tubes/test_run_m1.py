from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.insert_tubes import run_m1
from experiments.insert_tubes import runtime as runtime_module
from experiments.insert_tubes.perception import (
    derive_axis_from_xquat,
    find_axis_vector,
    parse_pick_response,
)
from experiments.insert_tubes.runtime import (
    M1Runtime,
    build_policy,
    grasp_only_graph,
)
from method.demo_graph import (
    ConstraintGraph,
    RestrictedCodeAgentCompiler,
)


class FakePipeline:
    def __init__(
        self,
        *,
        place_found: bool = False,
        place_error: str | None = None,
        nested_axis: bool = False,
        pick_failures: int = 0,
    ) -> None:
        self.pick_calls = 0
        self.pick_successes = 0
        self.place_found = place_found
        self.place_error = place_error
        self.nested_axis = nested_axis
        self.pick_failures = pick_failures
        self.actions: list[str] = []
        self.place_kwargs: list[dict] = []
        self.pick_kwargs: list[dict] = []
        self.qpos_by_arm = {
            0: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
            1: [1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7],
        }

    def reasoning(self, name, **kwargs):
        if name == "qwen_dof_xquat":
            self.pick_kwargs.append(dict(kwargs))
            self.pick_calls += 1
            if self.pick_calls <= self.pick_failures:
                return {
                    "xquats": [[None], [None]],
                    "grasp_angles": [[None], [None]],
                    "results": [{"run_id": "perception-miss"}],
                }
            self.pick_successes += 1
            z = 0.78 if self.pick_successes == 1 else 0.86
            payload = {
                "xquats": [
                    [[0.52, 0.09, z, 0.0, 0.0, 0.0, 1.0, 0.0]],
                    [None],
                ],
                "grasp_angles": [[60.0], [None]],
                "results": [{"run_id": "perception-run"}],
            }
            if self.nested_axis:
                payload["results"][0]["object_axis_world"] = [0.0, 1.0, 0.0]
            return payload
        if name == "qwen_dof_xquat_place":
            self.place_kwargs.append(dict(kwargs))
            if self.place_error:
                return {"xquats": [[None], [None]], "error": self.place_error}
            row = [0.68, 0.07, 0.82, 0.0, 0.0, 0.0, 1.0]
            return {"xquats": [[row], [None]] if self.place_found else [[None], [None]]}
        raise AssertionError(name)

    def ctrl(self, name, **kwargs):
        del kwargs
        self.actions.append(name)

    def info(self, name, **kwargs):
        assert name == "get_qpos"
        arm_id = int(kwargs.get("arm_id", 0))
        return list(self.qpos_by_arm[arm_id])


def _runtime(fake):
    return M1Runtime(
        fake,
        arm_id=0,
        pick_prompt="tube:dof",
        place_prompt="empty opening",
        settle_poll_s=0.0,
        settle_samples=1,
    )


def test_parse_nested_axis_and_derive_fallback():
    nested = {
        "xquats": [[[0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0]]],
        "grasp_angles": [[10.0]],
        "results": [{"run_id": "r1", "object_axis_world": [0.0, 1.0, 0.0]}],
    }
    parsed = parse_pick_response(nested, arm_id=0)
    assert parsed.axis == [0.0, 1.0, 0.0]
    assert parsed.axis_source and "object_axis_world" in parsed.axis_source

    axis, path = find_axis_vector({"results": [{"long_axis": [1, 0, 0]}]})
    assert axis == [1.0, 0.0, 0.0]
    assert path is not None

    derived = derive_axis_from_xquat([0, 0, 0, 0, 0, 0, 1])
    assert derived is not None
    assert abs(derived[2]) < 1e-9


def test_pick_and_place_pass_live_qpos_for_selector_seed():
    fake = FakePipeline(place_found=True)
    result = _runtime(fake).probe()
    assert result["grasp_candidate_found"] is True
    assert fake.pick_kwargs
    assert fake.pick_kwargs[0]["left_qpos"] == fake.qpos_by_arm[0]
    assert fake.pick_kwargs[0]["right_qpos"] == fake.qpos_by_arm[1]
    assert fake.place_kwargs
    assert fake.place_kwargs[0]["left_qpos"] == fake.qpos_by_arm[0]
    assert fake.place_kwargs[0]["right_qpos"] == fake.qpos_by_arm[1]


def test_probe_derives_tube_axis_and_reports_holder_error():
    fake = FakePipeline(place_error="point cloud insufficient")
    result = _runtime(fake).probe()
    assert result["grasp_candidate_found"] is True
    assert result["tube_axis_found"] is True
    assert result["tube_axis_source"] == "derived:grasp_xquat_horizontal"
    assert result["holder_pose_found"] is False
    assert result["holder_pose_error"] == "point cloud insufficient"
    assert result["perceptual_holes"] == ["holder_pose"]
    assert fake.place_kwargs  # 至少尝试过 place


def test_probe_uses_nested_response_axis():
    result = _runtime(FakePipeline(nested_axis=True, place_found=True)).probe()
    assert result["tube_axis_found"] is True
    assert "object_axis_world" in (result["tube_axis_source"] or "")
    assert result["holder_pose_found"] is True
    assert result["perceptual_holes"] == []


def test_grasp_cli_allows_holder_hole_and_marks_pick_sources(monkeypatch, capsys):
    fake = FakePipeline(place_found=False)
    monkeypatch.setattr(run_m1, "PipelineClient", lambda _url: fake)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)

    exit_code = run_m1.main(["--mode", "grasp"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["preflight"]["passed"] is True
    assert payload["preflight"]["perceptual_holes"] == ["holder_pose"]
    assert payload["preflight"]["gating_holes"] == []
    assert len(payload["preflight"]["attempts"]) == 1
    assert payload["preflight"]["attempts"][0]["attempt"] == 1
    assert payload["preflight"]["attempts"][0]["perceptual_holes"] == ["holder_pose"]
    assert payload["preflight"]["attempts"][0]["gating_holes"] == []
    assert "candidate_chain" in payload["preflight"]["attempts"][0]
    assert payload["preflight"]["attempts"][0]["candidate_chain"]["blocks_execution"] is False
    assert fake.pick_calls == 2  # preflight + fresh post-lift verification
    observations = payload["stage_evidence"]["observations"]
    assert [item["pick_source"] for item in observations] == [
        "preflight_reuse",
        "fresh",
    ]
    assert payload["stage_evidence"]["attachment"]["pick_source"] == "fresh"
    assert fake.actions == [
        "set_gripper",
        "xquat_move",
        "set_gripper",
        "delta_move",
    ]


def test_grasp_cli_retries_preflight_with_fresh_pick_until_third_attempt(
    monkeypatch, capsys
):
    fake = FakePipeline(place_found=False, pick_failures=2)
    monkeypatch.setattr(run_m1, "PipelineClient", lambda _url: fake)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)

    exit_code = run_m1.main(["--mode", "grasp"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["preflight"]["passed"] is True
    assert [
        attempt["perceptual_holes"]
        for attempt in payload["preflight"]["attempts"]
    ] == [
        ["grasp_pose", "holder_pose"],
        ["grasp_pose", "holder_pose"],
        ["holder_pose"],
    ]
    assert fake.pick_calls == 4  # three preflight attempts + post-lift fresh pick
    assert fake.actions == [
        "set_gripper",
        "xquat_move",
        "set_gripper",
        "delta_move",
    ]


def test_grasp_cli_preflight_failure_after_three_attempts_sends_no_control(
    monkeypatch, capsys
):
    fake = FakePipeline(place_found=True, pick_failures=3)
    monkeypatch.setattr(run_m1, "PipelineClient", lambda _url: fake)

    exit_code = run_m1.main(["--mode", "grasp"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["reason"] == "grasp M1 preflight failed closed"
    assert payload["preflight"]["passed"] is False
    assert payload["preflight"]["gating_holes"] == ["grasp_pose"]
    assert len(payload["preflight"]["attempts"]) == 3
    assert all(
        attempt["perceptual_holes"] == ["grasp_pose", "holder_pose"]
        for attempt in payload["preflight"]["attempts"]
    )
    assert fake.pick_calls == 3
    assert fake.actions == []


def test_full_cli_keeps_holder_pose_as_gate_for_all_three_attempts(
    monkeypatch, capsys
):
    fake = FakePipeline(place_found=False)
    monkeypatch.setattr(run_m1, "PipelineClient", lambda _url: fake)

    exit_code = run_m1.main(["--mode", "full"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["reason"] == "full M1 preflight failed closed"
    assert payload["preflight"]["passed"] is False
    assert payload["preflight"]["gating_holes"] == ["holder_pose"]
    assert len(payload["preflight"]["attempts"]) == 3
    assert fake.pick_calls == 3
    assert fake.actions == []


def test_policy_exception_emits_audit_json_then_reraises(monkeypatch, capsys):
    fake = FakePipeline(place_found=False)
    monkeypatch.setattr(run_m1, "PipelineClient", lambda _url: fake)

    def exploding_policy(runtime, graph, *, compiled):
        del graph, compiled

        class Policy:
            def run(self):
                runtime.observe(
                    "pick",
                    action="pick",
                    goal="tube_attached",
                )
                raise RuntimeError("policy boom")

        return Policy(), SimpleNamespace(audit_records=[])

    monkeypatch.setattr(run_m1, "build_policy", exploding_policy)

    with pytest.raises(RuntimeError, match="policy boom"):
        run_m1.main(["--mode", "grasp"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["succeeded"] is False
    assert payload["exception"] == {
        "type": "RuntimeError",
        "message": "policy boom",
    }
    assert payload["stage_evidence"]["observations"][0]["pick_source"] == (
        "preflight_reuse"
    )
    assert payload["method_calls"] == []
    assert fake.actions == []


def test_cli_converts_reraised_exception_to_exit_code_three(monkeypatch, capsys):
    fake = FakePipeline(place_found=False)
    monkeypatch.setattr(run_m1, "PipelineClient", lambda _url: fake)

    class Policy:
        def run(self):
            raise ValueError("structured failure")

    monkeypatch.setattr(
        run_m1,
        "build_policy",
        lambda runtime, graph, *, compiled: (
            Policy(),
            SimpleNamespace(audit_records=[]),
        ),
    )

    assert run_m1.cli(["--mode", "grasp"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["exception"]["type"] == "ValueError"
    assert payload["exception"]["message"] == "structured failure"
    assert payload["stage_evidence"]["observations"] == []
    assert fake.actions == []


def test_grasp_succeeds_then_full_graph_fails_closed_without_reorient_controller():
    graph = ConstraintGraph.load_json(Path(__file__).with_name("m1_graph.json"))
    fake = FakePipeline()
    runtime = _runtime(fake)
    policy, _broker = build_policy(runtime, graph)
    result = policy.run()
    assert result.succeeded is False
    assert result.nodes[0].node_id == "pick"
    assert result.nodes[0].succeeded is True
    assert result.nodes[1].node_id == "reorient"
    # 轴已由 xquat 推导出，但当前无可信 reorient 控制器，且目标未竖直
    assert "perceptual hole unresolved" in result.nodes[1].reason
    assert fake.actions == [
        "set_gripper",
        "xquat_move",
        "set_gripper",
        "delta_move",
    ]
    attachment = runtime.stage_evidence()["attachment"]
    assert attachment is not None
    assert attachment["minimum_z_rise_m"] == 0.04
    assert attachment["z_rise_m"] >= 0.04
    assert attachment["gate_passed"] is True


def test_compiled_extracted_node_names_bind_by_action_and_goal():
    base = ConstraintGraph.load_json(Path(__file__).with_name("m1_graph.json"))
    names = {node.node_id: f"{node.node_id}_1" for node in base.nodes}
    goals = {
        "pick": "tube_1_attached",
        "reorient": "tube_1_insertion_compatible",
        "align": "tube_1_aligned_to_empty_slot",
        "insert": "tube_1_inserted",
        "verify": "tube_1_inserted_and_upright",
    }
    graph = ConstraintGraph(
        graph_id="extracted_cycle_1",
        entry_node="pick_1",
        nodes=tuple(
            replace(
                node,
                node_id=names[node.node_id],
                goal=goals[node.action],
                next_node=(
                    names[node.next_node]
                    if node.next_node is not None
                    else None
                ),
            )
            for node in base.nodes
        ),
        provenance=base.provenance,
    )
    compiled = RestrictedCodeAgentCompiler(
        tuple(node.controller_ref for node in graph.nodes)
    ).compile(graph)
    fake = FakePipeline()
    policy, _broker = build_policy(
        _runtime(fake),
        graph,
        compiled=compiled,
    )

    result = policy.run()

    assert result.succeeded is False
    assert result.nodes[0].node_id == "pick_1"
    assert result.nodes[0].succeeded is True
    assert result.nodes[1].node_id == "reorient_1"
    assert "perceptual hole unresolved" in result.nodes[1].reason


def test_extracted_grasp_reobserves_within_graph_budget_after_ik_miss():
    base = ConstraintGraph.load_json(Path(__file__).with_name("m1_graph.json"))
    pick = replace(
        base.node("pick"),
        node_id="pick_1",
        goal="tube_1_attached",
        max_attempts=2,
        next_node=None,
    )
    graph = ConstraintGraph(
        graph_id="extracted_grasp_cycle_1",
        entry_node="pick_1",
        nodes=(pick,),
        provenance=base.provenance,
    )
    graph = grasp_only_graph(graph)
    compiled = RestrictedCodeAgentCompiler(("trusted.pick",)).compile(graph)
    fake = FakePipeline(pick_failures=1)
    policy, _broker = build_policy(
        _runtime(fake),
        graph,
        compiled=compiled,
    )

    result = policy.run()

    assert result.succeeded is True
    assert result.nodes[0].attempts == 2
    assert result.nodes[0].failure_constraint_id is None
    assert fake.pick_calls == 3
    assert fake.actions == [
        "set_gripper",
        "xquat_move",
        "set_gripper",
        "delta_move",
    ]


def test_candidate_chain_is_record_only(tmp_path):
    from experiments.insert_tubes.candidate_chain import parse_candidate_chain_from_log

    log = tmp_path / "pipeline.log"
    log.write_text(
        "19:19:44 pick_budget=10.00s, graspgen_timeout=8.00s\n"
        "19:19:55 unified GraspGen fallback failed: worker TCP connect timeout after 8.0s\n"
        "19:19:55 generated=16 (fit=16, graspgen=0/error) -> IK=32\n"
        "19:19:55 selector selected=1\n"
        "19:19:55 unified_graspgen=8.165s\n",
        encoding="utf-8",
    )
    chain = parse_candidate_chain_from_log(log)
    assert chain["blocks_execution"] is False
    assert chain["execution_precondition"] is False
    assert chain["degraded_fit_only"] is True
    assert chain["graspgen_candidates"] == 0
    assert chain["fit_candidates"] == 16
    assert chain["selector_candidates"] == 1
