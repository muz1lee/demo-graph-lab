"""判定阶段是否满足约束、验收条件和可观测效果。

`acceptance` 和 `constraints` 使用 PASS/FAIL/UNKNOWN 三值判定。
`throughout` 项在入口和出口检查，其他项在出口检查。操作类阶段还要求
可观测的物体位移。入口已经成立的验收条件记为 `vacuous_pass`。
"""

from __future__ import annotations

import math

from .predicates import UNCHECKABLE_IN_RUNTIME

# 三值与 evaluation.predicates 保持一致；这里独立列出以免强耦合导入顺序。
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

# 唯一可以被排除在 hold 合取之外的一类 UNKNOWN:本 runtime **结构上**永远查不出的
# 约束(carry 需跨阶段附着状态、order 需执行序,单帧几何快照都读不出)。三值合取里
# UNKNOWN→None→passed 恒非 True，因此只要 acceptance 里出现一条 carry，任何 stage
# 都永远过不了——这是死锁，不是严格。豁免面用 predicates 的白名单钉死(单一真源)，
# 其他任何 UNKNOWN(谓词异常、缺 ctx、缺参照实体、词表外)照旧阻塞。
EXCLUDABLE_UNCHECKABLE = UNCHECKABLE_IN_RUNTIME

# 需要观测到物体位移才算数的阶段(名字取自 vocab.STAGE_VOCAB 与 trace 的 motion_type)
EFFECTFUL_STAGES = {
    "pick", "grasp", "lift", "place", "stack", "insert", "insertion",
    "transport", "push", "reorient", "pour",
}
MIN_DISPLACEMENT_M = 0.005   # 小于此位移视为"世界没变"
STRICT_DEFAULT = True        # 效果检查失败即判 gate 不过


def _key(c: dict) -> str:
    import json
    args = json.dumps(c.get("args", {}), sort_keys=True, ensure_ascii=False)
    holds = c.get("holds", "at_end")
    return f"{c.get('name')}|{args}|holds={holds}"


def _and3(entry: str, exit_: str) -> str:
    """Three-valued conjunction used for temporal checks."""
    if FAIL in (entry, exit_):
        return FAIL
    if entry == PASS and exit_ == PASS:
        return PASS
    return UNKNOWN


def _verify3(rt, constraint: dict, ctx: dict | None = None) -> str:
    """优先调用三值 ``verify3``；bool ``verify`` 用于简单 runtime。

    ``ctx`` 是谓词专用输入(``grasp_point`` / ``approach_dir``，见
    ``evaluation.predicates``)，由 runner 从 runtime 侧取本 stage 实际记录的值。
    ``verify3`` 不接受这些关键字时退回不带 ctx 的调用，行为与未接线前逐位一致
    （那两条谓词照旧 UNKNOWN，不放松）。

    任一接口抛出异常都返回 UNKNOWN，不猜测 PASS 或 FAIL。
    """
    v3 = getattr(rt, "verify3", None)
    if callable(v3):
        try:
            return v3(constraint, **(ctx or {})).status
        except TypeError:
            if not ctx:              # 不是 ctx 引起的签名不匹配 → 与既有一致,记 UNKNOWN
                return UNKNOWN
        except Exception:
            return UNKNOWN
        try:                         # verify3 不收 ctx:退回未接线前的调用形态
            return v3(constraint).status
        except Exception:
            return UNKNOWN
    try:
        return PASS if bool(rt.verify(constraint)) else FAIL
    except Exception:
        return UNKNOWN


def _excludable(constraint: dict, status: str) -> bool:
    """这一项能否被排除在 hold 合取之外。

    两个条件都要满足：约束名在 ``EXCLUDABLE_UNCHECKABLE`` 白名单里，**并且**本次
    判定确实是 UNKNOWN。后一条让将来真能查 carry 的 runtime 说了算：它给 PASS 就
    算 PASS、给 FAIL 就照旧否决，豁免只吃"结构上查不出"这一种情形。
    """
    return status == UNKNOWN and constraint.get("name") in EXCLUDABLE_UNCHECKABLE


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


def manipulated_entity_key(manip, moved: dict, resolve_object=None):
    """图对象名 → 位移表里的实体键;拿不到就返回 ``None``。

    实测(8/6 ep1):图对象名是 ``tube_left``,实体字典的键是 ``tube0_prop``,位移
    检查拿前者查后者永远查不到 → ``effect_status=UNKNOWN`` → 任何 effectful stage
    结构性过不了。修法是接受一个**可注入**的 name→entity 解析(runner 从 runtime
    取,见 ``execution.runner.run_policy``);gate 侧自己不猜名字,拿不到映射就维持
    既有的 id/前缀匹配,再拿不到就返回 ``None`` 让上层记 UNKNOWN(fail-closed 不放松)。
    """
    if resolve_object is not None:
        try:
            key = resolve_object(manip)
        except Exception:
            key = None                 # 解析不出/有歧义都当"拿不到",不猜一个
        if isinstance(key, str) and key in moved:
            return key
    manip_id = str(manip).split(".")[0].lower()
    for key, _ in moved.items():
        entity_id = key.lower()
        if entity_id == manip_id or entity_id.startswith(f"{manip_id}_"):
            return key
    return None


