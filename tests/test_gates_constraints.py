"""Gate 必须消费 stage["constraints"]。

核心断言:造一个 constraints 全违反、但 acceptance 全过的 stage,
gate 必须判 passed=False。

同时覆盖字段级可分(acceptance_hold / constraints_hold 独立)、throughout 的 entry/exit
双查与 violated_midway、at_end 只查出口、无约束阶段的空合取恒真。纯逻辑,无 cv2/网络。
"""

from types import SimpleNamespace

import pytest

from demo_graph_lab.evaluation import gates, predicates


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
        key = (name, c.get("holds", "at_end"))
        source = self.pre_verdicts if c.get("_probe") == "pre" else self.verdicts
        if key in source:
            return source[key]
        if name in source:
            return source[name]
        return self.verdicts.get(key, self.verdicts.get(name, True))

    def verify3(self, c):
        value = self.verify(c)
        if value in (gates.PASS, gates.FAIL, gates.UNKNOWN):
            status = value
        else:
            status = gates.PASS if value else gates.FAIL
        return SimpleNamespace(status=status)


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


def test_pass_plus_unknown_acceptance_is_undetermined():
    """普通 UNKNOWN(这里用缺 ctx 的 region_grasp)照旧阻塞:PASS + UNKNOWN → 判不了。"""
    stage = {
        "index": 0, "name": "release", "stage_objects": {},
        "acceptance": [{"name": "above", "args": {}},
                       {"name": "region_grasp", "args": {}}],
        "constraints": [],
    }
    verdict = _run(FakeRT({"above": gates.PASS, "region_grasp": gates.UNKNOWN}), stage)
    assert verdict["acceptance_hold"] is None
    assert verdict["passed"] is False
    assert verdict["excluded_uncheckable_keys"] == []   # 白名单外,不豁免


def test_pass_plus_unknown_constraint_is_undetermined():
    stage = {
        "index": 0, "name": "release", "stage_objects": {},
        "acceptance": [{"name": "above", "args": {}}],
        "constraints": [
            {"name": "center_align", "args": {}, "holds": "at_end"},
            {"name": "region_grasp", "args": {}, "holds": "at_end"},
        ],
    }
    verdict = _run(FakeRT({
        "above": gates.PASS,
        "center_align": gates.PASS,
        "region_grasp": gates.UNKNOWN,
    }), stage)
    assert verdict["constraints_hold"] is None
    assert verdict["passed"] is False
    assert verdict["excluded_uncheckable_keys"] == []


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


def test_throughout_unknown_at_entry_cannot_pass_at_exit():
    stage = {
        "index": 1, "name": "release", "stage_objects": {},
        "acceptance": [{"name": "above", "args": {}}],
        "constraints": [{"name": "region_grasp", "args": {}, "holds": "throughout"}],
    }
    runtime = FakeRT(
        verdicts={"above": gates.PASS, "region_grasp": gates.PASS},
        pre_verdicts={"region_grasp": gates.UNKNOWN},
    )
    verdict = _run(runtime, stage)
    assert verdict["constraints_hold"] is None
    assert verdict["passed"] is False


def test_same_predicate_with_different_holds_cannot_overwrite_unknown():
    stage = {
        "index": 1, "name": "release", "stage_objects": {},
        "acceptance": [{"name": "above", "args": {}}],
        "constraints": [
            {"name": "region_grasp", "args": {"obj": "tube0", "region": "upper_body"},
             "holds": "throughout"},
            {"name": "region_grasp", "args": {"obj": "tube0", "region": "upper_body"},
             "holds": "at_end"},
        ],
    }
    runtime = FakeRT(
        verdicts={
            "above": gates.PASS,
            ("region_grasp", "throughout"): gates.PASS,
            ("region_grasp", "at_end"): gates.PASS,
        },
        pre_verdicts={("region_grasp", "throughout"): gates.UNKNOWN},
    )
    verdict = _run(runtime, stage)
    assert verdict["constraints_hold"] is None
    assert verdict["n_constraints"] == 2
    assert verdict["passed"] is False


def test_throughout_acceptance_unknown_at_entry_cannot_pass_at_exit():
    stage = {
        "index": 1, "name": "release", "stage_objects": {},
        "acceptance": [
            {"name": "carry", "args": {"relation": "tube0_in_gripper"},
             "holds": "throughout"},
        ],
        "constraints": [],
    }
    runtime = FakeRT(
        verdicts={("carry", "throughout"): gates.PASS},
        pre_verdicts={("carry", "throughout"): gates.UNKNOWN},
    )
    verdict = _run(runtime, stage)
    assert verdict["acceptance_hold"] is None
    assert verdict["passed"] is False


