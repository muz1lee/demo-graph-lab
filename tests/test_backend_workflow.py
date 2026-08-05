"""Offline backend contracts; all tests run without network or video dependencies."""

import json
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from demo_graph_lab.common import artifacts, llm
from demo_graph_lab.demo.registry import validate_registry
from demo_graph_lab.demo.stages import uniform_sample, validate_proposal
from demo_graph_lab.graph.extract import merge_samples
from demo_graph_lab.graph.validate import (
    validate_final_graph,
    validate_live_hole_contract,
    validate_run_dir,
    validate_stage_manifest,
    validate_stage_sample,
)


def test_uniform_sample_covers_the_whole_sequence():
    sampled = uniform_sample(list(range(30)), 4)
    assert len(sampled) == 4
    assert sampled[0] == 0
    assert sampled[-1] == 29
    assert uniform_sample(list(range(3)), 8) == [0, 1, 2]


def test_keyframe_count_must_be_positive():
    from demo_graph_lab.demo import keyframes

    with pytest.raises(ValueError, match="per_stage must be positive"):
        keyframes.run("unused", per_stage=0)


def test_stage_split_schema_is_strict():
    good = [{
        "stage": "grasp",
        "start_frame": 2,
        "end_frame": 10,
        "boundary_event": "gripper closes",
        "confidence": 0.8,
    }]
    assert validate_proposal(good, total_frames=20) == []

    bad = [{
        "stage": "invented_action",
        "start_frame": 10,
        "end_frame": 20,
        "boundary_event": "",
        "confidence": 1.2,
    }]
    errors = validate_proposal(bad, total_frames=20)
    assert any("closed vocabulary" in error for error in errors)
    assert any("outside the video" in error for error in errors)
    assert any("boundary_event" in error for error in errors)
    assert any("confidence" in error for error in errors)
    assert any("unknown fields" in error for error in validate_proposal(
        [dict(good[0], rationale="extra")], total_frames=20))


def test_stage_split_rejects_overlap_but_allows_shared_boundary():
    first = {
        "stage": "approach", "start_frame": 0, "end_frame": 10,
        "boundary_event": "touch", "confidence": 0.8,
    }
    shared_boundary = {
        "stage": "grasp", "start_frame": 10, "end_frame": 15,
        "boundary_event": "close", "confidence": 0.8,
    }
    assert validate_proposal([first, shared_boundary], total_frames=20) == []

    overlapping = dict(shared_boundary, start_frame=9)
    errors = validate_proposal([first, overlapping], total_frames=20)
    assert any("overlaps the previous stage" in error for error in errors)


def test_stage_manifest_rejects_duplicate_overlap_and_video_overrun():
    stages = [
        {"index": 0, "name": "approach", "label": "first",
         "start_sec": 0.0, "end_sec": 1.1},
        {"index": 0, "name": "grasp", "label": "second",
         "start_sec": 1.0, "end_sec": 2.1},
    ]
    errors = validate_stage_manifest(stages, fps=10.0, total_frames=20)
    assert any("duplicated" in error for error in errors)
    assert any("overlaps or precedes" in error for error in errors)
    assert any("exceeds video duration" in error for error in errors)


def test_object_registry_schema_rejects_duplicates_and_unknown_fields():
    good = [{
        "id": "tube_left",
        "category": "tube",
        "distinguishers": "left at start",
        "trace_aliases": ["tube0"],
        "first_seen_frame": 0,
    }]
    assert validate_registry(good, total_frames=100) == []

    bad = [dict(good[0], extra="not allowed"), dict(good[0])]
    errors = validate_registry(bad, total_frames=100)
    assert any("unknown fields" in error for error in errors)
    assert any("duplicated" in error for error in errors)


def _valid_stage_sample():
    return {
        "stage": "grasp",
        "stage_objects": {"manipulated": "tube0", "target": "rack"},
        "constraints": [{
            "name": "region_grasp",
            "args": {"obj": "tube0", "region": "upper_body"},
            "holds": "throughout",
            "confidence": 0.8,
            "evidence_frames": [3],
        }],
        "acceptance": [{
            "name": "above",
            "args": {"obj_a": "tube0", "obj_b": "rack"},
            "holds": "at_end",
            "confidence": 0.7,
            "evidence_frames": [5],
        }],
        "holes": [{
            "name": "tube_grasp_pose",
            "type": "pose_se3",
            "solver_hint": "pose from tube segmentation",
            "frame": "camera",
        }],
    }


