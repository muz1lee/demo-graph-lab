"""StageProgram contract and deterministic compiler tests."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from demo_graph_lab.policy.compiler import dry_run, static_check
from demo_graph_lab.policy.program import (
    ARGUMENT_SPECS,
    PRIMITIVES,
    compile_program,
    unwired_holes,
    validate_program,
    wired_hole_contracts_by_stage,
    wired_holes_by_stage,
)


def _graph() -> dict:
    return {
        "task": "mini",
        "stages": [
            {
                "index": 0,
                "name": "transfer",
                "stage_objects": {"manipulated": "tube0", "target": "rack"},
                "constraints": [],
                "acceptance": [{"name": "carry", "args": {}}],
                "holes": [
                    {"name": "grasp_pose", "type": "pose_se3"},
                    {"name": "target_point", "type": "point_3d"},
                    {"name": "retract_pose", "type": "pose_se3",
                     "solver_hint": "post-release retract pose"},
                    {"name": "tube_axis", "type": "axis_3d"},
                    {"name": "hole_axis", "type": "axis_3d"},
                    {"name": "contact", "type": "runtime_condition",
                     "purpose": "lower_stop"},
                    {"name": "clearance", "type": "scalar"},
                ],
            }
        ],
    }


def _program(actions: list[dict]) -> dict:
    return {"stages": [{"index": 0, "name": "transfer", "actions": actions}]}


def test_all_runtime_primitives_compile_and_dry_run():
    program = _program([
        {"op": "approach", "args": {
            "target": {"object": "tube0"}, "cone": "top_down"}},
        {"op": "grasp_at", "args": {
            "grasp_pose": {"hole": "grasp_pose"},
            "axis": {"hole": "tube_axis"}}},
        {"op": "lift", "args": {"obj": {"object": "tube0"}}},
        {"op": "reorient_held_axis", "args": {
            "obj": {"object": "tube0"},
            "object_axis": {"hole": "tube_axis"},
            "target_direction": {"hole": "hole_axis"}}},
        {"op": "transport", "args": {
            "obj": {"object": "tube0"},
            "target": {"hole": "target_point"}}},
        {"op": "align", "args": {
            "obj": {"object": "tube0"},
            "target": {"object": "rack"},
            "axis": {"hole": "tube_axis"}}},
        {"op": "lower_until", "args": {
            "stop_condition": {"hole": "contact"}}},
        {"op": "release", "args": {}},
        {"op": "retreat", "args": {
            "target": {"hole": "retract_pose"}}},
    ])

    assert set(PRIMITIVES) == set(ARGUMENT_SPECS)
    from demo_graph_lab.policy.api import RuntimeAPI
    for primitive in PRIMITIVES:
        parameters = set(inspect.signature(getattr(RuntimeAPI, primitive)).parameters)
        assert parameters - {"self"} == set(ARGUMENT_SPECS[primitive])
    assert validate_program(program, _graph()) == []
    code = compile_program(program, _graph())
    assert static_check(code) == []
    assert code.count("rt.solve('tube_axis')") == 1
    assert "rt.grasp_at(grasp_pose=h0, axis=h1)" in code
    assert "rt.release()" in code

    result = dry_run(code, _graph())
    assert result["normal"]["ok"]
    assert result["holes_solved"] == [
        "contact", "grasp_pose", "hole_axis", "retract_pose", "target_point",
        "tube_axis",
    ]
    action_calls = [
        call["op"] for call in result["calls"]
        if call["op"] not in {"solve", "verify"}
    ]
    assert action_calls[:9] == list(PRIMITIVES)


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ({"op": "teleport", "args": {}}, "未支持 primitive"),
        ({"op": "lift", "args": {}}, "缺少参数 ['obj']"),
        ({"op": "release", "args": {"target": {"object": "rack"}}},
         "含未知参数 ['target']"),
        ({"op": "lift", "args": {"obj": {"object": "ghost"}}},
         "未知 stage object"),
        ({"op": "grasp_at", "args": {
            "grasp_pose": {"hole": "missing"}}}, "未声明 hole"),
        ({"op": "grasp_at", "args": {
            "grasp_pose": {"hole": "tube_axis"}}}, "类型 'axis_3d' 不兼容"),
        ({"op": "approach", "args": {
            "target": {"object": "tube0"}, "cone": "vertical"}}, "未知离散值"),
        ({"op": "approach", "args": {
            "target": {"object": "tube0"}, "cone": 30}}, "禁止数值字面量"),
        ({"op": "transport", "args": {
            "obj": {"object": "tube0"}, "target": [0.1, 0.2, 0.3]}},
         "禁止数值字面量"),
        ({"op": "lift", "args": {"obj": {"ref": "tube0"}}}, "未知引用格式"),
        # 模型提案原语:两个轴参数都必填,且只收 axis_3d。
        ({"op": "reorient_held_axis", "args": {
            "obj": {"object": "tube0"},
            "object_axis": {"hole": "tube_axis"}}},
         "缺少参数 ['target_direction']"),
        ({"op": "reorient_held_axis", "args": {
            "obj": {"object": "tube0"},
            "object_axis": {"hole": "grasp_pose"},
            "target_direction": {"hole": "hole_axis"}}},
         "类型 'pose_se3' 不兼容"),
    ],
)
def test_stage_program_rejects_bad_actions(action, message):
    errors = validate_program(_program([action]), _graph())
    assert any(message in error for error in errors), errors


def test_stage_program_requires_exact_graph_stages():
    graph = _graph()
    graph["stages"].append({
        "index": 2,
        "name": "release",
        "stage_objects": {"manipulated": "tube0", "target": "rack"},
        "constraints": [],
        "acceptance": [],
        "holes": [],
    })
    wrong = _program([])
    wrong["stages"][0]["name"] = "pick"
    errors = validate_program(wrong, graph)
    assert any("应为 'transfer'" in error for error in errors)
    assert any("缺少 graph stages [2]" in error for error in errors)
    with pytest.raises(ValueError, match="StageProgram validation failed"):
        compile_program(wrong, graph)


def test_empty_or_reversed_stage_is_rejected():
    assert any("至少需要一个 primitive" in error
               for error in validate_program(_program([]), _graph()))
    reversed_program = _program([
        {"op": "release", "args": {}},
        {"op": "lift", "args": {"obj": {"object": "tube0"}}},
    ])
    assert any("primitive 顺序倒退" in error
               for error in validate_program(reversed_program, _graph()))


def _reorient_action() -> dict:
    return {"op": "reorient_held_axis", "args": {
        "obj": {"object": "tube0"},
        "object_axis": {"hole": "tube_axis"},
        "target_direction": {"hole": "hole_axis"}}}


def test_reorient_held_axis_sits_between_lift_and_transport():
    """模型提案原语的链序位:lift 之后(前置条件是「已被持有」)、transport 之前。"""
    assert (PRIMITIVES.index("lift")
            < PRIMITIVES.index("reorient_held_axis")
            < PRIMITIVES.index("transport"))

    legal = _program([
        {"op": "lift", "args": {"obj": {"object": "tube0"}}},
        _reorient_action(),
        {"op": "transport", "args": {
            "obj": {"object": "tube0"}, "target": {"hole": "target_point"}}},
    ])
    assert validate_program(legal, _graph()) == []

    # 搬完再转:已经对准的落点会重新失准,链序上直接判倒退。
    illegal = _program([
        {"op": "transport", "args": {
            "obj": {"object": "tube0"}, "target": {"hole": "target_point"}}},
        _reorient_action(),
    ])
    assert any("primitive 顺序倒退到 'reorient_held_axis'" in error
               for error in validate_program(illegal, _graph()))


def test_compile_prompt_renders_the_closed_set_from_code():
    """compile prompt 的原语闭集由代码渲染,prompt 文件里不留第二份手写副本。"""
    from demo_graph_lab.common import artifacts
    from demo_graph_lab.policy.compiler import compile_prompt

    prompt = compile_prompt(_graph())

    # 每个原语都进 prompt,链序也从 PRIMITIVES 渲染。
    for primitive in PRIMITIVES:
        assert f"| `{primitive}` |" in prompt
    assert " → ".join(f"`{op}`" for op in PRIMITIVES) in prompt
    # 模型提案入库的新原语与它两个必填 axis_3d 参数:靠渲染进 prompt,
    # 不靠有人记得往 prompt 文件里补一行。
    assert "| `reorient_held_axis` | `object_axis` | hole of type `axis_3d` |" in prompt
    assert ("| `reorient_held_axis` | `target_direction` | hole of type `axis_3d` |"
            in prompt)
    assert "| `approach` | `cone` (optional) | one of `oblique`, `side`, `top_down` |" \
        in prompt
    assert "hole `purpose` must be `lower_stop`" in prompt

    # prompt 源文件不再枚举原语:只有 JSON 示例里的 approach/grasp_at 和
    # 几条表格表达不了的硬规则会提到原语名。
    source = (artifacts.PROMPT_ROOT / "compile_policy.md").read_text()
    for primitive in ("lift", "reorient_held_axis", "transport", "align"):
        assert primitive not in source


@pytest.mark.parametrize("actions", [
    [
        {"op": "lower_until", "args": {
            "stop_condition": {"hole": "contact"}}},
        {"op": "lower_until", "args": {
            "stop_condition": {"hole": "contact"}}},
    ],
    [
        {"op": "release", "args": {}},
        {"op": "release", "args": {}},
    ],
])
def test_stage_program_rejects_repeated_primitive(actions):
    errors = validate_program(_program(actions), _graph())
    assert any("同一 stage 不允许重复 primitive" in error for error in errors)


def test_lower_until_requires_a_dedicated_lower_stop_hole():
    graph = _graph()
    contact = next(hole for hole in graph["stages"][0]["holes"]
                   if hole["name"] == "contact")
    del contact["purpose"]
    program = _program([{"op": "lower_until", "args": {
        "stop_condition": {"hole": "contact"}}}])
    errors = validate_program(program, graph)
    assert any("purpose='lower_stop'" in error for error in errors)


def test_retreat_requires_release_in_the_same_stage():
    program = _program([{"op": "retreat", "args": {
        "target": {"hole": "retract_pose"}}}])
    errors = validate_program(program, _graph())
    assert any("retreat 必须位于 release 之后" in error for error in errors)


def test_retreat_rejects_a_generic_target_hole():
    program = _program([
        {"op": "release", "args": {}},
        {"op": "retreat", "args": {"target": {"hole": "target_point"}}},
    ])
    errors = validate_program(program, _graph())
    assert any("未明确声明 retract/retreat 语义" in error for error in errors)


def test_release_and_retreat_may_be_adjacent_stages():
    graph = _graph()
    graph["stages"].append({
        "index": 1,
        "name": "retreat",
        "stage_objects": {"manipulated": "tube0", "target": "rack"},
        "constraints": [],
        "acceptance": [{"name": "above", "args": {}}],
        "holes": [{
            "name": "retract_pose", "type": "pose_se3",
            "solver_hint": "safe retreat pose",
        }],
    })
    program = {"stages": [
        {"index": 0, "name": "transfer", "actions": [
            {"op": "release", "args": {}},
        ]},
        {"index": 1, "name": "retreat", "actions": [
            {"op": "retreat", "args": {"target": {"hole": "retract_pose"}}},
        ]},
    ]}
    assert validate_program(program, graph) == []


def test_unwired_holes_are_reported_without_rejecting_program():
    program = _program([
        {"op": "lift", "args": {"obj": {"object": "tube0"}}},
    ])
    assert validate_program(program, _graph()) == []
    assert unwired_holes(program, _graph()) == [{
        "stage": 0,
        "holes": [
            "clearance", "contact", "grasp_pose", "hole_axis", "retract_pose",
            "target_point", "tube_axis",
        ],
    }]


def test_wired_holes_follow_runtime_signature_not_json_key_order():
    program = _program([
        {
            "op": "grasp_at",
            "args": {
                "axis": {"hole": "tube_axis"},
                "grasp_pose": {"hole": "grasp_pose"},
            },
        }
    ])

    assert validate_program(program, _graph()) == []
    assert wired_holes_by_stage(program) == {
        0: ("grasp_pose", "tube_axis"),
    }


def test_wired_hole_contracts_preserve_resolver_and_anchor_metadata():
    graph = _graph()
    grasp_pose, tube_axis = graph["stages"][0]["holes"][0], \
        graph["stages"][0]["holes"][3]
    grasp_pose.update({
        "resolver": "grasp_candidate",
        "anchor": {"object_id": "tube0", "part": "body"},
    })
    tube_axis.update({
        "resolver": "principal_axis",
        "anchor": {"object_id": "tube0", "part": "long_axis"},
    })
    program = _program([{
        "op": "grasp_at",
        "args": {
            "axis": {"hole": "tube_axis"},
            "grasp_pose": {"hole": "grasp_pose"},
        },
    }])

    contracts = wired_hole_contracts_by_stage(program, graph)

    assert tuple(hole["name"] for hole in contracts[0]) == (
        "grasp_pose", "tube_axis")
    assert contracts[0][0]["resolver"] == "grasp_candidate"
    assert contracts[0][1]["anchor"] == {
        "object_id": "tube0", "part": "long_axis"}
    contracts[0][0]["anchor"]["part"] = "mutated"
    assert grasp_pose["anchor"]["part"] == "body"


def test_compile_command_writes_program_then_deterministic_policy(tmp_path, monkeypatch):
    from demo_graph_lab.common import artifacts, llm
    from demo_graph_lab.graph import validate as graph_validate
    from demo_graph_lab.policy import compiler

    graph = _graph()
    artifacts.write_json(tmp_path / "graph.json", graph)
    artifacts.write_json(tmp_path / "objects.json", [{"id": "tube0"}, {"id": "rack"}])
    artifacts.write_json(tmp_path / "validation.json", {"passed": True, "violations": []})
    program = _program([
        {"op": "lift", "args": {"obj": {"object": "tube0"}}},
        {"op": "release", "args": {}},
    ])
    monkeypatch.setattr(artifacts, "latest_run_dir", lambda task: tmp_path)
    monkeypatch.setattr(graph_validate, "validate_run_dir", lambda *_args: {
        "passed": True, "violations": [],
    })
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: json.dumps(program))

    report_path = compiler.run("mini", model="test/model")

    assert report_path == tmp_path / "compile_report.json"
    assert artifacts.read_json(tmp_path / "stage_program.json") == program
    code = (tmp_path / "policy.py").read_text()
    assert "rt.lift(obj='tube0')" in code
    assert "rt.release()" in code
    report = artifacts.read_json(report_path)
    assert report["program_violations"] == []
    assert report["static_violations"] == []
    assert report["unwired_holes"]
    assert report["dryrun"]["normal"]["ok"]
    assert report["compiled_program"] == program
    call_result = artifacts.read_json(tmp_path / "model_calls" / "compile" / "result.json")
    assert call_result["parse_status"] == "passed"
    assert call_result["validator_status"] == "passed"

    from demo_graph_lab.execution.cli import _load_artifacts
    _, _, handlers = _load_artifacts(tmp_path)
    assert set(handlers) == {0}
    changed_graph = dict(graph, task="changed-after-compile")
    artifacts.write_json(tmp_path / "graph.json", changed_graph)
    with pytest.raises(ValueError, match="graph changed after policy compilation"):
        _load_artifacts(tmp_path)
    artifacts.write_json(tmp_path / "graph.json", graph)
    artifacts.write_json(tmp_path / "objects.json", [{"id": "changed"}])
    with pytest.raises(ValueError, match="object registry changed after compilation"):
        _load_artifacts(tmp_path)
    artifacts.write_json(tmp_path / "objects.json", [{"id": "tube0"}, {"id": "rack"}])
    policy_path = tmp_path / "policy.py"
    policy_path.write_text(policy_path.read_text() + "\n")
    with pytest.raises(ValueError, match="policy does not match StageProgram"):
        _load_artifacts(tmp_path)
    changed_program = _program([{"op": "release", "args": {}}])
    artifacts.write_json(tmp_path / "stage_program.json", changed_program)
    policy_path.write_text(compile_program(changed_program, graph))
    with pytest.raises(ValueError, match="StageProgram changed after compile dry-run"):
        _load_artifacts(tmp_path)
    retreat_program = _program([
        {"op": "release", "args": {}},
        {"op": "retreat", "args": {"target": {"hole": "retract_pose"}}},
    ])
    artifacts.write_json(tmp_path / "stage_program.json", retreat_program)
    policy_path.write_text(compile_program(retreat_program, graph))
    report["compiled_program"] = retreat_program
    artifacts.write_json(report_path, report)
    with pytest.raises(ValueError, match="retreat solver is unavailable"):
        _load_artifacts(tmp_path)


@pytest.mark.parametrize(("validation", "status"), [
    (None, "missing"),
    ({"passed": False, "violations": ["bad graph"]}, "failed"),
])
def test_compile_command_refuses_unvalidated_graph(
    tmp_path, monkeypatch, validation, status,
):
    from demo_graph_lab.common import artifacts, llm
    from demo_graph_lab.policy import compiler

    artifacts.write_json(tmp_path / "graph.json", _graph())
    (tmp_path / "policy.py").write_text("stale policy")
    artifacts.write_json(tmp_path / "stage_program.json", {"stale": True})
    if validation is not None:
        artifacts.write_json(tmp_path / "validation.json", validation)
    monkeypatch.setattr(artifacts, "latest_run_dir", lambda task: tmp_path)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("backend must not run before graph validation passes")

    monkeypatch.setattr(llm, "chat", unexpected_call)
    report_path = compiler.run("mini")
    report = artifacts.read_json(report_path)
    assert report["graph_validation"] == status
    assert not (tmp_path / "policy.py").exists()
    assert not (tmp_path / "stage_program.json").exists()
    assert not (tmp_path / "compiled_graph.json").exists()
    assert not (tmp_path / "compiled_objects.json").exists()


def test_compile_does_not_publish_policy_when_dry_run_fails(tmp_path, monkeypatch):
    from demo_graph_lab.common import artifacts, llm
    from demo_graph_lab.graph import validate as graph_validate
    from demo_graph_lab.policy import compiler

    artifacts.write_json(tmp_path / "graph.json", _graph())
    artifacts.write_json(tmp_path / "objects.json", [{"id": "tube0"}, {"id": "rack"}])
    artifacts.write_json(tmp_path / "validation.json", {"passed": True})
    program = _program([{"op": "lift", "args": {"obj": {"object": "tube0"}}}])
    monkeypatch.setattr(artifacts, "latest_run_dir", lambda task: tmp_path)
    monkeypatch.setattr(graph_validate, "validate_run_dir", lambda *_args: {
        "passed": True, "violations": [],
    })
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: json.dumps(program))
    monkeypatch.setattr(compiler, "dry_run", lambda *_args: {
        "normal": {"ok": False}, "retry_injection": {"ok": True},
    })

    report = artifacts.read_json(compiler.run("mini"))
    assert "dry-run failed" in report["dryrun_error"]
    assert not (tmp_path / "policy.py").exists()


def test_compile_does_not_publish_if_objects_change_during_backend_work(
    tmp_path, monkeypatch,
):
    from demo_graph_lab.common import artifacts, llm
    from demo_graph_lab.graph import validate as graph_validate
    from demo_graph_lab.policy import compiler

    graph = _graph()
    original_objects = [{"id": "tube0"}, {"id": "rack"}]
    artifacts.write_json(tmp_path / "graph.json", graph)
    artifacts.write_json(tmp_path / "objects.json", original_objects)
    artifacts.write_json(tmp_path / "validation.json", {"passed": True})
    program = _program([{"op": "lift", "args": {"obj": {"object": "tube0"}}}])
    monkeypatch.setattr(artifacts, "latest_run_dir", lambda _task: tmp_path)
    monkeypatch.setattr(graph_validate, "validate_run_dir", lambda *_args: {
        "passed": True, "violations": [],
    })
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: json.dumps(program))
    real_dry_run = compiler.dry_run

    def mutate_objects(code, frozen_graph):
        result = real_dry_run(code, frozen_graph)
        artifacts.write_json(tmp_path / "objects.json", [{"id": "ghost"}])
        return result

    monkeypatch.setattr(compiler, "dry_run", mutate_objects)
    report = artifacts.read_json(compiler.run("mini"))

    assert "changed during compilation" in report["publish_error"]
    assert compiler.report_ready(report) is False
    assert not (tmp_path / "policy.py").exists()
    assert not (tmp_path / "compiled_objects.json").exists()


def test_compile_revalidates_current_graph_before_backend(tmp_path, monkeypatch):
    from demo_graph_lab.common import artifacts, llm
    from demo_graph_lab.graph import validate as graph_validate
    from demo_graph_lab.policy import compiler

    artifacts.write_json(tmp_path / "graph.json", _graph())
    artifacts.write_json(tmp_path / "validation.json", {"passed": True})
    monkeypatch.setattr(artifacts, "latest_run_dir", lambda task: tmp_path)
    monkeypatch.setattr(graph_validate, "validate_run_dir", lambda *_args: {
        "passed": False, "violations": ["graph changed after validation"],
    })
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: pytest.fail(
        "backend must not run when current validation fails"))

    report = artifacts.read_json(compiler.run("mini"))
    assert report["graph_validation"] == "failed"
    assert report["graph_violations"] == ["graph changed after validation"]


def test_oracle_loader_rejects_stale_compile_report(tmp_path):
    from demo_graph_lab.common import artifacts
    from demo_graph_lab.execution.cli import _load_artifacts

    graph = _graph()
    artifacts.write_json(tmp_path / "graph.json", graph)
    artifacts.write_json(tmp_path / "validation.json", {"passed": True})
    artifacts.write_json(tmp_path / "compile_report.json", {
        "graph_validation": "passed",
        "program_violations": ["bad current program"],
        "static_violations": [],
        "dryrun": {"normal": {"ok": True}, "retry_injection": {"ok": True}},
    })
    (tmp_path / "policy.py").write_text(
        "def stage_0(rt):\n    rt.release()\nSTAGES = {0: stage_0}\n")
    with pytest.raises(ValueError, match="compile report is not ready"):
        _load_artifacts(tmp_path)


@pytest.mark.parametrize(("ok", "expected"), [(True, 0), (False, 1)])
def test_oracle_episode_exit_code_matches_policy_result(
    tmp_path, monkeypatch, ok, expected,
):
    from demo_graph_lab.execution import cli

    runtime = SimpleNamespace(
        eval=SimpleNamespace(reset=lambda _task: {"ok": True}),
        probes=lambda: [],
        calls=[],
    )
    monkeypatch.setattr(cli, "_load_artifacts", lambda _run_dir, _program_dir: (
        {"task": "mini", "stages": []}, [], {},
    ))
    monkeypatch.setattr(cli, "OracleRuntime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(cli, "run_policy", lambda *_args, **_kwargs: {
        "ok": ok,
        "stages": [{"index": 0, "status": "passed" if ok else "failed"}],
    })
    args = SimpleNamespace(
        run_dir=str(tmp_path), program_dir=None, task_id="task-0",
        eval_url="unused", pipe_url="unused", arm=1, max_attempts=2,
    )

    assert cli.episode(args) == expected
    reports = list(tmp_path.glob("episode_*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["result"]["ok"] is ok
    # 默认执行的就是 run 目录自己那份产物;修复回路靠这个字段知道是哪份程序失败的。
    assert report["program_dir"] == "."