def test_unrelated_object_motion_cannot_satisfy_manipulated_effect():
    stage = {
        "index": 1, "name": "lift",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "above", "args": {}}],
        "constraints": [],
    }
    verdict = _run(FakeRT(
        verdicts={"above": gates.PASS},
        positions={"bowl01_prop": [0.0, 0.0, 0.0]},
    ), stage)
    assert verdict["max_displacement_m"] > gates.MIN_DISPLACEMENT_M
    assert verdict["manip_displacement_m"] is None
    assert verdict["effect_status"] == gates.UNKNOWN
    assert verdict["passed"] is False


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


# ==========================================================================
# manipulated 名 → 实体键映射:注入解析后位移才可观测。
# 实测(8/6 ep1):图对象名 tube_left,实体字典键 tube0_prop,位移检查拿前者查后者
# 永远查不到 → effect_status=UNKNOWN → 任何 effectful stage 结构性过不了。
# ==========================================================================
def _tube_stage():
    return {
        "index": 1, "name": "lift",
        "stage_objects": {"manipulated": "tube_left"},
        "acceptance": [{"name": "above", "args": {}}],
        "constraints": [],
    }


def _tube_rt():
    return FakeRT(verdicts={"above": gates.PASS},
                  positions={"tube0_prop": [0.4, 0.2, 0.80],
                             "tube1_prop": [0.4, 0.0, 0.80]})


def _run_with_resolver(rt, stage, resolve_object):
    entry = gates.snapshot(rt, stage)
    rt.positions["tube0_prop"] = [0.4, 0.2, 0.92]      # 只有被操作的那根动了
    return gates.evaluate(rt, stage, entry, resolve_object=resolve_object)


def test_unmapped_manipulated_name_stays_unknown():
    """没有映射时维持现状:图名查不到实体 → 不可观测 → UNKNOWN,不放松。"""
    verdict = _run_with_resolver(_tube_rt(), _tube_stage(), None)
    assert verdict["manipulated_entity"] is None
    assert verdict["manip_displacement_m"] is None
    assert verdict["effect_status"] == gates.UNKNOWN
    assert verdict["passed"] is False


def test_injected_resolver_makes_manipulated_displacement_observable():
    """注入 name→entity 解析后位移可观测,effect 判定才真的生效。"""
    verdict = _run_with_resolver(_tube_rt(), _tube_stage(),
                                 {"tube_left": "tube0_prop"}.get)
    assert verdict["manipulated_entity"] == "tube0_prop"
    assert verdict["manip_displacement_m"] == pytest.approx(0.12, abs=1e-9)
    assert verdict["effect_status"] == gates.PASS
    assert verdict["passed"] is True


def test_injected_resolver_does_not_credit_another_object_motion():
    """解析到的是没动的那根 → FAIL(位移检查仍有牙齿,不是"有映射就过")。"""
    verdict = _run_with_resolver(_tube_rt(), _tube_stage(),
                                 {"tube_left": "tube1_prop"}.get)
    assert verdict["manipulated_entity"] == "tube1_prop"
    assert verdict["effect_status"] == gates.FAIL
    assert verdict["passed"] is False


def test_resolver_failure_falls_back_and_stays_fail_closed():
    """解析抛异常(如对象名有歧义)→ 当作拿不到映射,退回前缀匹配,仍 UNKNOWN。"""
    def _raises(_name):
        raise RuntimeError("ambiguous_object_reference")

    verdict = _run_with_resolver(_tube_rt(), _tube_stage(), _raises)
    assert verdict["manipulated_entity"] is None
    assert verdict["effect_status"] == gates.UNKNOWN


def test_resolver_pointing_outside_the_position_table_is_ignored():
    """映射指向位移表里没有的键 → 不认,退回前缀匹配,不凭空造一个位移。"""
    verdict = _run_with_resolver(_tube_rt(), _tube_stage(),
                                 {"tube_left": "ghost_prop"}.get)
    assert verdict["manipulated_entity"] is None
    assert verdict["effect_status"] == gates.UNKNOWN


def test_prefix_matching_still_works_without_a_resolver():
    """既有的 id/前缀匹配不受影响:bowl0 → bowl0_prop 仍可观测。"""
    stage = {"index": 1, "name": "lift", "stage_objects": {"manipulated": "bowl0"},
             "acceptance": [{"name": "above", "args": {}}], "constraints": []}
    verdict = _run(FakeRT(verdicts={"above": gates.PASS},
                          positions={"bowl0_prop": [0.5, 0.0, 0.79]}), stage)
    assert verdict["manipulated_entity"] == "bowl0_prop"
    assert verdict["effect_status"] == gates.PASS


