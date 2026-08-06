"""Repair-loop contracts: the model revises its own program, offline and gated.

本文件零网络:所有 backend 回复都是 canned。断言分三类——摘要提炼器的确定性、修订版
必须走与 compile 相同的发布门、以及"原发布产物一行不动"。
"""

from __future__ import annotations

import json

import pytest

from demo_graph_lab.common import artifacts, llm
from demo_graph_lab.graph import validate as graph_validate
from demo_graph_lab.policy import compiler, repair


def _graph() -> dict:
    return {
        "task": "mini",
        "stages": [
            {
                "index": 0,
                "name": "grasp",
                "stage_objects": {"manipulated": "tube0"},
                "constraints": [],
                "acceptance": [{"name": "carry", "args": {}}],
                "holes": [
                    {"name": "tube_grasp_pose", "type": "pose_se3"},
                    {"name": "tube_axis", "type": "axis_3d"},
                ],
            },
            {
                "index": 1,
                "name": "transfer",
                "stage_objects": {"manipulated": "tube0", "target": "rack"},
                "constraints": [],
                "acceptance": [{"name": "above",
                                "args": {"obj_a": "tube0", "obj_b": "rack"}}],
                "holes": [
                    {"name": "target_point", "type": "point_3d"},
                    {"name": "contact", "type": "runtime_condition",
                     "purpose": "lower_stop"},
                ],
            },
        ],
    }


def _program(grasp_actions: list[dict]) -> dict:
    return {"stages": [
        {"index": 0, "name": "grasp", "actions": grasp_actions},
        {"index": 1, "name": "transfer", "actions": [
            {"op": "transport", "args": {"obj": {"object": "tube0"},
                                         "target": {"hole": "target_point"}}},
            {"op": "lower_until", "args": {"stop_condition": {"hole": "contact"}}},
            {"op": "release", "args": {}},
        ]},
    ]}


# 已发布的那份:抓横躺管子却没有接长轴洞——8/6 ep1 那个真实失败型的最小化。
_PUBLISHED = _program([
    {"op": "approach", "args": {"target": {"object": "tube0"},
                                "cone": "top_down"}},
    {"op": "grasp_at", "args": {"grasp_pose": {"hole": "tube_grasp_pose"}}},
])
# 修订版:同一闭集内接上 axis,没有新原语、没有数字。
_REVISED = _program([
    {"op": "approach", "args": {"target": {"object": "tube0"},
                                "cone": "top_down"}},
    {"op": "grasp_at", "args": {"grasp_pose": {"hole": "tube_grasp_pose"},
                                "axis": {"hole": "tube_axis"}}},
])
_ATTRIBUTION = "grasp_at was wired without the tube long axis, so the gripper closed on a rolling surface."


def _verdict() -> dict:
    return {
        "acceptance_hold": True,
        "constraints_hold": True,
        "n_acceptance": 1,
        "n_constraints": 0,
        "violated_midway": [],
        "vacuous_pass": 1,
        "informative_pass": 0,
        "vacuous_keys": ["carry|{}|holds=at_end"],
        "unknown_keys": [],
        "n_unknown": 0,
        "unknown_frac": 0.0,
        "needs_effect": True,
        "effect_observable": True,
        "effect_status": "FAIL",
        "effect_ok": False,
        "manipulated": "tube0",
        "manipulated_entity": "tube0_prop",
        "manip_displacement_m": 0.0004,
        "max_displacement_m": 0.0011,
        "top_mover": "gripper_left",
        "passed": False,
        "reason": "vacuous: constraints hold but world unchanged (max Δ=0.0011 m < 0.005)",
    }