def snapshot(rt, stage: dict, ctx: dict | None = None) -> dict:
    """阶段入口快照:位置、验收初值及 throughout 项的入口三值。

    ``ctx`` 同 ``evaluate``:谓词专用输入。入口探针发生在本阶段动作之前,runner
    此时**不**传抓取点/接近方向(还没发生),于是 throughout 的 region_grasp /
    approach_direction 入口仍是 UNKNOWN、合取后整条 UNKNOWN——fail-closed,不放松。
    """
    idx = stage.get("index")
    # pre_true[k] = True 仅当入口三值检验为 PASS(UNKNOWN/FAIL 均非"已为真",不误判空洞)。
    pre_true = {}
    entry_acceptance = {}
    for c in stage.get("acceptance", []) or []:
        key = _key(c)
        status = _verify3(rt, dict(c, _stage=idx, _probe="pre"), ctx)
        pre_true[key] = status == PASS
        if str(c.get("holds")) == "throughout":
            entry_acceptance[key] = status
    # entry_constraint[k] = 入口三值(供 exit 侧判 throughout 的 violated_midway)。
    entry_constraint = {}
    for c in stage.get("constraints", []) or []:
        if str(c.get("holds")) == "throughout":
            entry_constraint[_key(c)] = _verify3(
                rt, dict(c, _stage=idx, _probe="pre"), ctx)
    return {"objects": object_positions(rt), "pre_true": pre_true,
            "entry_acceptance": entry_acceptance,
            "entry_constraint": entry_constraint}