# ==========================================================================
# 结构性不可查项(carry / order)不再死锁整条判定。
# 实测(8/6 ep1+ep2):acceptance 里的 carry 在 UNCHECKABLE_IN_RUNTIME 里,三值合取
# UNKNOWN→None→passed 恒非 True——只要 acceptance 含 carry,任何 stage 永远过不了。
# 现在这一类**从合取中排除但完整记账**;豁免面用 predicates 的白名单钉死。
# ==========================================================================
def _carry_stage(extra_acceptance=(), constraints=()):
    return {
        "index": 1, "name": "lift",
        "stage_objects": {"manipulated": "bowl0"},
        "acceptance": [{"name": "carry", "args": {"relation": "bowl0_in_gripper"}},
                       *extra_acceptance],
        "constraints": list(constraints),
    }


def test_uncheckable_acceptance_is_excluded_and_accounted():
    """acceptance 含 carry(UNKNOWN)、其余全 PASS → passed=True,且 carry 被点名记账。"""
    stage = _carry_stage(extra_acceptance=[{"name": "above", "args": {}}])
    rt = FakeRT(verdicts={"above": gates.PASS, "carry": gates.UNKNOWN},
                positions={"bowl0_prop": [0.5, 0.0, 0.79]})
    verdict = _run(rt, stage)

    assert verdict["acceptance_hold"] is True
    assert verdict["passed"] is True
    excluded = verdict["excluded_uncheckable_keys"]
    assert len(excluded) == 1 and "carry" in excluded[0]
    assert verdict["n_excluded_uncheckable"] == 1
    # 记账要完整:被豁免不等于被查过,它仍然留在 unknown_keys 里。
    assert excluded[0] in verdict["unknown_keys"]
    assert verdict["n_unknown"] == 1


def test_uncheckable_constraint_is_excluded_from_the_conjunction():
    stage = _carry_stage(
        extra_acceptance=[{"name": "above", "args": {}}],
        constraints=[{"name": "carry", "args": {}, "holds": "throughout"},
                     {"name": "center_align", "args": {}, "holds": "at_end"}])
    rt = FakeRT(verdicts={"above": gates.PASS, "center_align": gates.PASS,
                          "carry": gates.UNKNOWN},
                positions={"bowl0_prop": [0.5, 0.0, 0.79]})
    verdict = _run(rt, stage)

    assert verdict["constraints_hold"] is True
    assert verdict["passed"] is True
    assert verdict["n_constraints"] == 2          # 记账不缩水:两条都还在
    assert verdict["n_excluded_uncheckable"] == 2  # acceptance 与 constraint 各一条


def test_ordinary_unknown_still_blocks_next_to_an_excluded_one():
    """白名单外的 UNKNOWN 照旧阻塞,不因为同阶段有豁免项就一起放行。"""
    stage = _carry_stage(extra_acceptance=[{"name": "region_grasp", "args": {}}])
    rt = FakeRT(verdicts={"region_grasp": gates.UNKNOWN, "carry": gates.UNKNOWN},
                positions={"bowl0_prop": [0.5, 0.0, 0.79]})
    verdict = _run(rt, stage)

    assert verdict["acceptance_hold"] is None
    assert verdict["passed"] is False
    assert verdict["n_excluded_uncheckable"] == 1   # 只豁免了 carry
    assert "region_grasp" in verdict["reason"]      # 阻塞原因点名真正挡路的那条


def test_only_uncheckable_acceptance_does_not_pass():
    """全部 acceptance 都被豁免 → 合取里没有任何证据 → 仍非 True(豁免不凭空造 True)。"""
    rt = FakeRT(verdicts={"carry": gates.UNKNOWN},
                positions={"bowl0_prop": [0.5, 0.0, 0.79]})
    verdict = _run(rt, _carry_stage())

    assert verdict["acceptance_hold"] is None
    assert verdict["passed"] is False
    assert verdict["n_excluded_uncheckable"] == 1


def test_uncheckable_that_the_runtime_can_answer_is_not_excluded():
    """豁免只吃 UNKNOWN:runtime 真能判 carry 时,FAIL 照旧否决。"""
    stage = _carry_stage(extra_acceptance=[{"name": "above", "args": {}}])
    rt = FakeRT(verdicts={"above": gates.PASS, "carry": gates.FAIL},
                positions={"bowl0_prop": [0.5, 0.0, 0.79]})
    verdict = _run(rt, stage)

    assert verdict["acceptance_hold"] is False
    assert verdict["passed"] is False
    assert verdict["excluded_uncheckable_keys"] == []


def test_exemption_whitelist_is_pinned_to_the_predicate_module():
    """护栏:豁免面只有这两个名字,而且与 predicates 同一个真源。

    往白名单里塞第三个名字 = 给 gate 开后门(那条约束就再也挡不住任何 stage),
    这条断言逼将来的人先来改测试、把这个决定摆到台面上。
    """
    assert gates.EXCLUDABLE_UNCHECKABLE is predicates.UNCHECKABLE_IN_RUNTIME
    assert predicates.UNCHECKABLE_IN_RUNTIME == {"carry", "order"}


