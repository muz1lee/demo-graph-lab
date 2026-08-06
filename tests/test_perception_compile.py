"""Offline contracts for the second compile segment; no test here touches network."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from demo_graph_lab.common import artifacts, llm
from demo_graph_lab.graph import validate as graph_validate
from demo_graph_lab.perception.program import (
    OPERATORS,
    RESOLVER_BINDINGS,
    SCHEMA,
    validate_perception_program,
)
from demo_graph_lab.policy import compiler

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "graphs"

_PEG_ANCHOR = {"object_id": "peg", "part": "whole"}
_OPENING_ANCHOR = {"object_id": "fixture", "part": "hole", "instance": "center"}
_SIDE_ANCHOR = {"object_id": "fixture", "part": "hole", "instance": "side"}


def _graph() -> dict:
    return {
        "task": "mini",
        "stages": [{
            "index": 0,
            "name": "insertion",
            "stage_objects": {"manipulated": "peg", "target": "fixture"},
            "constraints": [],
            "acceptance": [{"name": "inside",
                            "args": {"obj_a": "peg", "obj_b": "fixture"}}],
            "holes": [
                {"name": "peg_grasp_pose", "type": "pose_se3",
                 "resolver": "grasp_candidate", "anchor": dict(_PEG_ANCHOR)},
                {"name": "peg_long_axis", "type": "axis_3d",
                 "resolver": "principal_axis", "anchor": dict(_PEG_ANCHOR)},
                {"name": "opening_center", "type": "point_3d",
                 "resolver": "part_center", "anchor": dict(_OPENING_ANCHOR)},
                {"name": "opening_axis", "type": "axis_3d",
                 "resolver": "part_axis", "anchor": dict(_OPENING_ANCHOR)},
                # 可发布但 StageProgram 没有接线:这一轮不需要它的值。
                {"name": "unwired_opening_center", "type": "point_3d",
                 "resolver": "part_center", "anchor": dict(_SIDE_ANCHOR)},
                {"name": "seated", "type": "runtime_condition",
                 "purpose": "lower_stop"},
            ],
        }],
    }


def _stage_program() -> dict:
    return {"stages": [{
        "index": 0,
        "name": "insertion",
        "selection": {
            "grasp_hole": "peg_grasp_pose",
            "current_constraints": [],
            "downstream_constraints": [],
        },
        "actions": [
            {"op": "grasp_at", "args": {
                "grasp_pose": {"hole": "peg_grasp_pose"},
                "axis": {"hole": "peg_long_axis"}}},
            {"op": "align", "args": {
                "obj": {"object": "peg"},
                "target": {"hole": "opening_center"},
                "axis": {"hole": "opening_axis"}}},
            {"op": "lower_until", "args": {"stop_condition": {"hole": "seated"}}},
            {"op": "release", "args": {}},
        ],
    }]}


def _perception_doc() -> dict:
    return {
        "schema": SCHEMA,
        "task": "mini",
        "programs": [
            {"stage": 0, "chain": ["localize", "segment", "fit_opening"],
             "provides": [{"field": "center", "hole": "opening_center"},
                          {"field": "axis", "hole": "opening_axis"}]},
            {"stage": 0, "chain": ["localize", "segment", "crop_points", "fit_axis"],
             "provides": [{"field": "axis", "hole": "peg_long_axis"}]},
        ],
    }


def _prepare(tmp_path, monkeypatch, graph, program):
    """Freeze a validated run dir and hand back the recorded backend calls."""
    artifacts.write_json(tmp_path / "graph.json", graph)
    artifacts.write_json(tmp_path / "objects.json", [{"id": "peg"}, {"id": "fixture"}])
    artifacts.write_json(tmp_path / "validation.json", {
        "passed": True, "violations": []})
    monkeypatch.setattr(artifacts, "latest_run_dir", lambda _task: tmp_path)
    monkeypatch.setattr(graph_validate, "validate_run_dir", lambda *_args: {
        "passed": True, "violations": []})
    return program


def _canned(monkeypatch, program, perception):
    """Serve one canned reply per tag and record what the compiler asked for."""
    calls: list[dict] = []

    def chat(messages, run_dir, tag, **_kwargs):
        calls.append({"tag": tag, "prompt": messages[0]["content"]})
        if tag == "compile_perception":
            assert perception is not None, "感知段不该被调用"
            return json.dumps(perception)
        return json.dumps(program)

    monkeypatch.setattr(llm, "chat", chat)
    return calls


# --------------------------------------------------------------------------
# 输入构造:覆盖目标来自 StageProgram 的 hole wiring,不是 graph 里的全部几何洞。
# --------------------------------------------------------------------------
def test_targets_are_the_wired_publishable_holes_in_first_use_order():
    targets = compiler.perception_targets(_stage_program(), _graph())

    assert list(targets) == [0]
    assert [hole["name"] for hole in targets[0]] == [
        "peg_long_axis", "opening_center", "opening_axis"]
    # 抓取洞与未接线洞都不是目标,前者不由感知程序发布,后者这一轮不需要值。
    assert targets[0][0]["anchor"] == _PEG_ANCHOR


# --------------------------------------------------------------------------
# 合法 canned 响应:两个产物齐全。
# --------------------------------------------------------------------------
def test_compile_publishes_stage_and_perception_programs(tmp_path, monkeypatch):
    program = _prepare(tmp_path, monkeypatch, _graph(), _stage_program())
    doc = _perception_doc()
    calls = _canned(monkeypatch, program, doc)

    report = artifacts.read_json(compiler.run("mini", model="test/model"))

    assert [call["tag"] for call in calls] == ["compile", "compile_perception"]
    assert artifacts.read_json(tmp_path / "stage_program.json") == program
    assert artifacts.read_json(tmp_path / "perception_program.json") == doc
    assert (tmp_path / "policy.py").exists()
    assert compiler.report_ready(report) is True
    assert report["perception_program"] == {
        "status": "published",
        "ref": "perception_program.json",
        "violations": [],
        "coverage": [{
            "stage": 0,
            "covered": ["opening_axis", "opening_center", "peg_long_axis"],
            "uncovered": ["peg_grasp_pose", "unwired_opening_center"],
        }],
    }
    result = artifacts.read_json(
        tmp_path / "model_calls" / "compile_perception" / "result.json")
    assert result["validator_status"] == "passed"
    assert result["parsed"] == doc


def test_perception_prompt_renders_the_operator_and_binding_tables_from_code(
    tmp_path, monkeypatch,
):
    program = _prepare(tmp_path, monkeypatch, _graph(), _stage_program())
    calls = _canned(monkeypatch, program, _perception_doc())

    compiler.run("mini", model="test/model")

    prompt = next(call["prompt"] for call in calls
                  if call["tag"] == "compile_perception")
    # 算子表与绑定表只有代码一份真相源,prompt 里不手写第二份。
    for name, spec in OPERATORS.items():
        assert f"| `{name}` | `{spec['consumes']}` | `{spec['produces']}` |" in prompt
    for resolver, (operator, field) in RESOLVER_BINDINGS.items():
        assert f"| `{resolver}` | `{operator}` field `{field}` |" in prompt
    # 目标洞是接线过的那些,连同它们的 resolver/anchor 契约。
    assert '"name": "opening_axis"' in prompt
    assert '"resolver": "part_axis"' in prompt
    assert "unwired_opening_center" not in prompt
    assert "peg_grasp_pose" not in prompt


def test_prompt_worked_example_still_validates_against_its_fixture_graph():
    """few-shot 是 fixture 的真实片段;它失效时必须在这里失败,而不是在生产路径。"""
    prompt = (artifacts.PROMPT_ROOT / "compile_perception.md").read_text()
    blocks = [block.split("```")[0] for block in prompt.split("```json\n")[1:]]
    example = json.loads(blocks[-1])
    fixture = json.loads(
        (_FIXTURE_ROOT / "insert_tubes.perception_program.json").read_text())
    graph = json.loads((_FIXTURE_ROOT / "insert_tubes.graph.json").read_text())

    assert validate_perception_program(example, graph) == []
    for entry in example["programs"]:
        assert entry in fixture["programs"]


# --------------------------------------------------------------------------
# 非法 canned 响应:感知程序不发布,policy.py 与退出状态不受影响。
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("mutate", "message"), [
    # 违规链:主轴洞被开口拟合发布,类型一致但语义不同。
    (lambda doc: doc["programs"][1].update(
        chain=["localize", "segment", "fit_opening"]),
     "类型一致不能替代 resolver 绑定"),
    # 越界洞:抓取洞不由感知程序发布。
    (lambda doc: doc["programs"][1]["provides"].__setitem__(
        0, {"field": "axis", "hole": "peg_grasp_pose"}),
     "不由感知程序发布"),
])
def test_rejected_perception_program_never_publishes_but_keeps_the_policy(
    tmp_path, monkeypatch, mutate, message,
):
    program = _prepare(tmp_path, monkeypatch, _graph(), _stage_program())
    doc = _perception_doc()
    mutate(doc)
    _canned(monkeypatch, program, doc)

    report_path = compiler.run("mini", model="test/model")
    report = artifacts.read_json(report_path)

    section = report["perception_program"]
    assert section["status"] == "failed"
    assert section["ref"] is None
    assert section["coverage"] == []
    assert any(message in violation for violation in section["violations"])
    assert not (tmp_path / "perception_program.json").exists()
    # StageProgram 侧完全不受影响,CLI 退出状态只看这两项。
    assert (tmp_path / "policy.py").exists()
    assert compiler.report_ready(report) is True
    result = artifacts.read_json(
        tmp_path / "model_calls" / "compile_perception" / "result.json")
    assert result["validator_status"] == "failed"
    assert result["validation_errors"] == section["violations"]


def test_unparseable_perception_reply_is_recorded_as_a_failure(tmp_path, monkeypatch):
    program = _prepare(tmp_path, monkeypatch, _graph(), _stage_program())

    def chat(messages, run_dir, tag, **_kwargs):
        return "no JSON here" if tag == "compile_perception" else json.dumps(program)

    monkeypatch.setattr(llm, "chat", chat)
    report = artifacts.read_json(compiler.run("mini", model="test/model"))

    assert report["perception_program"]["status"] == "failed"
    assert any("no parseable JSON" in violation
               for violation in report["perception_program"]["violations"])
    assert not (tmp_path / "perception_program.json").exists()
    assert compiler.report_ready(report) is True
    result = artifacts.read_json(
        tmp_path / "model_calls" / "compile_perception" / "result.json")
    assert result["parse_status"] == "failed"
    assert result["validator_status"] == "not_run"


# --------------------------------------------------------------------------
# skipped:没有可发布目标时不调用 backend。
# --------------------------------------------------------------------------
def test_compile_skips_the_perception_call_when_no_hole_is_publishable(
    tmp_path, monkeypatch,
):
    graph = _graph()
    graph["stages"][0]["holes"] = [
        hole for hole in graph["stages"][0]["holes"]
        if hole["name"] == "peg_grasp_pose"
    ]
    grasp_only = {"stages": [{
        "index": 0,
        "name": "insertion",
        "selection": {
            "grasp_hole": "peg_grasp_pose",
            "current_constraints": [],
            "downstream_constraints": [],
        },
        "actions": [
        {"op": "grasp_at", "args": {"grasp_pose": {"hole": "peg_grasp_pose"}}},
        {"op": "release", "args": {}},
    ]}]}
    program = _prepare(tmp_path, monkeypatch, graph, grasp_only)
    calls = _canned(monkeypatch, program, None)

    report = artifacts.read_json(compiler.run("mini", model="test/model"))

    assert [call["tag"] for call in calls] == ["compile"]
    assert report["perception_program"] == {
        "status": "skipped", "ref": None, "violations": [], "coverage": []}
    assert not (tmp_path / "model_calls" / "compile_perception").exists()
    assert not (tmp_path / "perception_program.json").exists()
    assert compiler.report_ready(report) is True


def test_failed_stage_program_never_reaches_the_perception_segment(
    tmp_path, monkeypatch,
):
    program = _prepare(tmp_path, monkeypatch, _graph(), {"stages": []})
    calls = _canned(monkeypatch, program, None)

    report = artifacts.read_json(compiler.run("mini", model="test/model"))

    assert [call["tag"] for call in calls] == ["compile"]
    assert report["program_violations"]
    assert "perception_program" not in report
    assert not (tmp_path / "perception_program.json").exists()


# --------------------------------------------------------------------------
# 记账与缓存:新调用点走既有机制,不另起一套。
# --------------------------------------------------------------------------
def test_perception_call_is_metered_and_reused_from_the_request_cache(
    tmp_path, monkeypatch,
):
    program = _prepare(tmp_path, monkeypatch, _graph(), _stage_program())
    doc = _perception_doc()
    replies = [json.dumps(program), json.dumps(doc)]
    provider_calls = {"count": 0}

    class FakeUsage:
        def model_dump(self):
            return {"prompt_tokens": 12, "completion_tokens": 3, "cost": 0.01}

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create))

        def create(self, **_kwargs):
            provider_calls["count"] += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=replies.pop(0)))],
                usage=FakeUsage(),
                model="provider/routed-model")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DGL_COST_CAP", "1")

    report = artifacts.read_json(compiler.run("mini", model="test/model"))

    assert report["perception_program"]["status"] == "published"
    assert provider_calls["count"] == 2
    ledger = [json.loads(line) for line in
              (tmp_path / "cost.jsonl").read_text().splitlines() if line.strip()]
    assert [entry["tag"] for entry in ledger] == ["compile", "compile_perception"]
    assert [entry["role"] for entry in ledger] == [
        "policy_program", "perception_program"]
    assert artifacts.accumulated_cost(tmp_path) == pytest.approx(0.02)

    call_dir = tmp_path / "model_calls" / "compile_perception"
    assert json.loads((call_dir / "raw.txt").read_text()) == doc
    request = artifacts.read_json(call_dir / "request.json")
    assert request["input_refs"] == [
        "graph.json", "stage_program.json", "package:perception/program.py",
        "package:prompts/compile_perception.md"]

    # 第二次编译:两个调用点都命中请求指纹缓存,产物照常重建,provider 不再被调用。
    again = artifacts.read_json(compiler.run("mini", model="test/model"))
    assert provider_calls["count"] == 2
    assert again["perception_program"] == report["perception_program"]
    assert artifacts.read_json(tmp_path / "perception_program.json") == doc
    assert artifacts.accumulated_cost(tmp_path) == pytest.approx(0.02)
