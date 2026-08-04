"""Deterministically complete repeated constraints without another VLM call.

规则:
1. 同类阶段传播:按阶段名分组(role=core);某 (约束名+角色模式) 在组内达到严格多数
   而在某阶段缺失 → 用该阶段的 manipulated/target 对象实例化补入,provenance=derived、
   confidence=0.4、votes="derived"。acceptance 同法。只用非 derived 项做来源,禁止链式。
2. 全图顺序约束:按 trace 阶段序补一条 order(provenance=derived)挂在首个 core 阶段。
3. 需要下放的阶段补一个 ``purpose=lower_stop`` 的控制洞。它只声明运行时应读取
   非特权接触/运动停止信号，不从示范猜测阈值。
"""

from __future__ import annotations

import json

from ..common import artifacts

_MANIP, _TARGET = "<MANIP>", "<TARGET>"


def _pattern(args: dict, manip: str | None, target: str | None):
    def sub(v):
        if isinstance(v, str):
            if manip and manip in v:
                return v.replace(manip, _MANIP)
            if target and target in v:
                return v.replace(target, _TARGET)
        return v
    return json.dumps({k: sub(v) for k, v in sorted(args.items())}, ensure_ascii=False)


def _instantiate(pattern_args: str, manip: str | None, target: str | None) -> dict | None:
    s = pattern_args
    if _MANIP in s:
        if not manip:
            return None
        s = s.replace(_MANIP, manip)
    if _TARGET in s:
        if not target:
            return None
        s = s.replace(_TARGET, target)
    return json.loads(s)


def propagate(graph: dict) -> int:
    groups: dict[str, list[dict]] = {}
    for st in graph["stages"]:
        if st.get("role", "core") == "core":
            groups.setdefault(st["name"], []).append(st)
    added = 0
    for _, stages in groups.items():
        if len(stages) < 2:
            continue
        need = len(stages) // 2 + 1
        for field in ("constraints", "acceptance"):
            presence: dict[tuple, list] = {}
            for st in stages:
                so = st.get("stage_objects") or {}
                for c in st.get(field, []):
                    if c.get("provenance") == "derived":
                        continue
                    key = (
                        c["name"],
                        _pattern(c.get("args", {}),
                                 so.get("manipulated"), so.get("target")),
                        c.get("holds"),
                    )
                    presence.setdefault(key, []).append(st["index"])
            for (name, pat, holds), idxs in presence.items():
                if len(set(idxs)) < need:
                    continue
                for st in stages:
                    if st["index"] in idxs:
                        continue
                    so = st.get("stage_objects") or {}
                    args = _instantiate(pat, so.get("manipulated"), so.get("target"))
                    if args is None:
                        continue
                    matching = [
                        constraint for constraint in st[field]
                        if (constraint["name"] == name
                            and constraint.get("args") == args)
                    ]
                    if any(constraint.get("holds") == holds
                           for constraint in matching):
                        continue
                    legacy = next((
                        constraint for constraint in matching
                        if (constraint.get("provenance") == "derived"
                            and constraint.get("holds") is None)
                    ), None)
                    if legacy is not None:
                        legacy["holds"] = holds
                        legacy["derived_from"] = sorted(set(idxs))
                        added += 1
                        continue
                    st[field].append({
                        "name": name, "args": args, "holds": holds,
                        "provenance": "derived",
                        "confidence": 0.4, "votes": "derived",
                        "derived_from": sorted(set(idxs)), "evidence_frames": []})
                    added += 1
    return added


def add_order(graph: dict) -> bool:
    core = [s for s in graph["stages"] if s.get("role", "core") == "core"]
    if len(core) < 2:
        return False
    first = core[0]
    for constraint in first["constraints"]:
        if constraint["name"] == "order":
            if (constraint.get("provenance") == "derived"
                    and constraint.get("holds") is None):
                constraint["holds"] = "throughout"
            return False
    seq = "<".join(f"s{s['index']}:{s['name']}" for s in core)
    first["constraints"].append({
        "name": "order", "args": {"stage_sequence": seq},
        "holds": "throughout",
        "provenance": "derived", "confidence": 0.5, "votes": "derived",
        "evidence_frames": []})
    return True


def add_control_holes(graph: dict) -> int:
    """Add the typed stop handle required by ``lower_until`` when it is absent."""
    added = 0
    lower_names = {"insert", "insertion", "place", "stack"}
    for stage in graph["stages"]:
        relations = [
            item.get("name")
            for field in ("constraints", "acceptance")
            for item in stage.get(field, [])
        ]
        needs_lower = stage.get("name") in lower_names or "inside" in relations
        if not needs_lower or any(
                hole.get("type") == "runtime_condition"
                and hole.get("purpose") == "lower_stop"
                for hole in stage.get("holes", [])):
            continue
        stage_objects = stage.get("stage_objects") or {}
        prefix = stage_objects.get("manipulated") or f"stage_{stage['index']}"
        existing = {hole.get("name") for hole in stage.get("holes", [])}
        name = f"{prefix}_lower_stop_condition"
        if name in existing:
            name = f"stage_{stage['index']}_lower_stop_condition"
        stage.setdefault("holes", []).append({
            "name": name,
            "type": "runtime_condition",
            "solver_hint": "non_privileged_contact_or_motion_plateau",
            "frame": "runtime",
            "purpose": "lower_stop",
            "votes": "derived",
        })
        added += 1
    return added


def run(task: str) -> dict:
    run_dir = artifacts.latest_run_dir(task)
    artifacts.invalidate_outputs(run_dir, (
        "validation.json", "report.html", "stage_program.json",
        "perception_program.json", "policy.py", "compile_report.json",
        "compiled_graph.json", "compiled_objects.json",
    ))
    graph = artifacts.read_json(run_dir / "graph.json")
    n = propagate(graph)
    ordered = add_order(graph)
    control_holes = add_control_holes(graph)
    artifacts.write_json(run_dir / "graph.json", graph)
    print(f"[enrich] {task}: +{n} propagated, +{control_holes} control holes, "
          f"order={'added' if ordered else 'kept'}")
    return graph
