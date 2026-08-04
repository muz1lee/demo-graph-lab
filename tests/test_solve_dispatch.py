"""Tests for typed-hole dispatch across the 86-hole fixture corpus.

覆盖范围:
  - 5 类 type 各归其求解器;含 86 洞语料在场时的 86/86 复核。
  - 歧义名字也只按声明的 type 派发:`coin_pose` / `retract_pose` → pose,
    `push_direction` → axis。
  - 未知 type → `UnsolvedHole`。

五张 graph fixture 共包含 86 个 typed holes。测试逐洞验证派发，fixture 缺失或
洞总数变化都直接失败。测试纯逻辑、离线，不触发 simulator、网络或 LLM。
"""

import json
from pathlib import Path

import pytest

from demo_graph_lab.execution.oracle_runtime import OracleRuntime
from demo_graph_lab.graph import vocab
from demo_graph_lab.graph.validate import check_item
from demo_graph_lab.selection import binding

_REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 离线实体桩:binding 的求解器只经 rt._ent(name) 取 oracle 实体态。
# 给一个覆盖测试里所有参照物名的实体表即可离线走真代码路径(不起 EvalServer)。
# --------------------------------------------------------------------------
def _entity(x=0.4, y=0.1, z=0.8, half=0.06):
    return {"pos": [x, y, z], "quat": [1.0, 0.0, 0.0, 0.0],
            "aabb": {"min": [x - half, y - half, z - 0.08],
                     "max": [x + half, y + half, z + 0.08]}}


class _StubRuntime:
    """只实现 binding 求解器需要的 _ent();任意名字都解析成同一个测试实体。"""

    def __init__(self, entities=None):
        self._entities = entities or {}

    def _ent(self, name):
        if name in self._entities:
            return self._entities[name]
        return _entity()          # 缺省实体,保证参照物总能解析(派发测试不关心具体数值)


# --------------------------------------------------------------------------
# 单洞派发:type → 句柄 kind 的期望映射。
# --------------------------------------------------------------------------
_TYPE_TO_KIND = {
    "pose_se3": "pose",
    "axis_3d": "axis",
    "point_3d": "point",
    "scalar": "scalar",
    "runtime_condition": "condition",
}


def _stage_with_constraints():
    """一个带典型约束的阶段,供参照物「从约束取」的路径被真正走到。"""
    return {
        "index": 0, "name": "insert",
        "stage_objects": {"manipulated": "tube_left", "target": "rack"},
        "constraints": [
            {"name": "region_grasp", "args": {"obj": "tube_left", "region": "upper_body"}},
            {"name": "center_align",
             "args": {"obj_a": "tube_left.center", "obj_b": "rack.hole_center"}},
            {"name": "inside", "args": {"obj_a": "tube_left", "obj_b": "rack"}},
            {"name": "axis_vertical", "args": {"axis": "tube_left.long_axis"}},
        ],
    }


def test_all_five_types_dispatch_to_correct_solver():
    """五类 type 各派发到正确求解器(句柄 kind 与 type 对应),无一落进兜底。"""
    stage = _stage_with_constraints()
    rt = _StubRuntime()
    for htype, expect_kind in _TYPE_TO_KIND.items():
        hole = {"name": f"h_{htype}", "type": htype, "solver_hint": "whatever"}
        out = binding.solve_hole(hole, stage=stage, constraints=stage["constraints"], rt=rt)
        assert out["kind"] == expect_kind, (
            f"type={htype} 应派发到 kind={expect_kind},实得 {out['kind']!r}")
        assert out["hole"] == hole["name"]


def test_dispatch_is_by_type_not_name_substring():
    """派发以 type 为准,不受名字子串影响:名字含 'axis' 但 type=pose_se3 → 仍派 pose。"""
    stage = _stage_with_constraints()
    rt = _StubRuntime()
    tricky = {"name": "axis_looking_but_pose", "type": "pose_se3"}
    out = binding.solve_hole(tricky, stage=stage, constraints=stage["constraints"], rt=rt)
    assert out["kind"] == "pose"


def test_oracle_geometry_is_not_translated_twice():
    """Oracle entity pose and AABB are already in world coordinates."""
    entity = _entity(x=1.0, y=2.0, z=3.0, half=0.5)
    rt = _StubRuntime({"rack": entity})
    stage = {
        "index": 0,
        "name": "insert",
        "stage_objects": {"manipulated": "tube", "target": "rack"},
        "constraints": [
            {"name": "center_align", "args": {"obj_a": "tube", "obj_b": "rack"}}
        ],
    }
    hole = {"name": "target", "type": "point_3d", "frame": "rack"}

    out = binding.solve_hole(hole, stage, stage["constraints"], rt)

    assert out["xyz"] == [1.0, 2.0, 3.08]
    assert out["frame"] == "world"
    assert out["requested_frame"] == "rack"


def test_axis_without_observed_pose_fails_instead_of_guessing_vertical():
    class MissingRuntime:
        def _ent(self, name):
            raise KeyError(name)

    stage = {
        "index": 0,
        "name": "align",
        "stage_objects": {"manipulated": "tube", "target": "rack"},
        "constraints": [],
    }
    hole = {"name": "tube_axis", "type": "axis_3d"}

    with pytest.raises(binding.UnsolvedHole) as error:
        binding.solve_hole(hole, stage, [], MissingRuntime())
    assert error.value.reason == "axis_unobserved"


def _stage_with_region(region_label):
    return {
        "index": 0, "name": "grasp",
        "stage_objects": {"manipulated": "tube_left", "target": "rack"},
        "constraints": [
            {"name": "region_grasp",
             "args": {"obj": "tube_left", "region": region_label}},
        ],
    }