def test_constraint_sample_schema_checks_evidence_objects_and_holes():
    stage = {"index": 0, "name": "grasp"}
    sample = _valid_stage_sample()
    assert validate_stage_sample(sample, stage, {"tube0", "rack"}) == ([], [])

    sample["constraints"][0]["args"]["obj"] = "invented_tube"
    sample["constraints"][0]["confidence"] = True
    sample["constraints"][0]["evidence_frames"] = []
    sample["holes"][0].pop("frame")
    errors, dropped = validate_stage_sample(sample, stage, {"tube0", "rack"})
    assert any("object registry" in error for error in errors)
    assert any("confidence" in error for error in errors)
    assert any("must not be empty" in error for error in errors)
    # 洞级错误不再进样本错误表,只让这一个洞出局。
    assert not any("missing fields ['frame']" in error for error in errors)
    assert [drop["name"] for drop in dropped] == ["tube_grasp_pose"]
    assert any("missing fields ['frame']" in error for error in dropped[0]["errors"])


def test_bad_hole_is_dropped_without_voiding_the_rest_of_the_sample():
    stage = {"index": 0, "name": "grasp"}
    sample = _valid_stage_sample()
    sample["holes"] = [
        # 昨夜真实错误型:scalar 洞缺 frame、anchor.part 不是零件名。
        {"name": "insert_depth", "type": "scalar", "solver_hint": "depth from rim"},
        dict(sample["holes"][0]),
        {
            "name": "rack_hole_center", "type": "point_3d", "frame": "camera",
            "solver_hint": "center from rack segmentation",
            "resolver": "part_center",
            "anchor": {"object_id": "rack", "part": "whole_object"},
        },
    ]
    errors, dropped = validate_stage_sample(sample, stage, {"tube0", "rack"})

    assert errors == []
    assert [drop["index"] for drop in dropped] == [0, 2]
    assert [drop["name"] for drop in dropped] == ["insert_depth", "rack_hole_center"]
    assert any("missing fields ['frame']" in error for error in dropped[0]["errors"])
    assert dropped[1]["errors"]
    # 好洞、好约束和验收条件原样留在样本里。
    assert sample["holes"][1]["name"] == "tube_grasp_pose"
    assert len(sample["constraints"]) == 1 and len(sample["acceptance"]) == 1


def test_duplicate_hole_names_drop_both_holes_but_keep_the_sample():
    stage = {"index": 0, "name": "grasp"}
    sample = _valid_stage_sample()
    twin = dict(sample["holes"][0], type="point_3d")
    sample["holes"] = [sample["holes"][0], twin]

    errors, dropped = validate_stage_sample(sample, stage, {"tube0", "rack"})

    assert errors == []
    assert [drop["index"] for drop in dropped] == [0, 1]
    assert all("duplicated" in drop["errors"][0] for drop in dropped)


def test_constraint_errors_still_void_the_whole_sample():
    """约束级语义不变:一条坏约束仍然否决整个样本,P/R 口径不受洞级改动影响。"""
    stage = {"index": 0, "name": "grasp"}
    sample = _valid_stage_sample()
    sample["constraints"][0]["args"]["region"] = "invented_region"

    errors, dropped = validate_stage_sample(sample, stage, {"tube0", "rack"})

    assert dropped == []
    assert any("非法 region" in error for error in errors)


def test_constraint_sample_rejects_evidence_past_the_video():
    sample = _valid_stage_sample()
    sample["constraints"][0]["evidence_frames"] = [20]
    errors, _ = validate_stage_sample(
        sample, {"index": 0, "name": "grasp"}, {"tube0", "rack"},
        total_frames=20,
    )
    assert any("smaller than total_frames" in error for error in errors)


def test_constraint_sample_rejects_evidence_not_shown_to_backend():
    sample = _valid_stage_sample()
    errors, _ = validate_stage_sample(
        sample, {"index": 0, "name": "grasp"}, {"tube0", "rack"},
        total_frames=20, allowed_evidence_frames={3},
    )
    assert any("only displayed keyframes" in error for error in errors)


