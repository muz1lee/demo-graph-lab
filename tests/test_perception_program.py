"""PerceptionProgram contract and fake dry-run tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo_graph_lab.graph import vocab
from demo_graph_lab.perception.fake_runtime import FakePerceptionRuntime
from demo_graph_lab.perception.program import (
    ANCHOR,
    GEOMETRY,
    OPERATORS,
    PROVIDABLE_RESOLVERS,
    ROOT_OPERATOR,
    SCHEMA,
    program_id,
    validate_perception_program,
)


_OPENING_ANCHOR = {"object_id": "fixture", "part": "hole", "instance": "center"}
_SIDE_ANCHOR = {"object_id": "fixture", "part": "hole", "instance": "side"}
_PART_ANCHOR = {"object_id": "peg", "part": "whole"}

_OPENING_CHAIN = ["localize", "segment", "fit_opening"]
_AXIS_CHAIN = ["localize", "segment", "crop_points", "fit_axis"]


def _graph() -> dict:
    return {
        "task": "mini",
        "stages": [
            {
                "index": 0,
                "name": "insertion",
                "stage_objects": {"manipulated": "peg", "target": "fixture"},
                "constraints": [],
                "acceptance": [],
                "holes": [
                    {"name": "opening_center", "type": "point_3d",
                     "resolver": "part_center", "anchor": dict(_OPENING_ANCHOR)},
                    {"name": "opening_axis", "type": "axis_3d",
                     "resolver": "part_axis", "anchor": dict(_OPENING_ANCHOR)},
                    {"name": "side_opening_center", "type": "point_3d",
                     "resolver": "part_center", "anchor": dict(_SIDE_ANCHOR)},
                    {"name": "peg_long_axis", "type": "axis_3d",
                     "resolver": "principal_axis", "anchor": dict(_PART_ANCHOR)},
                    {"name": "peg_grasp_pose", "type": "pose_se3",
                     "resolver": "grasp_candidate", "anchor": dict(_PART_ANCHOR)},
                    {"name": "retract_pose", "type": "pose_se3",
                     "resolver": "motion_derived", "anchor": dict(_OPENING_ANCHOR)},
                    {"name": "insertion_depth", "type": "scalar"},
                    {"name": "seated_condition", "type": "runtime_condition"},
                ],
            }
        ],
    }


def _doc(programs: list[dict]) -> dict:
    return {"schema": SCHEMA, "task": "mini", "programs": programs}


def _opening_program() -> dict:
    return {
        "stage": 0,
        "chain": list(_OPENING_CHAIN),
        "provides": [
            {"field": "center", "hole": "opening_center"},
            {"field": "axis", "hole": "opening_axis"},
        ],
    }


def _axis_program() -> dict:
    return {
        "stage": 0,
        "chain": list(_AXIS_CHAIN),
        "provides": [{"field": "axis", "hole": "peg_long_axis"}],
    }


# --------------------------------------------------------------------------
# 注册表:算子闭集与类型表本身就是契约,先钉住它。
# --------------------------------------------------------------------------
def test_operator_registry_is_a_closed_rooted_chain():
    assert set(OPERATORS) == {
        "localize", "segment", "fit_opening", "crop_points", "fit_axis"}
    assert [op for op, spec in OPERATORS.items() if spec["consumes"] == ANCHOR] == [
        ROOT_OPERATOR]
    produced = {spec["produces"] for spec in OPERATORS.values()}
    for name, spec in OPERATORS.items():
        # 中间产物必须真的有算子产出;字段只属于产出 GEOMETRY 的终点算子。
        assert spec["consumes"] == ANCHOR or spec["consumes"] in produced, name
        assert bool(spec["fields"]) == (spec["produces"] == GEOMETRY), name
    assert OPERATORS["fit_opening"]["fields"] == {
        "center": "point_3d", "axis": "axis_3d"}
    assert OPERATORS["fit_axis"]["fields"] == {"axis": "axis_3d"}
    assert PROVIDABLE_RESOLVERS < vocab.PERCEPTION_RESOLVERS
    assert set(PROVIDABLE_RESOLVERS) == {
        "part_center", "part_axis", "principal_axis"}
    assert vocab.MOTION_DERIVED_RESOLVER not in PROVIDABLE_RESOLVERS


def test_valid_document_passes_and_derives_program_identity():
    doc = _doc([_opening_program(), _axis_program()])
    assert validate_perception_program(doc, _graph()) == []
    assert program_id(0, 1) == "p0_1"


# --------------------------------------------------------------------------
# 校验器:每条规则一个反例。
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        # (c) 链
        (lambda doc: doc["programs"][0].update(chain=["localize", "fit_axis"]),
         "消费 POINTS"),
        (lambda doc: doc["programs"][0].update(chain=["segment", "fit_opening"]),
         "链必须以 'localize' 开头"),
        (lambda doc: doc["programs"][0].update(
            chain=["localize", "segment", "detect_grasps"]), "未支持算子"),
        (lambda doc: doc["programs"][0].update(chain=["localize", "segment"]),
         "必须产出 GEOMETRY 字段"),
        (lambda doc: doc["programs"][0].update(chain=[]), "必须是非空算子列表"),
        # (d) provides
        (lambda doc: doc["programs"][0].update(provides=[]), "必须是非空列表"),
        (lambda doc: doc["programs"][0]["provides"].append(
            {"field": "axis", "hole": "ghost_axis"}), "未声明 hole 'ghost_axis'"),
        (lambda doc: doc["programs"][0]["provides"][0].update(
            hole="opening_axis"), "与字段 'center' 的类型 'point_3d' 不一致"),
        (lambda doc: doc["programs"][0]["provides"][0].update(field="normal"),
         "不是终点算子 'fit_opening' 的产出字段"),
        (lambda doc: doc["programs"][0]["provides"][0].pop("hole"),
         "缺少字段 ['hole']"),
        # (e) anchor
        (lambda doc: doc["programs"][0]["provides"][1].update(
            field="center", hole="side_opening_center"),
         "anchor 与 'opening_center' 不一致"),
        # (f) resolver 越界
        (lambda doc: doc["programs"][1]["provides"].__setitem__(
            0, {"field": "axis", "hole": "retract_pose"}), "不由感知程序发布"),
        # (g) 重复 provide
        (lambda doc: doc["programs"][0]["provides"].append(
            {"field": "center", "hole": "opening_center"}),
         "同一程序重复 provide"),
        (lambda doc: doc["programs"].append(_opening_program()),
         "已由 programs[0] provide"),
        # 数值字面量走私与多余 key
        (lambda doc: doc["programs"][0].update(min_points=12),
         "禁止数值字面量 12"),
        (lambda doc: doc["programs"][0]["provides"][0].update(tolerance="2 mm"),
         "禁止数值字面量 '2 mm'"),
        (lambda doc: doc["programs"][0].update(name="opening"), "未知字段 ['name']"),
        (lambda doc: doc["programs"][0]["provides"][0].update(frame="world"),
         "未知字段 ['frame']"),
        (lambda doc: doc.update(notes="free text"), "未知字段 ['notes']"),
        # (a) schema / task / (b) stage
        (lambda doc: doc.update(schema="demo_graph_lab.perception_program.v2"),
         "perception_program.schema: 应为"),
        (lambda doc: doc.update(task="other"), "perception_program.task: 应为 'mini'"),
        (lambda doc: doc["programs"][0].update(stage=7), "graph 中不存在 stage 7"),
    ],
)
def test_perception_program_rejects_bad_documents(mutate, message):
    doc = _doc([_opening_program(), _axis_program()])
    mutate(doc)
    errors = validate_perception_program(doc, _graph())
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize("hole", ["peg_grasp_pose", "retract_pose"])
def test_grasp_and_motion_holes_are_outside_the_perception_dsl(hole):
    """v1 明确不服务抓取洞与执行态洞:它们不是被观测到的对象几何。"""
    graph = _graph()
    target = next(item for item in graph["stages"][0]["holes"]
                  if item["name"] == hole)
    target["type"] = "point_3d"          # 隔离出 resolver 这一条规则
    doc = _doc([{
        "stage": 0,
        "chain": list(_OPENING_CHAIN),
        "provides": [{"field": "center", "hole": hole}],
    }])
    errors = validate_perception_program(doc, graph)
    assert any("不由感知程序发布" in error for error in errors), errors


def test_provided_hole_must_carry_an_anchor():
    graph = _graph()
    graph["stages"][0]["holes"][3].pop("anchor")
    errors = validate_perception_program(_doc([_axis_program()]), graph)
    assert any("缺少 anchor" in error for error in errors), errors


def test_same_hole_name_in_two_stages_is_not_a_duplicate():
    """洞身份是 stage 内唯一的;跨 stage 同名洞由两个程序发布是合法的。"""
    graph = _graph()
    second = json.loads(json.dumps(graph["stages"][0]))
    second["index"] = 1
    graph["stages"].append(second)
    doc = _doc([_axis_program(), dict(_axis_program(), stage=1)])
    assert validate_perception_program(doc, graph) == []


def test_numeric_literal_rule_matches_the_policy_side_rule():
    """两侧各自持有实现,禁令必须逐条一致;drift 在这里失败,而不是在生产路径。"""
    from demo_graph_lab.perception.program import _is_numeric_literal as perception
    from demo_graph_lab.policy.program import _is_numeric_literal as policy

    for value in [0, 1, -3, 0.5, True, False, "0.5", " 12 ", "5 mm", "30deg",
                  "1.5 rad", "90°", "opening_a", "fit_axis", "", None,
                  ["localize", 3], ["localize"], {"field": "axis"},
                  {"field": 2}, [], {}]:
        assert perception(value) is policy(value), value

# --------------------------------------------------------------------------
# 干跑:正常、注入失败与 all-or-nothing。
# --------------------------------------------------------------------------
def test_dry_run_walks_each_chain_and_publishes_its_holes():
    doc = _doc([_opening_program(), _axis_program()])
    result = FakePerceptionRuntime(_graph()).run(doc)

    assert result["filled"] == {
        "s0.opening_center": "center",
        "s0.opening_axis": "axis",
        "s0.peg_long_axis": "axis",
    }
    assert result["unfilled"] == []
    assert [entry["op"] for entry in result["log"]] == [
        "localize", "segment", "fit_opening", "publish", "publish",
        "localize", "segment", "crop_points", "fit_axis", "publish",
    ]
    assert {entry["program"] for entry in result["log"]} == {"p0_0", "p0_1"}
    # 链的根只有 anchor,查询文本不在程序里;中间步骤只拿到不透明 handle。
    assert result["log"][0]["anchor"] == _OPENING_ANCHOR
    assert result["log"][1]["input"] == "<BBOX:p0_0.localize>"
    assert result["log"][2]["input"] == "<MASK:p0_0.segment>"
    assert result["log"][3]["handle"] == "<GEOMETRY:p0_0.fit_opening>"
    assert result["log"][8]["input"] == "<POINTS:p0_1.crop_points>"


def test_injected_failure_is_all_or_nothing_and_local_to_one_program():
    doc = _doc([_opening_program(), _axis_program()])
    runtime = FakePerceptionRuntime(_graph(), fail_at=(0, "fit_opening"))
    result = runtime.run(doc)

    # 失败程序的两个洞一个都不填;后一个程序不受影响。
    assert result["filled"] == {"s0.peg_long_axis": "axis"}
    assert result["unfilled"] == ["s0.opening_axis", "s0.opening_center"]
    assert [entry["op"] for entry in result["log"]] == [
        "localize", "segment", "fail",
        "localize", "segment", "crop_points", "fit_axis", "publish",
    ]
    assert result["log"][2] == {
        "op": "fail", "program": "p0_0", "stage": 0, "at": "fit_opening"}


def test_failure_at_the_first_operator_publishes_nothing():
    doc = _doc([_opening_program()])
    result = FakePerceptionRuntime(_graph(), fail_at=(0, "localize")).run(doc)
    assert result["filled"] == {}
    assert result["unfilled"] == ["s0.opening_axis", "s0.opening_center"]
    assert [entry["op"] for entry in result["log"]] == ["fail"]


def test_dry_run_refuses_an_unvalidated_document_and_a_dead_injection():
    broken = _doc([dict(_opening_program(), chain=["localize", "segment"])])
    with pytest.raises(ValueError, match="PerceptionProgram validation failed"):
        FakePerceptionRuntime(_graph()).run(broken)
    runtime = FakePerceptionRuntime(_graph(), fail_at=(0, "crop_points"))
    with pytest.raises(ValueError, match="injected failure never fired"):
        runtime.run(_doc([_opening_program()]))

# --------------------------------------------------------------------------
# 护栏:感知层源码不得包含任务名或物体名(与 tests/test_regions.py 同规)。
# 感知程序 DSL 是通用模块,任务专属信息只能经 graph 文档进入。
# --------------------------------------------------------------------------
@pytest.mark.parametrize("module", ["program.py", "fake_runtime.py"])
def test_no_task_or_object_names_in_source(module):
    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "demo_graph_lab"
        / "perception"
        / module
    ).read_text("utf-8").lower()
    for bad in ("insert_tube", "stack_bowl", "deposit", "push_t",
                "tube", "bowl", "coin", "rack", "slot"):
        assert bad not in src, f"{module} 出现禁用词 {bad!r}(任务名/物体名硬失败)"