def _episode(**overrides) -> dict:
    report = {
        "banner": "PRIVILEGED_ORACLE",
        "task": "mini",
        "task_id": "mini_000",
        "program_dir": ".",
        "result": {
            "ok": False,
            "failed_at": 0,
            "vacuous_pass_total": 1,
            "stages": [{"index": 0, "name": "grasp", "status": "failed",
                        "gate": _verdict()}],
        },
        "probes_before": [{"label": "tube_upright", "passed": True}],
        "probes_after": [{"label": "tube_upright", "passed": True},
                         {"label": "tube_in_rack", "passed": False}],
        "wall_sec": 41.2,
        "n_calls": 3,
        "calls": [
            {"t": 1.0, "op": "solve", "hole": "tube_grasp_pose", "kind": "pose_se3"},
            {"t": 2.0, "op": "grasp_branch", "branch": "flipped", "retried": True},
            {"t": 3.0, "op": "verify", "name": "carry", "stage": 0, "status": "PASS"},
        ],
    }
    report.update(overrides)
    return report


def _publish(tmp_path, monkeypatch, graph=None, program=None):
    """走真实 compile 路径发布一份产物;修复回路必须作用在真产物上。"""
    graph = _graph() if graph is None else graph
    program = _PUBLISHED if program is None else program
    artifacts.write_json(tmp_path / "graph.json", graph)
    artifacts.write_json(tmp_path / "objects.json", [{"id": "tube0"}, {"id": "rack"}])
    artifacts.write_json(tmp_path / "validation.json", {
        "passed": True, "violations": []})
    monkeypatch.setattr(artifacts, "latest_run_dir", lambda _task: tmp_path)
    monkeypatch.setattr(graph_validate, "validate_run_dir", lambda *_args: {
        "passed": True, "violations": []})
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: json.dumps(program))
    compiler.run("mini", model="test/model")
    assert (tmp_path / "policy.py").exists()
    return graph


def _canned(monkeypatch, *replies):
    """Serve one canned repair reply per call and record the prompts sent."""
    prompts: list[dict] = []
    queue = list(replies)

    def chat(messages, run_dir, tag, **_kwargs):
        prompts.append({"tag": tag, "prompt": messages[0]["content"]})
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return reply if isinstance(reply, str) else json.dumps(reply)

    monkeypatch.setattr(llm, "chat", chat)
    return prompts


def _reply(program=None, attribution=_ATTRIBUTION) -> dict:
    return {"attribution": attribution,
            "program": _REVISED if program is None else program}


# --------------------------------------------------------------------------
# 摘要提炼器:确定性、有界、不灌整份报告。
# --------------------------------------------------------------------------
def test_episode_summary_is_a_fixed_deterministic_distillation():
    summary = repair.summarize_episode(_episode())

    assert summary == {
        "banner": "PRIVILEGED_ORACLE",
        "task": "mini",
        "task_id": "mini_000",
        "failed_stage": {"index": 0, "name": "grasp", "status": "failed"},
        "gate": {
            "acceptance_hold": True,
            "constraints_hold": True,
            "violated_midway": [],
            "unknown_keys": [],
            "vacuous_keys": ["carry|{}|holds=at_end"],
            "needs_effect": True,
            "effect_observable": True,
            "effect_status": "FAIL",
            "manipulated": "tube0",
            "manipulated_entity": "tube0_prop",
            "top_mover": "gripper_left",
            "reason": "vacuous: constraints hold but world unchanged "
                      "(max Δ=0.0011 m < 0.005)",
        },
        "stages": [{"index": 0, "name": "grasp", "status": "failed",
                    "reason": "vacuous: constraints hold but world unchanged "
                              "(max Δ=0.0011 m < 0.005)"}],
        "probes": [{"label": "tube_upright", "before": True, "after": True},
                   {"label": "tube_in_rack", "before": None, "after": False}],
        "n_calls": 3,
        "calls_tail": [
            {"op": "solve", "hole": "tube_grasp_pose", "kind": "pose_se3"},
            {"op": "grasp_branch", "branch": "flipped", "retried": True},
            {"op": "verify", "name": "carry", "stage": 0, "status": "PASS"},
        ],
    }
    # 墙钟时间被丢掉:同一次失败不能因为时间戳变成"另一次"。
    assert repair.summarize_episode(_episode()) == summary
    slow = _episode()
    for offset, call in enumerate(slow["calls"]):
        call["t"] = 1000.0 + offset
    slow["wall_sec"] = 99.9
    assert repair.summarize_episode(slow) == summary


