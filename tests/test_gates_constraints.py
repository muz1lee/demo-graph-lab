"""Gate 必须消费 stage["constraints"]。

核心断言:造一个 constraints 全违反、但 acceptance 全过的 stage,
gate 必须判 passed=False。

同时覆盖字段级可分(acceptance_hold / constraints_hold 独立)、throughout 的 entry/exit
双查与 violated_midway、at_end 只查出口、无约束阶段的空合取恒真。纯逻辑,无 cv2/网络。
"""

from demo_graph_lab.evaluation import gates


class FakeRT:
    """按约束名给定 verify 结果;可为 pre/exit 探针分别指定(测 throughout 中途违反)。

    verdicts     : {constraint_name: bool} —— 默认(出口)判定。
    pre_verdicts : {constraint_name: bool} —— 入口探针(_probe=="pre")判定;缺省回退 verdicts。
    positions    : 物体位置(供 effect 检查);默认给一个会动的物体避免 effect 干扰。
    """

    def __init__(self, verdicts, pre_verdicts=None, positions=None):
        self.verdicts = verdicts
        self.pre_verdicts = pre_verdicts or {}
        self.positions = positions or {}

    def _entities(self, max_age_s=0.0):
        return {k: {"pos": v} for k, v in self.positions.items()}

    def verify(self, c):
        name = c.get("name")
        if c.get("_probe") == "pre" and name in self.pre_verdicts:
            return self.pre_verdicts[name]
        return self.verdicts.get(name, True)


def _run(rt, stage, move=True):
    """跑一遍 snapshot→(可选移动物体)→evaluate,返回 verdict。"""
    entry = gates.snapshot(rt, stage)
    if move:
        for k in list(rt.positions):
            rt.positions[k] = [rt.positions[k][0], rt.positions[k][1],
                               rt.positions[k][2] + 0.10]
    return gates.evaluate(rt, stage, entry)


# ==========================================================================
# constraints 全违反 + acceptance 全过 → passed=False。
# ==========================================================================
def test_constraints_violated_acceptance_ok_fails():
    stage = {
        "index": 1, "name": "place",
        "stage_objects": {"manipulated": "bowl0", "target": "table"},
        "acceptance": [{"name": "above", "args": {"obj_a": "bowl0", "obj_b": "table"}}],
        "constraints": [
            {"name": "center_align", "args": {"obj_a": "bowl0", "obj_b": "table"},
             "holds": "at_end"},
            {"name": "inside", "args": {"obj_a": "bowl0", "obj_b": "box"},
             "holds": "at_end"},
        ],
    }
    rt = FakeRT(verdicts={"above": True, "center_align": False, "inside": False},
                positions={"bowl0": [0.5, 0.0, 0.79]})
    v = _run(rt, stage)
    assert v["acceptance_hold"] is True         # 验收全过
    assert v["constraints_hold"] is False       # 约束全违反
    assert v["passed"] is False                 # constraints 必须参与最终判定
    assert "constraints failed" in v["reason"]


# ==========================================================================
# 字段级可分:反过来 constraints 全过 + acceptance 全违反 → 也 False,reason 指向 acceptance
# ==========================================================================
def test_acceptance_violated_constraints_ok_fails():
    stage = {
        "index": 1, "name": "place",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {"obj_a": "bowl0", "obj_b": "table"}}],
        "constraints": [{"name": "center_align",
                         "args": {"obj_a": "bowl0", "obj_b": "table"}, "holds": "at_end"}],
    }
    rt = FakeRT(verdicts={"above": False, "center_align": True},
                positions={"bowl0": [0.5, 0.0, 0.79]})
    v = _run(rt, stage)
    assert v["acceptance_hold"] is False
    assert v["constraints_hold"] is True
    assert v["passed"] is False
    assert "acceptance failed" in v["reason"]