def test_merge_denominator_counts_failed_attempts():
    constraint = {
        "name": "axis_vertical",
        "args": {"axis": "tube0.long_axis"},
        "holds": "at_end",
        "confidence": 0.9,
        "evidence_frames": [4],
    }
    sample = {"constraints": [constraint], "acceptance": [], "holes": []}
    assert merge_samples([sample, sample], total_samples=5)["constraints"] == []
    merged = merge_samples([sample, sample, sample], total_samples=5)
    assert merged["constraints"][0]["votes"] == "3/5"

    duplicated = {"constraints": [constraint, constraint], "acceptance": [], "holes": []}
    assert merge_samples([duplicated], total_samples=3)["constraints"] == []
    assert merge_samples([sample, sample], total_samples=4)["constraints"] == []


def test_merge_votes_separately_by_holds_and_full_hole_contract():
    constraint = {
        "name": "axis_vertical", "args": {"axis": "tube0.long_axis"},
        "holds": "at_end", "confidence": 0.9, "evidence_frames": [4],
    }
    throughout = dict(constraint, holds="throughout")
    samples = [
        {"constraints": [constraint], "acceptance": [], "holes": []},
        {"constraints": [constraint], "acceptance": [], "holes": []},
        {"constraints": [throughout], "acceptance": [], "holes": []},
    ]
    merged = merge_samples(samples)
    assert merged["constraints"][0]["holds"] == "at_end"
    assert merged["constraints"][0]["votes"] == "2/3"

    base_hole = {
        "name": "tube_axis", "type": "axis_3d", "frame": "camera",
        "purpose": None, "solver_hint": "tube segmentation",
        "resolver": "principal_axis",
        "anchor": {"object_id": "tube0", "part": "long_axis"},
    }
    for field, value in (
        ("frame", "world"),
        ("purpose", "lower_stop"),
        ("resolver", "part_axis"),
        ("anchor", {"object_id": "rack", "part": "hole_axis"}),
        ("solver_hint", "depth segmentation"),
    ):
        changed = dict(base_hole, **{field: value})
        hole_samples = [
            {"constraints": [], "acceptance": [], "holes": [base_hole]},
            {"constraints": [], "acceptance": [], "holes": [base_hole]},
            {"constraints": [], "acceptance": [], "holes": [changed]},
        ]
        hole = merge_samples(hole_samples)["holes"][0]
        assert hole["votes"] == "2/3"
        assert hole[field] == base_hole[field]


def test_merge_result_order_is_stable_across_sample_order():
    def item(name):
        args = ({"axis": "tube0.long_axis"} if name == "axis_vertical"
                else {"obj_a": "tube0", "obj_b": "rack"})
        return {
            "name": name, "args": args, "holds": "at_end",
            "confidence": 0.8, "evidence_frames": [3],
        }

    constraint_a = item("axis_vertical")
    constraint_b = item("above")
    hole_a = {"name": "z_axis", "type": "axis_3d", "frame": "camera",
              "solver_hint": "segmentation"}
    hole_b = {"name": "a_point", "type": "point_3d", "frame": "camera",
              "solver_hint": "segmentation"}
    forward = {"constraints": [constraint_a, constraint_b], "acceptance": [],
               "holes": [hole_a, hole_b]}
    reverse = {"constraints": [constraint_b, constraint_a], "acceptance": [],
               "holes": [hole_b, hole_a]}
    assert merge_samples([forward]) == merge_samples([reverse])


def _valid_final_stage(index=0, name="grasp", start_sec=0.0, end_sec=1.0,
                       evidence_frame=3):
    return {
        "index": index,
        "name": name,
        "label": f"{name} stage",
        "start_sec": start_sec,
        "end_sec": end_sec,
        "stage_objects": {"manipulated": "tube0", "target": "rack"},
        "constraints": [{
            "name": "region_grasp",
            "args": {"obj": "tube0", "region": "upper_body"},
            "holds": "throughout",
            "confidence": 0.8,
            "evidence_frames": [evidence_frame],
            "provenance": "demo_video",
        }],
        "acceptance": [{
            "name": "above",
            "args": {"obj_a": "tube0", "obj_b": "rack"},
            "holds": "at_end",
            "confidence": 0.8,
            "evidence_frames": [evidence_frame],
            "provenance": "demo_video",
        }],
        "holes": [{
            "name": "tube_grasp_pose", "type": "pose_se3",
            "solver_hint": "tube segmentation", "frame": "camera",
        }],
        "k_valid": 1,
    }


