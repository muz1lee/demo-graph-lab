"""solve 按 hole["type"] 派发的单测(P0-02,交付物 3;判据出处 docs/TODO.md §1.3、EXECUTION §2.5 #1a)。

三条断言(TODO §1.3):
  ① 全量派发命中(5 类 type 各归其求解器;含 86 洞语料在场时的 86/86 复核)。
  ② `coin_pose` / `retract_pose` / `push_direction` 三个已知误派归位——
     旧 `solve()` 靠名字子串匹配,把这三个洞误派进 runtime_condition 兜底;
     binding 按 type 派发后,pose_se3→pose、axis_3d→axis,不再兜底。
  ③ 未知 type → `UnsolvedHole`。

**86 洞语料说明(诚实标注)**:TODO §1.3 的「86 洞 / 误派 30 / 兜底 28 / 非 world frame 43」
来自工作机 `harness/runs/` 的 19 个 run 目录(EXECUTION §2.5),该语料**未随本仓提交**,
本 worktree 内不存在。因此 86/86 全量复核走**语料自动发现**:发现 graph.json 语料则逐洞断言
派发命中并核对总数,发现不到则以清晰信息 skip(绝不伪造 86 个洞来假装通过)。语料就位后
(工作机上跑,或把 graphs.lock/runs 带进仓)此断言自动生效。

风格对齐 tests/test_harness_units.py:纯逻辑、离线、不触 sim/网络/LLM。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import binding, vocab
from harness.kwadapter import KWRuntime

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
    """① 五类 type 各派发到正确求解器(句柄 kind 与 type 对应),无一落进兜底。"""
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


# --------------------------------------------------------------------------
# ② 三个已知误派归位。这三个洞名在旧子串匹配 solve() 下的实际去向:
#   - coin_pose      : "pose" in n 但 "grasp" not in n,且无 place/target 关键词 → else 兜底(condition)
#   - retract_pose   : 同理无 grasp、无 target 关键词 → else 兜底(condition)
#   - push_direction : 无 grasp/pose、无 "axis"、无点/标量关键词 → else 兜底(condition)
# 按 type 派发后:pose_se3→pose,axis_3d→axis,全部脱离兜底。
# (洞名与 type 出处:goldset rationale 与 docs/OVERVIEW.md 的 rt.solve("retract_pose"))
# --------------------------------------------------------------------------
_KNOWN_MISDISPATCH = [
    ("coin_pose", "pose_se3", "pose"),
    ("retract_pose", "pose_se3", "pose"),
    ("push_direction", "axis_3d", "axis"),
]


@pytest.mark.parametrize("name,htype,expect_kind", _KNOWN_MISDISPATCH)
def test_known_misdispatch_holes_now_route_by_type(name, htype, expect_kind):
    stage = _stage_with_constraints()
    rt = _StubRuntime()
    hole = {"name": name, "type": htype}
    out = binding.solve_hole(hole, stage=stage, constraints=stage["constraints"], rt=rt)
    assert out["kind"] == expect_kind, (
        f"{name}(type={htype})应派发到 {expect_kind},实得 {out['kind']!r}——"
        "旧子串匹配会把它兜进 runtime_condition(condition)。")
    assert out["kind"] != "condition", f"{name} 仍落进 runtime_condition 兜底 = 误派未归位"


def _old_substring_dispatch_kind(name):
    """复刻旧 solve() 的名字子串匹配去向,用于证明这三个洞在旧逻辑下确实落进兜底。"""
    n = name.lower()
    if "grasp" in n and "pose" in n:
        return "pose"
    if "axis" in n:
        return "axis"
    if any(k in n for k in ("hole", "slot", "place", "target", "insert_point", "center")):
        return "point"
    if any(k in n for k in ("depth", "height", "clearance", "distance")):
        return "scalar"
    return "condition"


@pytest.mark.parametrize("name,htype,expect_kind", _KNOWN_MISDISPATCH)
def test_misdispatch_baseline_was_fallback(name, htype, expect_kind):
    """归位证据:这三个洞在旧子串匹配下**确实**落进 condition 兜底(与新 type 派发对照)。"""
    assert _old_substring_dispatch_kind(name) == "condition", (
        f"{name} 在旧子串匹配下应落 condition(误派);若不成立则误派前提失效")


# --------------------------------------------------------------------------
# ③ 未知 type → UnsolvedHole。
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


def test_unsolved_hole_attribution_is_l2_bind():
    """归因字段固定 L2_bind(TODO §1.2 C-2)。"""
    assert binding.UnsolvedHole.layer == "L2_bind"


def test_kwruntime_solve_unknown_hole_raises():
    """C-2:KWRuntime.solve 查不到 hole_name → UnsolvedHole,不回退当前阶段猜。"""
    graph = {"stages": [{"index": 0, "name": "grasp", "stage_objects": {},
                         "holes": [{"name": "declared_hole", "type": "pose_se3"}],
                         "constraints": []}]}
    rt = KWRuntime(graph)
    with pytest.raises(binding.UnsolvedHole):
        rt.solve("undeclared_hole")


# --------------------------------------------------------------------------
# ① 全量 86/86:语料自动发现。语料缺席则 skip(不伪造),就位则逐洞断言命中 + 核对总数。
# --------------------------------------------------------------------------
def _discover_hole_corpus():
    """收集所有带 stages[].holes[] 的 graph.json。优先 experiments/causal/graphs.lock 钉定的
    5 份;否则扫 harness/runs/。返回 [(path, hole_dict), ...]。语料不在本仓时返回 []。"""
    graphs = []
    lock = _REPO / "experiments" / "causal" / "graphs.lock"
    if lock.exists():
        try:
            entries = json.loads(lock.read_text())
            paths = entries.get("graphs", entries) if isinstance(entries, dict) else entries
            for item in paths:
                p = item.get("path") if isinstance(item, dict) else item
                gp = (_REPO / p) if not Path(p).is_absolute() else Path(p)
                if gp.exists():
                    graphs.append(gp)
        except (ValueError, KeyError, TypeError):
            pass
    if not graphs:
        runs = _REPO / "harness" / "runs"
        if runs.is_dir():
            graphs = sorted(runs.rglob("graph.json"))

    holes = []
    for gp in graphs:
        try:
            g = json.loads(gp.read_text())
        except ValueError:
            continue
        for st in g.get("stages", []):
            for h in st.get("holes", []) or []:
                holes.append((gp, st, h))
    return holes


def test_full_corpus_dispatch_hits_all_holes():
    """① 语料全量:每个洞按其 type 派发命中(不落未知 type);总数与 TODO 记账 86 对照。

    语料缺席(本 worktree 无 harness/runs/,无 graphs.lock)→ skip 并说明,不伪造。
    """
    corpus = _discover_hole_corpus()
    if not corpus:
        pytest.skip(
            "86 洞语料不在本仓:harness/runs/ 与 experiments/causal/graphs.lock 均不存在"
            "(语料在工作机的 19 个 run 目录,见 EXECUTION §2.5)。"
            "把 graphs.lock 或 runs/ 带进仓后此断言自动全量核验 86/86。")

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
    # TODO §1.3 记账为 86;语料就位后若总数不符,是语料/记账口径漂移,需人核对(不放宽此断言)。
    assert len(corpus) == 86, (
        f"语料洞总数 {len(corpus)} ≠ TODO §1.3 记账 86——"
        "语料版本或 graphs.lock 选取与记账时不一致,请核对 EXECUTION §2.5 的 sha256 钉定。")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