def test_unknown_region_fails_closed_instead_of_silently_using_centroid():
    """词表外 region 不静默退化成质心(与 regions.region_preference 同规抛 ValueError)。"""
    stage = _stage_with_region("nonexistent_region")
    hole = {"name": "grasp_pose", "type": "pose_se3"}

    with pytest.raises(ValueError, match="未知 region"):
        binding.solve_hole(hole, stage, stage["constraints"], _StubRuntime())


def test_pose_without_region_constraint_still_uses_centroid():
    """没有 region 语义(非抓取洞)时仍取质心;fail-closed 只针对词表外标签。"""
    stage = {
        "index": 0, "name": "place",
        "stage_objects": {"manipulated": "tube_left", "target": "rack"},
        "constraints": [
            {"name": "center_align",
             "args": {"obj_a": "tube_left", "obj_b": "rack"}},
        ],
    }
    hole = {"name": "place_pose", "type": "pose_se3"}

    out = binding.solve_hole(hole, stage, stage["constraints"], _StubRuntime())

    assert out["region"] is None and out["region_status"] == "centroid"


# --------------------------------------------------------------------------
# fixture 中包含名字不能可靠表达类型的洞。派发必须服从声明的 type:
# pose_se3→pose,axis_3d→axis,不能从名字猜测求解器。
# --------------------------------------------------------------------------
_AMBIGUOUS_NAMES = [
    ("coin_pose", "pose_se3", "pose"),
    ("retract_pose", "pose_se3", "pose"),
    ("push_direction", "axis_3d", "axis"),
]


@pytest.mark.parametrize("name,htype,expect_kind", _AMBIGUOUS_NAMES)
def test_ambiguous_hole_names_route_by_declared_type(name, htype, expect_kind):
    stage = _stage_with_constraints()
    rt = _StubRuntime()
    hole = {"name": name, "type": htype}
    out = binding.solve_hole(hole, stage=stage, constraints=stage["constraints"], rt=rt)
    assert out["kind"] == expect_kind


# --------------------------------------------------------------------------
# 未知 type → UnsolvedHole。
# --------------------------------------------------------------------------
def test_unknown_type_raises_unsolved_hole():
    rt = _StubRuntime()
    stage = _stage_with_constraints()
    with pytest.raises(binding.UnsolvedHole):
        binding.solve_hole({"name": "weird", "type": "matrix_6x6"},
                           stage=stage, constraints=[], rt=rt)


def test_missing_type_raises_unsolved_hole():
    rt = _StubRuntime()
    stage = _stage_with_constraints()
    with pytest.raises(binding.UnsolvedHole):
        binding.solve_hole({"name": "no_type"}, stage=stage, constraints=[], rt=rt)


def test_unsolved_hole_attribution_is_binding():
    assert binding.UnsolvedHole.layer == "binding"


def test_oracle_runtime_solve_unknown_hole_raises():
    """OracleRuntime.solve 查不到 hole_name 时不得猜测。"""
    graph = {"stages": [{"index": 0, "name": "grasp", "stage_objects": {},
                         "holes": [{"name": "declared_hole", "type": "pose_se3"}],
                         "constraints": []}]}
    rt = OracleRuntime(graph)
    with pytest.raises(binding.UnsolvedHole):
        rt.solve("undeclared_hole")


# --------------------------------------------------------------------------
# 固定语料缺失或洞总数变化都必须失败。
# --------------------------------------------------------------------------
def _discover_hole_corpus():
    """Load every graph fixture directly from the fixture directory."""
    root = _REPO / "tests" / "fixtures" / "graphs"
    graphs = sorted(root.glob("*.graph.json"))
    assert len(graphs) == 5, f"expected 5 graph fixtures, found {len(graphs)}"

    holes = []
    for gp in graphs:
        g = json.loads(gp.read_text())
        for st in g.get("stages", []):
            for h in st.get("holes", []) or []:
                holes.append((gp, st, h))
    return holes


def test_full_corpus_dispatch_hits_all_holes():
    """Every committed hole dispatches by type; the fixture total stays 86."""
    corpus = _discover_hole_corpus()
    assert corpus, "constraint graph fixture corpus is empty"

    hit = 0
    bad_types = []
    for gp, st, hole in corpus:
        htype = hole.get("type")
        if htype not in vocab.HOLE_TYPES:
            bad_types.append((gp.name, hole.get("name"), htype))
            continue
        out = binding.solve_hole(hole, stage=st,
                                 constraints=st.get("constraints") or [],
                                 rt=_StubRuntime())
        assert out["kind"] == _TYPE_TO_KIND[htype], (
            f"{gp.name}:{hole.get('name')} type={htype} 误派到 {out['kind']!r}")
        hit += 1

    assert not bad_types, f"语料含未知 hole type(应 UnsolvedHole 而非静默):{bad_types}"
    assert hit == len(corpus), f"派发命中 {hit}/{len(corpus)}"
    assert len(corpus) == 86, f"expected 86 holes, found {len(corpus)}"


def test_fixture_constraint_arguments_match_vocabulary():
    errors = []
    for path in sorted((_REPO / "tests" / "fixtures" / "graphs").glob("*.graph.json")):
        graph = json.loads(path.read_text())
        for stage in graph["stages"]:
            for field in ("constraints", "acceptance"):
                for item in stage.get(field, []):
                    errors.extend(
                        f"{path.name}: {error}"
                        for error in check_item(item, stage["index"], field)
                    )
    assert errors == []