def test_episode_summary_keeps_only_the_tail_of_the_call_log():
    report = _episode()
    report["calls"] = [{"t": float(i), "op": "move", "i": i} for i in range(40)]

    summary = repair.summarize_episode(report)

    assert summary["n_calls"] == 40
    assert len(summary["calls_tail"]) == repair.SUMMARY_TAIL_CALLS
    assert summary["calls_tail"][-1] == {"op": "move", "i": 39}


def test_a_passing_episode_is_not_repairable():
    passing = _episode()
    passing["result"] = {"ok": True, "stages": [
        {"index": 0, "name": "grasp", "status": "passed", "gate": {"passed": True}}]}

    with pytest.raises(ValueError, match="没有失败 stage"):
        repair.summarize_episode(passing)


# --------------------------------------------------------------------------
# 合法修订:发布门全过,产物落在 repairs/r1,原产物一行不动。
# --------------------------------------------------------------------------
def test_legal_revision_publishes_into_repairs_without_touching_the_original(
    tmp_path, monkeypatch,
):
    _publish(tmp_path, monkeypatch)
    original_program = (tmp_path / "stage_program.json").read_text()
    original_policy = (tmp_path / "policy.py").read_text()
    original_report = (tmp_path / "compile_report.json").read_text()
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    _canned(monkeypatch, _reply())

    report_path = repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")

    out_dir = tmp_path / "repairs" / "r1"
    assert report_path == out_dir / "compile_report.json"
    report = artifacts.read_json(report_path)
    assert compiler.report_ready(report) is True
    assert artifacts.read_json(out_dir / "stage_program.json") == _REVISED
    assert "rt.grasp_at(grasp_pose=h0, axis=h1)" in (out_dir / "policy.py").read_text()
    assert artifacts.read_json(out_dir / "compiled_graph.json") == _graph()
    assert (out_dir / "attribution.txt").read_text().strip() == _ATTRIBUTION
    # 归因留档,但不进任何被执行的产物。
    assert "attribution" not in artifacts.read_json(out_dir / "stage_program.json")
    assert report["repair"]["attribution"] == _ATTRIBUTION
    assert report["repair"]["banner"] == "PRIVILEGED_ORACLE"

    # 原发布产物逐字节不变。
    assert (tmp_path / "stage_program.json").read_text() == original_program
    assert (tmp_path / "policy.py").read_text() == original_policy
    assert (tmp_path / "compile_report.json").read_text() == original_report

    ledger = artifacts.read_json(tmp_path / "repairs" / "repair_ledger.json")
    assert ledger["max_repairs"] == repair.MAX_REPAIRS
    assert len(ledger["repairs"]) == 1
    entry = ledger["repairs"][0]
    assert entry["index"] == 1
    assert entry["published"] is True
    assert entry["ref"] == "repairs/r1"
    assert entry["episode"] == "episode_1.json"
    assert entry["source_program"] == "."
    assert entry["violations"] == []
    assert entry["attribution"] == _ATTRIBUTION
    assert entry["episode_fingerprint"] == repair.episode_fingerprint(_episode())


def test_a_published_repair_passes_the_execution_consistency_gates(
    tmp_path, monkeypatch,
):
    from demo_graph_lab.execution.cli import _load_artifacts

    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    _canned(monkeypatch, _reply())
    repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")

    _, _, handlers = _load_artifacts(tmp_path, tmp_path / "repairs" / "r1")
    assert set(handlers) == {0, 1}

    # 同一批一致性门对修订目录同样有牙齿:改一个字节就拒绝执行。
    policy_path = tmp_path / "repairs" / "r1" / "policy.py"
    policy_path.write_text(policy_path.read_text() + "\n")
    with pytest.raises(ValueError, match="policy does not match StageProgram"):
        _load_artifacts(tmp_path, tmp_path / "repairs" / "r1")