# ==========================================================================
# 两者全过 + 有效果 → passed=True
# ==========================================================================
def test_both_ok_with_effect_passes():
    stage = {
        "index": 1, "name": "lift",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {"obj_a": "bowl0", "obj_b": "table"}}],
        "constraints": [{"name": "center_align",
                         "args": {"obj_a": "bowl0", "obj_b": "table"}, "holds": "at_end"}],
    }
    rt = FakeRT(verdicts={"above": True, "center_align": True},
                positions={"bowl0": [0.5, 0.0, 0.79]})
    v = _run(rt, stage)   # bowl0 会被抬 0.10m,满足 effect
    assert v["acceptance_hold"] and v["constraints_hold"] and v["passed"]
    assert v["n_constraints"] == 1


# ==========================================================================
# 无 constraints 的阶段:空合取恒真(无待检约束 ≠ 失败)。
# ==========================================================================
def test_no_constraints_key_is_vacuously_true():
    stage = {
        "index": 0, "name": "lift",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {"obj_a": "bowl0", "obj_b": "table"}}],
    }
    rt = FakeRT(verdicts={"above": True}, positions={"bowl0": [0.5, 0.0, 0.79]})
    v = _run(rt, stage)
    assert v["constraints_hold"] is True   # 无约束 → 空合取真
    assert v["n_constraints"] == 0
    assert v["passed"] is True


def test_strict_gate_rejects_unobservable_effect() -> None:
    stage = {
        "index": 0,
        "name": "lift",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {}}],
    }
    runtime = FakeRT(verdicts={"above": True}, positions={})
    entry = gates.snapshot(runtime, stage)

    strict = gates.evaluate(runtime, stage, entry, strict=True)
    relaxed = gates.evaluate(runtime, stage, entry, strict=False)

    assert strict["effect_status"] == gates.UNKNOWN
    assert strict["passed"] is False
    assert relaxed["passed"] is True


# ==========================================================================
# throughout:入口成立、出口违反 → violated_midway 记账 + constraints_hold=False
# ==========================================================================
def test_throughout_violated_midway_recorded():
    stage = {
        "index": 1, "name": "transport",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {"obj_a": "bowl0", "obj_b": "table"}}],
        "constraints": [{"name": "carry", "args": {"relation": "held"},
                         "holds": "throughout"}],
    }
    # 入口 carry=True,出口 carry=False
    rt = FakeRT(verdicts={"above": True, "carry": False},
                pre_verdicts={"carry": True},
                positions={"bowl0": [0.5, 0.0, 0.79]})
    v = _run(rt, stage)
    assert v["constraints_hold"] is False
    key = [k for k in v["violated_midway"]]
    assert len(key) == 1 and "carry" in key[0]
    assert v["passed"] is False


# ==========================================================================
# throughout:入口出口都成立 → 不记 midway,约束成立
# ==========================================================================
def test_throughout_held_all_along_ok():
    stage = {
        "index": 1, "name": "transport",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {"obj_a": "bowl0", "obj_b": "table"}}],
        "constraints": [{"name": "carry", "args": {"relation": "held"},
                         "holds": "throughout"}],
    }
    rt = FakeRT(verdicts={"above": True, "carry": True},
                pre_verdicts={"carry": True},
                positions={"bowl0": [0.5, 0.0, 0.79]})
    v = _run(rt, stage)
    assert v["constraints_hold"] is True
    assert v["violated_midway"] == []
    assert v["passed"] is True


# ==========================================================================
# at_end(及缺省 holds):只在出口查,入口态不影响 midway 记账
# ==========================================================================
def test_at_end_only_checks_exit():
    stage = {
        "index": 1, "name": "place",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {"obj_a": "bowl0", "obj_b": "table"}}],
        # 一条 at_end、一条无 holds(缺省按 at_end 处理:只查出口、不参与 midway)
        "constraints": [
            {"name": "center_align", "args": {"obj_a": "bowl0", "obj_b": "table"},
             "holds": "at_end"},
            {"name": "inside", "args": {"obj_a": "bowl0", "obj_b": "box"}},
        ],
    }
    # 入口两者皆假(at_end 不该因此记 midway),出口皆真 → 通过
    rt = FakeRT(verdicts={"above": True, "center_align": True, "inside": True},
                pre_verdicts={"center_align": False, "inside": False},
                positions={"bowl0": [0.5, 0.0, 0.79]})
    v = _run(rt, stage)
    assert v["constraints_hold"] is True
    assert v["violated_midway"] == []
    assert v["passed"] is True
