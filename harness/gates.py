"""[runtime] 阶段 gate 的有效性判定:约束成立 ≠ 本阶段做成了事。

动机(B4-probe 教训 + 2026-07-30 实测):stack_bowls 的 0/1/2 阶段"passed"而三个碗位移
全是 0.0000——它们满足的是 reset 时就已成立的谓词(axis_aligned margin 43.9、复合
any_of margin 3.6e-07 近乎恒真)。「跑完没报错」和「谓词碰巧为真」都不是成功证据。

本模块给每个阶段的 gate 加三道:
1. **验收(acceptance)**:`stage["acceptance"]` 逐条谓词的合取(旧字段名叫 constraints_hold,
   名不副实——P0-04/C-6 起改名 `acceptance_hold`,语义不变)。
2. **约束(constraints)**:`stage["constraints"]` 逐条谓词判定(P0-04/C-6 新增,此前一行不读):
   `holds=="throughout"` 在 entry/exit 各查一次(入口成立、出口违反记 `violated_midway`);
   `holds=="at_end"`(及缺省)只在出口查,并入验收口径。
3. **空洞性(vacuity)**:阶段入口就已为真的**验收**约束,不作为本阶段的成功证据(只统计不否决)。
4. **效果(effect)**:操作类阶段必须观测到世界的真实变化(被操作物体位移)。

判定结果全部落进 episode 报告,`vacuous_pass` 计数本身就是研究数据。
"""

from __future__ import annotations

import math

# 三值(与 harness.predicates 同一套字面量;此处独立列出以免强耦合导入顺序)
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

# 需要观测到物体位移才算数的阶段(名字取自 vocab.STAGE_VOCAB 与 trace 的 motion_type)
EFFECTFUL_STAGES = {
    "pick", "grasp", "lift", "place", "stack", "insert", "insertion",
    "transport", "push", "reorient", "pour",
}
MIN_DISPLACEMENT_M = 0.005   # 小于此位移视为"世界没变"
STRICT_DEFAULT = True        # 效果检查失败即判 gate 不过


def _key(c: dict) -> str:
    import json
    return f"{c.get('name')}|{json.dumps(c.get('args', {}), sort_keys=True, ensure_ascii=False)}"


def _verify3(rt, constraint: dict) -> str:
    """三值检验(破口②/P0-05):优先用 runtime 的 verify3(harness.predicates),
    退回旧 bool verify()。**fail-open 归零**:任何检查不了 = UNKNOWN,绝不静默成 PASS/FAIL。

    - rt.verify3 存在(KWRuntime)→ 返回其 status(PASS/FAIL/UNKNOWN)。
    - 只有 bool verify()(如 FakeRuntime)→ True→PASS / False→FAIL;
      **异常 → UNKNOWN**(而不是旧代码的静默 False,那是方向性 fail-closed)。
    """
    v3 = getattr(rt, "verify3", None)
    if callable(v3):
        try:
            return v3(constraint).status
        except Exception:
            return UNKNOWN
    try:
        return PASS if bool(rt.verify(constraint)) else FAIL
    except Exception:
        return UNKNOWN


def object_positions(rt) -> dict:
    """从可信 runtime 读当前物体位置(仅 evaluator 侧特权数据,不进方法路径)。"""
    fn = getattr(rt, "_entities", None)
    if fn is None:
        return {}
    try:
        try:
            ents = fn(max_age_s=0.0)
        except TypeError:
            ents = fn()
        return {k: list(v["pos"]) for k, v in ents.items()
                if isinstance(v, dict) and "pos" in v}
    except Exception:
        return {}


def snapshot(rt, stage: dict) -> dict:
    """阶段入口快照:物体位置 + 哪些验收约束"已经"为真(这些将被判为空洞) +
    throughout 约束在入口是否成立(供 exit 侧判 violated_midway)。"""
    idx = stage.get("index")
    # pre_true[k] = True 仅当入口三值检验为 PASS(UNKNOWN/FAIL 均非"已为真",不误判空洞)。
    pre_true = {}
    for c in stage.get("acceptance", []) or []:
        pre_true[_key(c)] = _verify3(rt, dict(c, _stage=idx, _probe="pre")) == PASS
    # entry_constraint[k] = 入口三值(供 exit 侧判 throughout 的 violated_midway)。
    entry_constraint = {}
    for c in stage.get("constraints", []) or []:
        if str(c.get("holds")) == "throughout":
            entry_constraint[_key(c)] = _verify3(rt, dict(c, _stage=idx, _probe="pre"))
    return {"objects": object_positions(rt), "pre_true": pre_true,
            "entry_constraint": entry_constraint}