def test_live_hole_contract_is_strict_without_breaking_legacy_graphs():
    stage = _valid_final_stage()
    manifest = [{key: stage[key] for key in
                 ("index", "name", "label", "start_sec", "end_sec")}]
    graph = {"k": 1, "stages": [stage]}

    errors, _, _ = validate_final_graph(
        graph, manifest, {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert errors == []
    live_errors = validate_live_hole_contract(graph, {"tube0", "rack"})
    assert any("missing live field 'resolver'" in error for error in live_errors)
    assert any("canonical execution frame" in error for error in live_errors)

    hole = stage["holes"][0]
    hole.update({
        "frame": "robot_base",
        "resolver": "grasp_candidate",
        "anchor": {
            "object_id": "tube0",
            "part": "whole",
        },
    })
    assert validate_live_hole_contract(graph, {"tube0", "rack"}) == []

    hole["anchor"] = {
        "object_id": "tube0",
        "part": "body",
        "selection": "upper_body",
    }
    live_errors = validate_live_hole_contract(graph, {"tube0", "rack"})
    assert any("whole-object anchor" in error for error in live_errors)


def test_live_hole_contract_checks_resolver_anchor_and_execution_frame():
    stage = _valid_final_stage()
    hole = stage["holes"][0]
    hole.update({
        "frame": "world",
        "resolver": "principal_axis",
        "anchor": {
            "object_id": "ghost",
            "part": "Upper Body",
            "instance": "",
        },
    })

    errors = validate_live_hole_contract(
        {"k": 1, "stages": [stage]}, {"tube0", "rack"})

    assert any("incompatible with type 'pose_se3'" in error for error in errors)
    assert any("not a registry id" in error for error in errors)
    assert any("not a stage object" in error for error in errors)
    assert any("anchor.part must be snake_case" in error for error in errors)
    assert any("anchor.instance must be a non-empty string" in error
               for error in errors)
    assert any("canonical execution frame" in error for error in errors)

    hole["resolver"] = {"kind": "principal_axis"}
    assert any("closed vocabulary" in error for error in
               validate_live_hole_contract(
                   {"k": 1, "stages": [stage]}, {"tube0", "rack"}))


def test_live_hole_contract_rejects_ambiguous_part_anchor_pairs():
    stage = _valid_final_stage()
    center = {
        "name": "rack_hole_center",
        "type": "point_3d",
        "solver_hint": "rack opening center",
        "frame": "robot_base",
        "resolver": "part_center",
        "anchor": {"object_id": "rack", "part": "hole", "instance": "left"},
    }
    axis = {
        "name": "rack_hole_axis",
        "type": "axis_3d",
        "solver_hint": "rack opening axis",
        "frame": "robot_base",
        "resolver": "part_axis",
        "anchor": {"object_id": "rack", "part": "hole", "instance": "right"},
    }
    stage["holes"] = [center, axis]
    graph = {"k": 1, "stages": [stage]}

    errors = validate_live_hole_contract(graph, {"tube0", "rack"})
    assert any("must use the same anchors" in error for error in errors)

    axis["anchor"] = {
        "object_id": "rack",
        "part": "hole",
        "instance": "left",
        "selection": "left_opening",
    }
    errors = validate_live_hole_contract(graph, {"tube0", "rack"})
    assert any("cannot contain both instance and selection" in error
               for error in errors)
    assert any("exactly one of instance or selection" in error
               for error in errors)


@pytest.mark.parametrize(("resolver", "hole_type", "anchor", "message"), [
    (
        "principal_axis",
        "axis_3d",
        {"object_id": "tube0", "part": "body", "selection": "upper_body"},
        "principal_axis must use a whole-object anchor",
    ),
    (
        "part_center",
        "point_3d",
        {"object_id": "rack", "part": "whole"},
        "part_center must use a hole anchor",
    ),
    (
        "motion_derived",
        "pose_se3",
        {"object_id": "rack", "part": "hole"},
        "hole anchor requires exactly one",
    ),
    (
        "motion_derived",
        "pose_se3",
        {"object_id": "tube0", "part": "whole", "instance": "left"},
        "whole-object anchor cannot contain",
    ),
])
def test_live_hole_contract_enforces_resolver_anchor_semantics(
    resolver, hole_type, anchor, message
):
    stage = _valid_final_stage()
    stage["holes"] = [{
        "name": "geometry_value",
        "type": hole_type,
        "solver_hint": "trusted runtime geometry",
        "frame": "robot_base",
        "resolver": resolver,
        "anchor": anchor,
    }]

    errors = validate_live_hole_contract(
        {"k": 1, "stages": [stage]}, {"tube0", "rack"}
    )

    assert any(message in error for error in errors)


def test_final_graph_requires_complete_ordered_stage_semantics():
    first = _valid_final_stage()
    second = _valid_final_stage(
        index=1, name="release", start_sec=1.0, end_sec=2.0,
        evidence_frame=12,
    )
    stages = [
        {key: stage[key] for key in
         ("index", "name", "label", "start_sec", "end_sec")}
        for stage in (first, second)
    ]
    graph = {"k": 1, "stages": [first, second]}
    errors, _, checked = validate_final_graph(
        graph, stages, {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert errors == []
    assert checked == 4

    partial = {"k": 1, "stages": [first]}
    errors, _, _ = validate_final_graph(
        partial, stages, {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert any("contain every stages.json entry" in error for error in errors)

    reordered = {"k": 1, "stages": [second, first]}
    errors, _, _ = validate_final_graph(
        reordered, stages, {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert any("does not align" in error for error in errors)

    changed_window = deepcopy(graph)
    changed_window["stages"][1]["start_sec"] = 1.1
    changed_window["stages"][1]["label"] = "changed"
    errors, _, _ = validate_final_graph(
        changed_window, stages, {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert any("start_sec does not align" in error for error in errors)
    assert any("label does not align" in error for error in errors)


def test_final_graph_requires_k_and_per_stage_majority_count():
    stage = _valid_final_stage()
    manifest = [{key: stage[key] for key in
                 ("index", "name", "label", "start_sec", "end_sec")}]

    missing_k = {"stages": [stage]}
    errors, _, _ = validate_final_graph(
        missing_k, manifest, {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert any("graph.k must be a positive integer" in error for error in errors)

    missing_k_valid = deepcopy(stage)
    missing_k_valid.pop("k_valid")
    errors, _, _ = validate_final_graph(
        {"k": 1, "stages": [missing_k_valid]}, manifest,
        {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert any("k_valid must be a non-negative integer" in error for error in errors)

    no_majority = deepcopy(stage)
    no_majority["k_valid"] = 1
    errors, _, _ = validate_final_graph(
        {"k": 3, "stages": [no_majority]}, manifest,
        {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert any("不足多数票" in error for error in errors)


def test_final_graph_rejects_empty_or_malformed_semantics_and_invalid_evidence():
    stage = _valid_final_stage()
    stage["constraints"] = []
    stage["acceptance"][0].pop("holds")
    stage["acceptance"][0]["evidence_frames"] = [20]
    stage["holes"] = [None]
    graph = {"k": 1, "stages": [stage]}
    manifest = [{
        "index": 0, "name": "grasp", "label": "grasp stage",
        "start_sec": 0.0, "end_sec": 1.0,
    }]
    errors, _, _ = validate_final_graph(
        graph, manifest, {"tube0", "rack"}, fps=10.0, total_frames=20)
    assert any("constraints must not be empty" in error for error in errors)
    assert any("holds is outside" in error for error in errors)
    assert any("smaller than total_frames" in error for error in errors)
    assert any("holes[0] must be an object" in error for error in errors)

    errors, _, _ = validate_final_graph(
        {"stages": "not-a-list"}, manifest, set(), fps=10.0, total_frames=20)
    assert any("graph.stages must be a list" in error for error in errors)


def test_validate_run_dir_refreshes_validation_for_the_explicit_run(tmp_path):
    run_dir = tmp_path / "explicit_run"
    run_dir.mkdir()
    stage = _valid_final_stage()
    artifacts.write_json(run_dir / "graph.json", {"k": 1, "stages": [stage]})
    artifacts.write_json(run_dir / "stages.json", [{
        "index": 0, "name": "grasp", "label": "grasp stage",
        "start_sec": 0.0, "end_sec": 1.0,
    }])
    artifacts.write_json(run_dir / "meta.json", {
        "video": {"fps": 10.0, "total_frames": 20},
    })
    artifacts.write_json(run_dir / "objects.json", [{
        "id": "tube0", "category": "tube", "distinguishers": "only tube",
        "trace_aliases": [], "first_seen_frame": 0,
    }, {
        "id": "rack", "category": "rack", "distinguishers": "only rack",
        "trace_aliases": [], "first_seen_frame": 0,
    }])

    result = validate_run_dir(run_dir, "explicit")
    assert result["passed"] is True
    stage["acceptance"] = []
    artifacts.write_json(run_dir / "graph.json", {"k": 1, "stages": [stage]})
    refreshed = validate_run_dir(run_dir, "explicit")
    assert refreshed["passed"] is False
    assert artifacts.read_json(run_dir / "validation.json") == refreshed


def test_cached_response_only_reuses_completed_call(tmp_path):
    expected = {"model": "test/model", "prompt": "same"}
    assert llm.cached_response(tmp_path, "extract_s0_k0", expected) is None
    call_dir = tmp_path / "model_calls" / "extract_s0_k0"
    call_dir.mkdir(parents=True)
    (call_dir / "request.json").write_text(json.dumps(expected))
    assert llm.cached_response(tmp_path, "extract_s0_k0", expected) is None
    (call_dir / "raw.txt").write_text("model output")
    (call_dir / "call.json").write_text(json.dumps({"status": "ok"}))
    assert llm.cached_response(tmp_path, "extract_s0_k0", expected) == "model output"
    (call_dir / "result.json").write_text(json.dumps({
        "parse_status": "passed", "validator_status": "failed",
    }))
    assert llm.cached_response(tmp_path, "extract_s0_k0", expected) is None
    (call_dir / "result.json").unlink()
    (call_dir / "call.json").write_text(json.dumps({"status": "failed"}))
    assert llm.cached_response(tmp_path, "extract_s0_k0", expected) is None
    assert llm.cached_response(
        tmp_path, "extract_s0_k0", {"model": "other", "prompt": "same"}) is None


def test_request_record_fingerprints_image_content():
    def request(payload):
        messages = [{"role": "user", "content": [{
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{payload}"},
        }]}]
        return llm.request_record(
            messages, tag="vision", role="vision", model="test/model",
            max_tokens=10, temperature=0.1, input_refs=["frame.jpg"])

    first = request("AAAA")
    second = request("BBBB")
    assert first != second
    assert "AAAA" not in json.dumps(first)
    assert "BBBB" not in json.dumps(second)


def test_model_call_artifacts_redact_embedded_images(tmp_path, monkeypatch):
    class FakeUsage:
        def model_dump(self):
            return {"prompt_tokens": 12, "completion_tokens": 3, "cost": 0.01}

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        usage=FakeUsage(),
        model="provider/routed-model",
    )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: response))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DGL_COST_CAP", "1")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "inspect frame"},
        {"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64,VERY_LARGE_IMAGE"}},
    ]}]

    raw = llm.chat(
        messages, tmp_path, "stage_split", model="requested/model", retries=0,
        role="stage_split", input_refs=["frames/f00000.jpg"])
    assert raw == '{"ok": true}'
    llm.record_result(tmp_path, "stage_split", parsed={"ok": True})

    call_dir = tmp_path / "model_calls" / "stage_split"
    request_text = (call_dir / "request.json").read_text()
    assert "VERY_LARGE_IMAGE" not in request_text
    assert "frames/f00000.jpg" in request_text
    assert (call_dir / "raw.txt").read_text() == raw
    call = json.loads((call_dir / "call.json").read_text())
    assert call["role"] == "stage_split"
    assert call["model"] == "requested/model"
    assert call["response_model"] == "provider/routed-model"
    result = json.loads((call_dir / "result.json").read_text())
    assert result["parse_status"] == "passed"
    assert result["validator_status"] == "passed"

    llm.chat(
        messages, tmp_path, "stage_split", model="requested/model", retries=0,
        role="stage_split", input_refs=["frames/f00000.jpg"])
    history = call_dir / "history" / "call_001"
    assert (history / "raw.txt").read_text() == raw
    assert json.loads((history / "call.json").read_text())["status"] == "ok"


def test_cost_cap_is_checked_before_a_paid_call(tmp_path, monkeypatch):
    calls = {"count": 0}

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **_kwargs):
            calls["count"] += 1
            raise AssertionError("provider must not be called after the cap")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DGL_COST_CAP", "0")
    with pytest.raises(llm.CostCapExceeded, match="reached cap"):
        llm.chat([{"role": "user", "content": "test"}], tmp_path, "capped")
    assert calls["count"] == 0


def test_local_persistence_failure_never_retries_provider(tmp_path, monkeypatch):
    calls = {"count": 0}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        usage=None,
        model="test/model",
    )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **_kwargs):
            calls["count"] += 1
            return response

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DGL_COST_CAP", "1")

    def fail_append(*_args):
        raise OSError("ledger unavailable")

    monkeypatch.setattr(artifacts, "append_cost", fail_append)
    request = llm.request_record(
        [{"role": "user", "content": "test"}], tag="persist", role="persist",
        model="test/model", max_tokens=1500, temperature=0.2, input_refs=[])
    with pytest.raises(RuntimeError, match="persistence failed"):
        llm.chat(
            [{"role": "user", "content": "test"}], tmp_path, "persist",
            model="test/model", retries=3,
        )
    assert calls["count"] == 1
    assert llm.cached_response(tmp_path, "persist", request) is None


def test_extract_requires_registry_and_records_resolved_model(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "demo" / "20260804_000000"
    run_dir.mkdir(parents=True)
    artifacts.write_json(run_dir / "meta.json", {
        "task": "demo",
        "video": {"fps": 10.0, "total_frames": 20},
        "frames": [],
    })
    artifacts.write_json(run_dir / "stages.json", [{
        "index": 0, "name": "grasp", "label": "grasp",
        "start_sec": 0.0, "end_sec": 1.0,
    }])
    frame_dir = run_dir / "frames" / "stage00"
    frame_dir.mkdir(parents=True)
    (frame_dir / "f.jpg").write_bytes(b"jpeg-placeholder")
    artifacts.write_json(run_dir / "keyframes.json", {"0": [
        {"frame_idx": 3, "t_sec": 0.3, "file": "frames/stage00/f.jpg"},
        {"frame_idx": 5, "t_sec": 0.5, "file": "frames/stage00/f.jpg"},
    ]})
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)

    from demo_graph_lab.graph import extract

    artifacts.write_json(run_dir / "graph.json", {"stale": True})
    with pytest.raises(FileNotFoundError, match="objects.json is required"):
        extract.run("demo", k=1)
    assert not (run_dir / "graph.json").exists()

    objects = [{
        "id": "tube0", "category": "tube", "distinguishers": "only tube",
        "trace_aliases": [], "first_seen_frame": 0,
    }, {
        "id": "rack", "category": "rack", "distinguishers": "only rack",
        "trace_aliases": [], "first_seen_frame": 0,
    }]
    artifacts.write_json(run_dir / "objects.json", objects)
    artifacts.write_json(run_dir / "keyframes.json", {"0": []})
    with pytest.raises(ValueError, match="has no keyframes"):
        extract.run("demo", k=1)
    artifacts.write_json(run_dir / "keyframes.json", {"0": [
        {"frame_idx": 3, "t_sec": 0.3, "file": "frames/stage00/f.jpg"},
        {"frame_idx": 5, "t_sec": 0.5, "file": "frames/stage00/f.jpg"},
    ]})
    monkeypatch.setenv("DGL_VLM_MODEL", "resolved/offline-model")
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: json.dumps(
        _valid_stage_sample()))

    graph_path = extract.run("demo", k=1)
    graph = artifacts.read_json(graph_path)
    assert graph["model"] == "resolved/offline-model"
    assert graph["stages"][0]["k_valid"] == 1


def _extract_stage_run(tmp_path, monkeypatch, response: dict, k: int = 1) -> dict:
    """最小 run 目录:只跑一个 stage,模型回复固定为 response,返回 graph.json。"""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "demo" / "20260805_000000"
    frame_dir = run_dir / "frames" / "stage00"
    frame_dir.mkdir(parents=True)
    (frame_dir / "f.jpg").write_bytes(b"jpeg-placeholder")
    artifacts.write_json(run_dir / "meta.json", {
        "task": "demo", "video": {"fps": 10.0, "total_frames": 20}, "frames": [],
    })
    artifacts.write_json(run_dir / "stages.json", [{
        "index": 0, "name": "grasp", "label": "grasp",
        "start_sec": 0.0, "end_sec": 1.0,
    }])
    artifacts.write_json(run_dir / "keyframes.json", {"0": [
        {"frame_idx": 3, "t_sec": 0.3, "file": "frames/stage00/f.jpg"},
        {"frame_idx": 5, "t_sec": 0.5, "file": "frames/stage00/f.jpg"},
    ]})
    artifacts.write_json(run_dir / "objects.json", [
        {"id": "tube0", "category": "tube", "distinguishers": "only tube",
         "trace_aliases": [], "first_seen_frame": 0},
        {"id": "rack", "category": "rack", "distinguishers": "only rack",
         "trace_aliases": [], "first_seen_frame": 0},
    ])
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    monkeypatch.setenv("DGL_VLM_MODEL", "resolved/offline-model")
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: json.dumps(response))

    from demo_graph_lab.graph import extract

    return artifacts.read_json(extract.run("demo", k=k))


def test_extract_drops_bad_holes_and_accounts_for_them_per_stage(
        tmp_path, monkeypatch):
    sample = _valid_stage_sample()
    sample["holes"] = [
        {"name": "insert_depth", "type": "scalar", "solver_hint": "depth from rim"},
        dict(sample["holes"][0]),
    ]

    stage = _extract_stage_run(tmp_path, monkeypatch, sample)["stages"][0]

    assert stage["k_valid"] == 1 and stage["schema_fail"] == 0
    assert [hole["name"] for hole in stage["holes"]] == ["tube_grasp_pose"]
    assert len(stage["constraints"]) == 1 and len(stage["acceptance"]) == 1
    assert stage["hole_drops"]["count"] == 1
    assert stage["hole_drops"]["dropped"] == [{
        "sample": 0,
        "name": "insert_depth",
        "errors": [
            "sample.holes[0] missing fields ['frame']",
            "sample.holes[0].frame must be a non-empty string",
        ],
    }]
    assert stage["hole_drops"]["reasons"] == {
        "sample.holes[] missing fields ['frame']": 1,
        "sample.holes[].frame must be a non-empty string": 1,
    }


def test_extract_keeps_hole_drop_accounting_empty_on_a_clean_sample(
        tmp_path, monkeypatch):
    stage = _extract_stage_run(
        tmp_path, monkeypatch, _valid_stage_sample())["stages"][0]

    assert stage["hole_drops"] == {"count": 0, "reasons": {}, "dropped": []}


def test_cli_returns_nonzero_when_validate_fails(monkeypatch):
    from demo_graph_lab import cli
    from demo_graph_lab.graph import validate

    monkeypatch.setattr(artifacts, "load_env", lambda: {})
    monkeypatch.setattr(validate, "run", lambda _task: {"passed": False})
    assert cli.main(["validate", "--task", "demo"]) == 1


def test_cli_returns_nonzero_when_compile_fails(tmp_path, monkeypatch):
    from demo_graph_lab import cli
    from demo_graph_lab.policy import compiler

    report_path = tmp_path / "compile_report.json"
    artifacts.write_json(report_path, {
        "graph_validation": "failed",
        "program_violations": [],
        "static_violations": [],
    })
    monkeypatch.setattr(artifacts, "load_env", lambda: {})
    monkeypatch.setattr(compiler, "run", lambda *_args: report_path)
    assert cli.main(["compile", "--task", "demo"]) == 1


def test_all_returns_nonzero_after_failed_validation(monkeypatch):
    from demo_graph_lab import cli
    from demo_graph_lab.demo import ingest, keyframes, registry, stages
    from demo_graph_lab.graph import enrich, extract, report, validate

    monkeypatch.setattr(artifacts, "load_env", lambda: {})
    monkeypatch.setattr(ingest, "run", lambda *_args: None)
    monkeypatch.setattr(stages, "run", lambda *_args: None)
    monkeypatch.setattr(keyframes, "run", lambda *_args: None)
    monkeypatch.setattr(registry, "run", lambda *_args: None)
    monkeypatch.setattr(extract, "run", lambda *_args: None)
    monkeypatch.setattr(enrich, "run", lambda *_args: None)
    monkeypatch.setattr(validate, "run", lambda *_args: {"passed": False})
    rendered = {"called": False}
    monkeypatch.setattr(report, "run", lambda *_args: rendered.update(called=True))

    assert cli.main(["all", "--task", "demo"]) == 1
    assert rendered["called"] is True