def evaluate(rt, stage: dict, entry: dict, strict: bool = STRICT_DEFAULT,
             resolve_object=None, ctx: dict | None = None) -> dict:
    """阶段结束时判定。返回含 passed 与全部诊断字段的字典。

    ``resolve_object`` 是可选的图对象名 → 实体键解析(见 ``manipulated_entity_key``);
    不给时行为与既有一致。``ctx`` 是谓词专用输入(``grasp_point`` / ``approach_dir``),
    由 runner 从 runtime 侧取本 stage 实际记录的值;不给时那两条谓词照旧 UNKNOWN。

    结构性不可查项(``EXCLUDABLE_UNCHECKABLE``)见 ``_excludable``:它们不参与 hold
    合取,但完整记账在 ``excluded_uncheckable_keys`` 与 ``unknown_keys`` 里。
    """
    idx = stage.get("index")
    acceptance = stage.get("acceptance", []) or []
    # throughout acceptance 必须入口和出口都成立；其他 acceptance 只看出口。
    held = {}
    excluded_acceptance = set()
    acceptance_violated_midway = []
    entry_acceptance = entry.get("entry_acceptance", {})
    for c in acceptance:
        key = _key(c)
        exit_v = _verify3(rt, dict(c, _stage=idx), ctx)
        if str(c.get("holds")) == "throughout":
            entry_v = entry_acceptance.get(key, UNKNOWN)
            held[key] = _and3(entry_v, exit_v)
            if entry_v == PASS and exit_v == FAIL:
                acceptance_violated_midway.append(key)
        else:
            held[key] = exit_v
        if _excludable(c, held[key]):
            excluded_acceptance.add(key)

    # 三值合取：任一 FAIL→False；全部 PASS→True；含 UNKNOWN→None。
    # 结构性不可查项先剔除(它们只可能是 UNKNOWN,不影响 FAIL 一侧)；全部被剔除时
    # 合取里没有任何证据 → 仍是 None,豁免不凭空造出一个 True。
    graded = {k: v for k, v in held.items() if k not in excluded_acceptance}
    if any(value == FAIL for value in graded.values()):
        acceptance_hold = False
    elif graded and all(value == PASS for value in graded.values()):
        acceptance_hold = True
    else:
        acceptance_hold = None
    a_unknown = sorted(k for k, v in held.items() if v == UNKNOWN)
    vacuous = sorted(k for k, v in held.items() if v == PASS and entry["pre_true"].get(k))
    informative = sorted(k for k, v in held.items()
                         if v == PASS and not entry["pre_true"].get(k))

    # throughout 在入口和出口检查；其他约束只在出口检查。
    entry_constraint = entry.get("entry_constraint", {})
    constraint_held = {}
    excluded_constraints = set()
    constraint_violated_midway = []
    for c in stage.get("constraints", []) or []:
        k = _key(c)
        exit_v = _verify3(rt, dict(c, _stage=idx), ctx)
        if str(c.get("holds")) == "throughout":
            entry_v = entry_constraint.get(k, UNKNOWN)
            constraint_held[k] = _and3(entry_v, exit_v)
            if entry_v == PASS and exit_v == FAIL:
                constraint_violated_midway.append(k)
        else:
            constraint_held[k] = exit_v
        if _excludable(c, constraint_held[k]):
            excluded_constraints.add(k)
    graded_c = {k: v for k, v in constraint_held.items()
                if k not in excluded_constraints}
    c_fail = (any(v == FAIL for v in graded_c.values())
              or bool(constraint_violated_midway))
    if c_fail:
        constraints_hold = False
    elif all(value == PASS for value in graded_c.values()):
        constraints_hold = True          # 空合取为真(无约束,或全部结构性不可查)
    else:
        constraints_hold = None          # 至少一个可查项 UNKNOWN → 判不了
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
    manip_move, manip_key = None, None
    if manip:
        manip_key = manipulated_entity_key(manip, moved, resolve_object)
        if manip_key is not None:
            manip_move = moved[manip_key]
    effect_move = manip_move if manip is not None else max_move
    observable = manip_move is not None if manip is not None else bool(post)
    # 不需位移的阶段为 PASS；不可观测为 UNKNOWN；其余按位移阈值判定。
    if not needs_effect:
        effect_status = PASS
    elif not observable:
        effect_status = UNKNOWN
    elif effect_move is not None and effect_move >= MIN_DISPLACEMENT_M:
        effect_status = PASS
    else:
        effect_status = FAIL
    effect_ok = (effect_status == PASS)

    # UNKNOWN 单独记账，不折入 PASS/FAIL。被豁免的结构性不可查项**仍然**留在
    # unknown_keys 里（它们确实没被查过），只是另外点名在 excluded 里。
    unknown_keys = sorted(set(a_unknown) | set(c_unknown))
    excluded_keys = sorted(excluded_acceptance | excluded_constraints)
    n_checks = len(held) + len(constraint_held)
    unknown_frac = (len(unknown_keys) / n_checks) if n_checks else 0.0

    verdict = {
        "acceptance_hold": acceptance_hold,    # True/False/None（判不了）
        "constraints_hold": constraints_hold,  # True/False/None，来自 stage["constraints"]
        "n_acceptance": len(acceptance),
        "n_constraints": len(constraint_held),
        "violated_midway": sorted(
            acceptance_violated_midway + constraint_violated_midway),
        "vacuous_pass": len(vacuous),          # 研究数据:入口即为真的"成功"
        "informative_pass": len(informative),
        "vacuous_keys": vacuous,
        "unknown_keys": unknown_keys,          # 三值记账:检查不了的约束(不静默计入 pass/fail)
        "n_unknown": len(unknown_keys),
        # 结构性不可查、因而被排除在合取之外的键(白名单内且本次确为 UNKNOWN)。
        "excluded_uncheckable_keys": excluded_keys,
        "n_excluded_uncheckable": len(excluded_keys),
        "unknown_frac": round(unknown_frac, 4),
        "needs_effect": needs_effect,
        "effect_observable": observable,
        "effect_status": effect_status,        # PASS/FAIL/UNKNOWN(不可观测=UNKNOWN,不再静默放行)
        "effect_ok": effect_ok,
        "manipulated": manip,
        "manipulated_entity": manip_key,       # 图名映到了哪个实体键(拿不到=None)
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
        failed_c = (sorted(k for k, v in constraint_held.items() if v == FAIL)
                    or constraint_violated_midway)
        verdict["reason"] = f"constraints failed: {failed_c[:3]}"
    elif acceptance_hold is False:
        failed = sorted(k for k, v in held.items() if v == FAIL)
        verdict["reason"] = f"acceptance failed: {failed[:3]}"
    elif effect_status == FAIL:
        verdict["reason"] = (f"vacuous: constraints hold but world unchanged "
                             f"(max Δ={max_move:.4f} m < {MIN_DISPLACEMENT_M})")
    elif not verdict["passed"]:
        # 只点名**真正挡住判定**的 UNKNOWN;被豁免的那些另计,免得把已豁免的键
        # 写成阻塞原因。
        blocking = [k for k in unknown_keys if k not in set(excluded_keys)]
        verdict["reason"] = (f"undetermined: unknown checks={blocking[:3]} "
                             f"excluded_uncheckable={len(excluded_keys)} "
                             f"effect={effect_status}")
    return verdict