def evaluate(rt, stage: dict, entry: dict, strict: bool = STRICT_DEFAULT) -> dict:
    """阶段结束时判定。返回含 passed 与全部诊断字段的字典。"""
    idx = stage.get("index")
    acceptance = stage.get("acceptance", []) or []
    # 三值检验(破口②):PASS/FAIL/UNKNOWN。UNKNOWN 记账,不静默折进 PASS/FAIL。
    held = {_key(c): _verify3(rt, dict(c, _stage=idx)) for c in acceptance}

    # acceptance_hold(C-6 前叫 constraints_hold,名不副实已改名):在**可判子集**(PASS/FAIL)上
    # 取合取——任一 FAIL → False;全 PASS → True;全 UNKNOWN/空 → None(判不了,不硬当 True/False)。
    a_fail = any(v == FAIL for v in held.values())
    a_pass = any(v == PASS for v in held.values())
    acceptance_hold = (not a_fail) if a_pass else (False if a_fail else None)
    a_unknown = sorted(k for k, v in held.items() if v == UNKNOWN)
    vacuous = sorted(k for k, v in held.items() if v == PASS and entry["pre_true"].get(k))
    informative = sorted(k for k, v in held.items()
                         if v == PASS and not entry["pre_true"].get(k))

    # constraints_hold:真的读 stage["constraints"](C-6 新增),同样三值。
    #   holds=="throughout" → entry/exit 各查一次:入口 PASS、出口非 PASS 记 violated_midway;
    #   holds=="at_end"(及缺省) → 只在出口查。
    # 无约束/全 UNKNOWN 的阶段:constraints_hold=None(判不了 ≠ 失败,也 ≠ 通过)。
    entry_constraint = entry.get("entry_constraint", {})
    constraint_held = {}
    violated_midway = []
    for c in stage.get("constraints", []) or []:
        k = _key(c)
        exit_v = _verify3(rt, dict(c, _stage=idx))
        constraint_held[k] = exit_v
        if (str(c.get("holds")) == "throughout"
                and entry_constraint.get(k) == PASS and exit_v == FAIL):
            violated_midway.append(k)
    c_fail = any(v == FAIL for v in constraint_held.values()) or bool(violated_midway)
    c_pass = any(v == PASS for v in constraint_held.values())
    if not constraint_held:
        constraints_hold = True          # 无约束 = 空合取真(C-6 口径,不 fail-open)
    elif c_fail:
        constraints_hold = False
    elif c_pass:
        constraints_hold = True
    else:
        constraints_hold = None          # 全 UNKNOWN → 判不了
    c_unknown = sorted(k for k, v in constraint_held.items() if v == UNKNOWN)

    post = object_positions(rt)
    pre = entry.get("objects", {})
    moved = {k: round(math.dist(post[k], pre[k]), 4)
             for k in post if k in pre and len(post[k]) == len(pre[k])}
    max_move = max(moved.values(), default=0.0)
    top_mover = max(moved, key=moved.get) if moved else None

    stage_name = str(stage.get("name", "")).lower()
    needs_effect = any(tok in stage_name for tok in EFFECTFUL_STAGES)
    manip = (stage.get("stage_objects") or {}).get("manipulated")
    manip_move = None
    if manip:
        for k, d in moved.items():
            if str(manip).split(".")[0].lower() in k.lower():
                manip_move = d
                break
    effect_move = manip_move if manip_move is not None else max_move
    observable = bool(post)
    # 效果三值(破口②/#5 fail-open 归零):不再用 `or (not observable)` 静默把不可观测当通过。
    #   不需效果的阶段        → PASS(结构上无需效果)。
    #   需效果但不可观测(干跑) → UNKNOWN(判不了,显式记账;不静默声称 effect_ok)。
    #   需效果且可观测        → 位移达标 PASS,否则 FAIL(空洞:约束成立但世界没动)。
    if not needs_effect:
        effect_status = PASS
    elif not observable:
        effect_status = UNKNOWN
    elif effect_move >= MIN_DISPLACEMENT_M:
        effect_status = PASS
    else:
        effect_status = FAIL
    effect_ok = (effect_status == PASS)   # 仅真观测到效果(或无需效果)才为真,不再含不可观测

    # UNKNOWN 记账(CC-4 <20% 是 D0 闸门);unknown 计数即旧 "unchecked" 的归零替代。
    unknown_keys = sorted(set(a_unknown) | set(c_unknown))
    n_checks = len(held) + len(constraint_held)
    unknown_frac = (len(unknown_keys) / n_checks) if n_checks else 0.0

    verdict = {
        "acceptance_hold": acceptance_hold,    # True/False/None(判不了);C-6 前叫 constraints_hold
        "constraints_hold": constraints_hold,  # True/False/None;C-6 新增:真的读 stage["constraints"]
        "n_acceptance": len(acceptance),
        "n_constraints": len(constraint_held),
        "violated_midway": violated_midway,    # throughout 约束入口成立、出口违反
        "vacuous_pass": len(vacuous),          # 研究数据:入口即为真的"成功"
        "informative_pass": len(informative),
        "vacuous_keys": vacuous,
        "unknown_keys": unknown_keys,          # 三值记账:检查不了的约束(不静默计入 pass/fail)
        "n_unknown": len(unknown_keys),
        "unknown_frac": round(unknown_frac, 4),
        "needs_effect": needs_effect,
        "effect_observable": observable,
        "effect_status": effect_status,        # PASS/FAIL/UNKNOWN(不可观测=UNKNOWN,不再静默放行)
        "effect_ok": effect_ok,
        "manipulated": manip,
        "manip_displacement_m": manip_move,
        "max_displacement_m": max_move,
        "top_mover": top_mover,
    }
    # passed:只有 acceptance_hold 与 constraints_hold 都**确为 True**(None=判不了不算通过),
    # 且效果非 FAIL(strict 下)。UNKNOWN 从不静默变成通过,也不静默变成失败——只是不满足 passed。
    verdict["passed"] = bool(acceptance_hold is True and constraints_hold is True
                             and (effect_status != FAIL or not strict))
    if constraints_hold is False:
        failed_c = sorted(k for k, v in constraint_held.items() if v == FAIL) or violated_midway
        verdict["reason"] = f"constraints failed: {failed_c[:3]}"
    elif acceptance_hold is False:
        failed = sorted(k for k, v in held.items() if v == FAIL)
        verdict["reason"] = f"acceptance failed: {failed[:3]}"
    elif effect_status == FAIL:
        verdict["reason"] = (f"vacuous: constraints hold but world unchanged "
                             f"(max Δ={max_move:.4f} m < {MIN_DISPLACEMENT_M})")
    elif not verdict["passed"]:
        verdict["reason"] = (f"undetermined: unknown checks={unknown_keys[:3]} "
                             f"effect={effect_status}")
    return verdict