def test_predicate_outside_the_whitelist_cannot_be_exempted():
    """行为侧护栏:名字不在白名单里,就算 UNKNOWN 也不许被排除。"""
    for name in ("region_grasp", "approach_direction", "inside", "frobnicate"):
        stage = {"index": 0, "name": "release", "stage_objects": {},
                 "acceptance": [{"name": name, "args": {}}], "constraints": []}
        verdict = _run(FakeRT({name: gates.UNKNOWN}), stage)
        assert verdict["excluded_uncheckable_keys"] == [], name
        assert verdict["passed"] is False, name


# ==========================================================================
# ctx 接线:region_grasp 要 grasp_point、approach_direction 要 approach_dir。
# gate 不传 ctx 时这两条**永远** UNKNOWN(几何实现其实是完整的),ep1/ep2 两集里
# 就是这样一直判不了;现在由 runner 从 runtime 侧取值经 gate 传进谓词。
# ==========================================================================
class _PredicateRT:
    """verify3 直通真谓词的最小 runtime,用来钉住 ctx 确实到达了谓词。"""

    def __init__(self, entities):
        self.entities = entities
        self.positions = {"tube0_prop": [0.4, 0.2, 0.80]}

    def _entities(self, max_age_s=0.0):
        return {k: {"pos": list(v)} for k, v in self.positions.items()}

    def verify3(self, constraint, **ctx):
        return predicates.check(constraint, self.entities, **ctx)


_TUBE = {"tube0": {"pos": [0.4, 0.2, 0.8],
                   "aabb": {"min": [0.0, 0.0, 0.0], "max": [0.1, 0.1, 1.0]}}}


def _ctx_stage(constraint):
    return {"index": 0, "name": "release", "stage_objects": {},
            "acceptance": [constraint], "constraints": []}


def _evaluate_with_ctx(stage, ctx):
    rt = _PredicateRT(_TUBE)
    entry = gates.snapshot(rt, stage)
    return gates.evaluate(rt, stage, entry, ctx=ctx)


def test_region_grasp_is_decided_once_the_grasp_point_reaches_the_predicate():
    stage = _ctx_stage({"name": "region_grasp",
                        "args": {"obj": "tube0", "region": "upper_body"}})
    # 抓在上段 → PASS;抓在下段 → FAIL。两侧都要真的判出来,不是一律 UNKNOWN。
    assert _evaluate_with_ctx(stage, {"grasp_point": [0.05, 0.05, 0.9]})[
        "acceptance_hold"] is True
    assert _evaluate_with_ctx(stage, {"grasp_point": [0.05, 0.05, 0.1]})[
        "acceptance_hold"] is False


def test_approach_direction_is_decided_once_the_direction_reaches_the_predicate():
    stage = _ctx_stage({"name": "approach_direction",
                        "args": {"cone": "top_down", "target": "tube0"}})
    assert _evaluate_with_ctx(stage, {"approach_dir": [0, 0, -1]})[
        "acceptance_hold"] is True
    assert _evaluate_with_ctx(stage, {"approach_dir": [1, 0, 0]})[
        "acceptance_hold"] is False


def test_without_ctx_the_two_predicates_stay_unknown():
    """没有 ctx 时与现状逐位一致:两条谓词仍是 UNKNOWN,判定仍是 None。"""
    for constraint, reason in (
        ({"name": "region_grasp", "args": {"obj": "tube0", "region": "upper_body"}},
         "no_grasp_point"),
        ({"name": "approach_direction", "args": {"cone": "top_down"}},
         "no_approach_dir"),
    ):
        stage = _ctx_stage(constraint)
        none_ctx = _evaluate_with_ctx(stage, None)
        empty_ctx = _evaluate_with_ctx(stage, {})
        assert none_ctx == empty_ctx                  # 空 ctx 不改变任何一位
        assert none_ctx["acceptance_hold"] is None
        assert none_ctx["n_unknown"] == 1
        assert none_ctx["excluded_uncheckable_keys"] == []   # 缺输入 ≠ 结构性不可查
        assert predicates.check(constraint, _TUBE).reason == reason


def test_runtime_that_does_not_accept_ctx_keeps_the_current_behaviour():
    """老 runtime 的 verify3 不收 ctx → 退回不带 ctx 的调用,逐位与现状一致。"""
    stage = _ctx_stage({"name": "above", "args": {}})
    rt = FakeRT(verdicts={"above": gates.PASS},
                positions={"bowl0_prop": [0.5, 0.0, 0.79]})   # FakeRT.verify3 无 **ctx
    entry = gates.snapshot(rt, stage)
    assert gates.evaluate(rt, stage, entry, ctx={"grasp_point": [0, 0, 1]}) == \
        gates.evaluate(rt, stage, entry)
