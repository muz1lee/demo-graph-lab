"""判定阶段是否满足约束、验收条件和可观测效果。

`acceptance` 和 `constraints` 使用 PASS/FAIL/UNKNOWN 三值判定。
`throughout` 约束在入口和出口检查，其他约束在出口检查。操作类阶段还要求
可观测的物体位移。入口已经成立的验收条件记为 `vacuous_pass`。
"""

from __future__ import annotations

import math

# 三值与 evaluation.predicates 保持一致；这里独立列出以免强耦合导入顺序。
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
    """优先调用三值 ``verify3``；bool ``verify`` 用于简单 runtime。

    任一接口抛出异常都返回 UNKNOWN，不猜测 PASS 或 FAIL。
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
    # 三值检验：UNKNOWN 记账，不静默折进 PASS/FAIL。
    held = {_key(c): _verify3(rt, dict(c, _stage=idx)) for c in acceptance}

    # 在可判子集上取合取；全 UNKNOWN 或空集返回 None。
    a_fail = any(v == FAIL for v in held.values())
    a_pass = any(v == PASS for v in held.values())
    acceptance_hold = (not a_fail) if a_pass else (False if a_fail else None)
    a_unknown = sorted(k for k, v in held.items() if v == UNKNOWN)
    vacuous = sorted(k for k, v in held.items() if v == PASS and entry["pre_true"].get(k))
    informative = sorted(k for k, v in held.items()
                         if v == PASS and not entry["pre_true"].get(k))

    # throughout 在入口和出口检查；其他约束只在出口检查。
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
        constraints_hold = True          # 无约束时空合取为真
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
    # 不需位移的阶段为 PASS；不可观测为 UNKNOWN；其余按位移阈值判定。
    if not needs_effect:
        effect_status = PASS
    elif not observable:
        effect_status = UNKNOWN
    elif effect_move >= MIN_DISPLACEMENT_M:
        effect_status = PASS
    else:
        effect_status = FAIL
    effect_ok = (effect_status == PASS)

    # UNKNOWN 单独记账，不折入 PASS/FAIL。
    unknown_keys = sorted(set(a_unknown) | set(c_unknown))
    n_checks = len(held) + len(constraint_held)
    unknown_frac = (len(unknown_keys) / n_checks) if n_checks else 0.0

    verdict = {
        "acceptance_hold": acceptance_hold,    # True/False/None（判不了）
        "constraints_hold": constraints_hold,  # True/False/None，来自 stage["constraints"]
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
    # UNKNOWN 不满足通过条件；strict 模式还要求效果为 PASS。
    effect_passes = effect_status == PASS if strict else True
    verdict["passed"] = bool(
        acceptance_hold is True and constraints_hold is True and effect_passes
    )
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