# --------------------------------------------------------------------------
# 违规修订:一律不发布,但如实记账。
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("reply", "message"), [
    # 越出闭集原语。
    (_reply(_program([{"op": "shake", "args": {"obj": {"object": "tube0"}}}])),
     "未支持 primitive"),
    # 塞数值字面量。
    (_reply(_program([
        {"op": "approach", "args": {"target": {"object": "tube0"},
                                    "cone": "top_down"}},
        {"op": "grasp_at", "args": {"grasp_pose": {"hole": "tube_grasp_pose"},
                                    "axis": {"hole": "tube_axis"}}},
        {"op": "lift", "args": {"obj": "0.05 m"}}])),
     "禁止数值字面量"),
    # 想连 graph/约束一起改:graph 根本不在输出 schema 里。
    ({"attribution": "the acceptance condition is wrong",
      "program": dict(_REVISED, graph={"stages": []})},
     "未知字段"),
    # 归因缺席:修复必须先说清楚归因。
    ({"program": _REVISED}, "缺少字段"),
    # 什么也没改。
    (_reply(_PUBLISHED), "没有提出修复"),
])
def test_illegal_revision_is_refused_and_recorded(
    tmp_path, monkeypatch, reply, message,
):
    _publish(tmp_path, monkeypatch)
    original_policy = (tmp_path / "policy.py").read_text()
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    _canned(monkeypatch, reply)

    report = artifacts.read_json(
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model"))

    assert compiler.report_ready(report) is False
    assert any(message in violation for violation in report["program_violations"])
    out_dir = tmp_path / "repairs" / "r1"
    assert not (out_dir / "policy.py").exists()
    assert not (out_dir / "stage_program.json").exists()
    assert (tmp_path / "policy.py").read_text() == original_policy

    entry = artifacts.read_json(
        tmp_path / "repairs" / "repair_ledger.json")["repairs"][0]
    assert entry["published"] is False
    assert any(message in violation for violation in entry["violations"])
    # 被拒的尝试同样进 model_calls,校验结论可复查。
    result = artifacts.read_json(
        tmp_path / "model_calls" / "repair_r1" / "result.json")
    assert result["validator_status"] == "failed"


def test_a_revision_that_fails_the_dry_run_is_never_published(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    _canned(monkeypatch, _reply())
    monkeypatch.setattr(repair, "dry_run", lambda *_args: {
        "normal": {"ok": True}, "retry_injection": {"ok": False}})

    report = artifacts.read_json(
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model"))

    assert report["program_violations"] == []
    assert report["dryrun_error"] == "normal or retry-injection dry-run failed"
    assert not (tmp_path / "repairs" / "r1" / "policy.py").exists()
    entry = artifacts.read_json(
        tmp_path / "repairs" / "repair_ledger.json")["repairs"][0]
    assert entry["published"] is False


def test_unparseable_reply_is_recorded_as_one_used_attempt(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    _canned(monkeypatch, "no JSON here")

    report = artifacts.read_json(
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model"))

    assert any("no parseable JSON" in violation
               for violation in report["program_violations"])
    ledger = artifacts.read_json(tmp_path / "repairs" / "repair_ledger.json")
    assert len(ledger["repairs"]) == 1
    assert ledger["repairs"][0]["published"] is False


# --------------------------------------------------------------------------
# 纪律:每个 run 目录三次上限,超限拒绝且不调用 backend。
# --------------------------------------------------------------------------
def test_repair_is_capped_at_three_attempts_per_run_dir(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    prompts = _canned(monkeypatch, _reply())

    for expected in (1, 2, 3):
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")
        ledger = artifacts.read_json(tmp_path / "repairs" / "repair_ledger.json")
        assert [entry["index"] for entry in ledger["repairs"]] == list(
            range(1, expected + 1))
    assert repair.MAX_REPAIRS == 3
    assert [call["tag"] for call in prompts] == [
        "repair_r1", "repair_r2", "repair_r3"]

    with pytest.raises(ValueError, match="repair 上限已达 3 次"):
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")
    # 超限之后一次 backend 调用都没有多发生。
    assert len(prompts) == 3
    assert not (tmp_path / "repairs" / "r4").exists()


def test_repair_refuses_a_run_whose_published_artifacts_no_longer_match(
    tmp_path, monkeypatch,
):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: pytest.fail(
        "backend must not run on inconsistent artifacts"))
    policy_path = tmp_path / "policy.py"
    policy_path.write_text(policy_path.read_text() + "\n")

    with pytest.raises(ValueError, match="policy does not match StageProgram"):
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")
    assert not (tmp_path / "repairs").exists()


# --------------------------------------------------------------------------
# prompt 输入面:只给摘要与闭集,不灌整份 episode。
# --------------------------------------------------------------------------
def test_prompt_carries_the_closed_set_the_program_and_only_the_summary(
    tmp_path, monkeypatch,
):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    prompts = _canned(monkeypatch, _reply())

    repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")

    prompt = prompts[0]["prompt"]
    assert prompts[0]["tag"] == "repair_r1"
    # 原语闭集从代码渲染,prompt 里不手写第二份。
    for primitive in ("approach", "grasp_at", "lower_until", "retreat"):
        assert f"| `{primitive}` |" in prompt
    assert "hole `purpose` must be `lower_stop`" in prompt
    # 当前程序、失败判据与调用尾巴在;整份报告不在。
    assert '"op": "grasp_at"' in prompt
    assert "world unchanged" in prompt
    assert "grasp_branch" in prompt
    assert "wall_sec" not in prompt
    assert "manip_displacement_m" not in prompt
    assert '"attribution"' in prompt


def test_repair_call_is_metered_under_its_own_tag(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    _canned(monkeypatch, _reply())

    repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")

    request = artifacts.read_json(
        tmp_path / "model_calls" / "repair_r1" / "result.json")
    assert request["validator_status"] == "passed"
    # 原 compile 的调用记录没有被这次修复覆盖。
    assert artifacts.read_json(
        tmp_path / "model_calls" / "compile" / "result.json")["parsed"] == _PUBLISHED


# --------------------------------------------------------------------------
# 链式修复:第二轮读的是"真正失败的那份程序",不是原始产物。
# --------------------------------------------------------------------------
def test_a_second_repair_reads_the_program_that_actually_failed(
    tmp_path, monkeypatch,
):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    prompts = _canned(monkeypatch, _reply())
    repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")

    # 第二次 episode 跑的是 repairs/r1 的产物,失败的也是它。
    second = _episode(program_dir="repairs/r1")
    artifacts.write_json(tmp_path / "episode_2.json", second)
    third = _program([
        {"op": "approach", "args": {"target": {"object": "tube0"},
                                    "cone": "side"}},
        {"op": "grasp_at", "args": {"grasp_pose": {"hole": "tube_grasp_pose"},
                                    "axis": {"hole": "tube_axis"}}},
    ])
    monkeypatch.setattr(llm, "chat", lambda messages, run_dir, tag, **_kwargs: (
        prompts.append({"tag": tag, "prompt": messages[0]["content"]})
        or json.dumps(_reply(third))))

    report = artifacts.read_json(
        repair.run(tmp_path, tmp_path / "episode_2.json", "test/model"))

    assert compiler.report_ready(report) is True
    assert report["repair"]["source_program"] == "repairs/r1"
    assert artifacts.read_json(
        tmp_path / "repairs" / "r2" / "stage_program.json") == third
    # 第二轮看到的当前程序是 r1 的修订版,不是原始产物。
    assert '"axis"' in prompts[1]["prompt"]
    ledger = artifacts.read_json(tmp_path / "repairs" / "repair_ledger.json")
    assert [entry["source_program"] for entry in ledger["repairs"]] == [
        ".", "repairs/r1"]


def test_repair_refuses_an_episode_pointing_outside_the_run_directory(
    tmp_path, monkeypatch,
):
    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json",
                         _episode(program_dir="../other_run"))
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: pytest.fail(
        "backend must not run for an out-of-run program reference"))

    with pytest.raises(ValueError, match="只能是 run 目录自身或 repairs"):
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model")


# --------------------------------------------------------------------------
# 感知段:修订版的接线变了,感知程序在修复目录里重编译,原产物不动。
# --------------------------------------------------------------------------
def test_perception_program_is_recompiled_into_the_repair_directory(
    tmp_path, monkeypatch,
):
    graph = _graph()
    graph["stages"][0]["holes"][1].update(
        resolver="principal_axis", anchor={"object_id": "tube0", "part": "whole"})
    perception = {
        "schema": "demo_graph_lab.perception_program.v1",
        "task": "mini",
        "programs": [{
            "stage": 0,
            "chain": ["localize", "segment", "crop_points", "fit_axis"],
            "provides": [{"field": "axis", "hole": "tube_axis"}],
        }],
    }
    _publish(tmp_path, monkeypatch, graph=graph)
    # 原产物没有接线 tube_axis,所以原编译跳过了感知段。
    assert not (tmp_path / "perception_program.json").exists()
    artifacts.write_json(tmp_path / "episode_1.json", _episode())

    calls: list[dict] = []

    def chat(messages, run_dir, tag, **kwargs):
        calls.append({"tag": tag, "input_refs": kwargs["input_refs"]})
        return json.dumps(perception if tag.startswith("repair_perception")
                          else _reply())

    monkeypatch.setattr(llm, "chat", chat)
    report = artifacts.read_json(
        repair.run(tmp_path, tmp_path / "episode_1.json", "test/model"))

    assert report["perception_program"]["status"] == "published"
    assert artifacts.read_json(
        tmp_path / "repairs" / "r1" / "perception_program.json") == perception
    assert not (tmp_path / "perception_program.json").exists()
    # 感知段的调用记账落在原 run 目录的独立 tag 下,输入指向修订版程序。
    assert [call["tag"] for call in calls] == ["repair_r1", "repair_perception_r1"]
    assert "repairs/r1/stage_program.json" in calls[1]["input_refs"]
    assert (tmp_path / "model_calls" / "repair_perception_r1" / "result.json").exists()
    entry = artifacts.read_json(
        tmp_path / "repairs" / "repair_ledger.json")["repairs"][0]
    assert entry["perception_program"] == "published"


# --------------------------------------------------------------------------
# CLI 接线。
# --------------------------------------------------------------------------
def test_cli_repair_exit_code_follows_the_publish_gate(tmp_path, monkeypatch):
    from demo_graph_lab import cli

    _publish(tmp_path, monkeypatch)
    artifacts.write_json(tmp_path / "episode_1.json", _episode())
    monkeypatch.setattr(artifacts, "load_env", lambda: {})
    _canned(monkeypatch, _reply())

    assert cli.main(["repair", "--run-dir", str(tmp_path),
                     "--episode", str(tmp_path / "episode_1.json"),
                     "--model", "test/model"]) == 0

    _canned(monkeypatch, _reply(_PUBLISHED))
    assert cli.main(["repair", "--run-dir", str(tmp_path),
                     "--episode", str(tmp_path / "episode_1.json"),
                     "--model", "test/model"]) == 1


def test_cli_repair_reports_a_refusal_without_a_traceback(tmp_path, monkeypatch, capsys):
    from demo_graph_lab import cli

    _publish(tmp_path, monkeypatch)
    passing = _episode()
    passing["result"] = {"ok": True, "stages": []}
    artifacts.write_json(tmp_path / "episode_1.json", passing)
    monkeypatch.setattr(artifacts, "load_env", lambda: {})
    monkeypatch.setattr(llm, "chat", lambda *_args, **_kwargs: pytest.fail(
        "backend must not run for a passing episode"))

    assert cli.main(["repair", "--run-dir", str(tmp_path),
                     "--episode", str(tmp_path / "episode_1.json")]) == 1
    assert "REFUSED" in capsys.readouterr().out
