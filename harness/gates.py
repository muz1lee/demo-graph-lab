"""阶段 gate 的有效性判定:约束成立 ≠ 本阶段做成了事。

动机(B4-probe 教训 + 2026-07-30 实测):stack_bowls 的 0/1/2 阶段"passed"而三个碗位移
全是 0.0000——它们满足的是 reset 时就已成立的谓词(axis_aligned margin 43.9、复合
any_of margin 3.6e-07 近乎恒真)。「跑完没报错」和「谓词碰巧为真」都不是成功证据。

本模块给每个阶段的 gate 加两道:
1. **空洞性(vacuity)**:阶段入口就已为真的约束,不能作为本阶段的成功证据。
2. **效果(effect)**:操作类阶段必须观测到世界的真实变化(被操作物体位移)。

判定结果全部落进 episode 报告,`vacuous_pass` 计数本身就是研究数据。
"""

from __future__ import annotations

import math

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
    """阶段入口快照:物体位置 + 哪些验收约束"已经"为真(这些将被判为空洞)。"""
    pre_true = {}
    for c in stage.get("acceptance", []) or []:
        probe = dict(c, _stage=stage.get("index"), _probe="pre")
        try:
            pre_true[_key(c)] = bool(rt.verify(probe))
        except Exception:
            pre_true[_key(c)] = False
    return {"objects": object_positions(rt), "pre_true": pre_true}


def evaluate(rt, stage: dict, entry: dict, strict: bool = STRICT_DEFAULT) -> dict:
    """阶段结束时判定。返回含 passed 与全部诊断字段的字典。"""
    idx = stage.get("index")
    acceptance = stage.get("acceptance", []) or []
    held = {}
    for c in acceptance:
        try:
            held[_key(c)] = bool(rt.verify(dict(c, _stage=idx)))
        except Exception:
            held[_key(c)] = False

    constraints_hold = all(held.values()) if held else False
    vacuous = sorted(k for k, v in held.items() if v and entry["pre_true"].get(k))
    informative = sorted(k for k, v in held.items() if v and not entry["pre_true"].get(k))

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
    # 无法观测物体(如 fake 干跑)时不做效果判定——但显式记录,不静默放行
    observable = bool(post)
    effect_ok = (not needs_effect) or (not observable) or (effect_move >= MIN_DISPLACEMENT_M)

    verdict = {
        "constraints_hold": constraints_hold,
        "n_acceptance": len(acceptance),
        "vacuous_pass": len(vacuous),          # 研究数据:入口即为真的"成功"
        "informative_pass": len(informative),
        "vacuous_keys": vacuous,
        "needs_effect": needs_effect,
        "effect_observable": observable,
        "effect_ok": effect_ok,
        "manipulated": manip,
        "manip_displacement_m": manip_move,
        "max_displacement_m": max_move,
        "top_mover": top_mover,
    }
    verdict["passed"] = bool(constraints_hold and (effect_ok or not strict))
    if constraints_hold and not effect_ok:
        verdict["reason"] = (f"vacuous: constraints hold but world unchanged "
                             f"(max Δ={max_move:.4f} m < {MIN_DISPLACEMENT_M})")
    elif not constraints_hold:
        failed = sorted(k for k, v in held.items() if not v)
        verdict["reason"] = f"acceptance failed: {failed[:3]}"
    return verdict
