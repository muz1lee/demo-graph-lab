"""[phase0 6/9 · enrich] enrich:提取后的确定性补全 pass(无 LLM)。治「不对称提取」——R 的最大杠杆。

规则:
1. 同类阶段传播:按阶段名分组(role=core);某 (约束名+角色模式) 在组内 ≥⌈半数⌉ 阶段出现
   而在某阶段缺失 → 用该阶段的 manipulated/target 对象实例化补入,provenance=derived、
   confidence=0.4、votes="derived"。acceptance 同法。只用非 derived 项做来源,禁止链式。
2. 全图顺序约束:按 trace 阶段序补一条 order(provenance=derived)挂在首个 core 阶段。
"""

from __future__ import annotations

import json
import math

from . import util

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
        need = math.ceil(len(stages) / 2)
        for field in ("constraints", "acceptance"):
            presence: dict[tuple, list] = {}
            for st in stages:
                so = st.get("stage_objects") or {}
                for c in st.get(field, []):
                    if c.get("provenance") == "derived":
                        continue
                    key = (c["name"], _pattern(c.get("args", {}),
                                               so.get("manipulated"), so.get("target")))
                    presence.setdefault(key, []).append(st["index"])
            for (name, pat), idxs in presence.items():
                if len(set(idxs)) < need:
                    continue
                for st in stages:
                    if st["index"] in idxs:
                        continue
                    so = st.get("stage_objects") or {}
                    args = _instantiate(pat, so.get("manipulated"), so.get("target"))
                    if args is None or any(c["name"] == name and c.get("args") == args
                                           for c in st[field]):
                        continue
                    st[field].append({
                        "name": name, "args": args, "provenance": "derived",
                        "confidence": 0.4, "votes": "derived",
                        "derived_from": sorted(set(idxs)), "evidence_frames": []})
                    added += 1
    return added


def add_order(graph: dict) -> bool:
    core = [s for s in graph["stages"] if s.get("role", "core") == "core"]
    if len(core) < 2:
        return False
    first = core[0]
    if any(c["name"] == "order" for c in first["constraints"]):
        return False
    seq = "<".join(f"s{s['index']}:{s['name']}" for s in core)
    first["constraints"].append({
        "name": "order", "args": {"stage_sequence": seq},
        "provenance": "derived", "confidence": 0.5, "votes": "derived",
        "evidence_frames": []})
    return True


def run(task: str) -> dict:
    run_dir = util.latest_run_dir(task)
    graph = util.read_json(run_dir / "graph.json")
    n = propagate(graph)
    ordered = add_order(graph)
    util.write_json(run_dir / "graph.json", graph)
    print(f"[enrich] {task}: +{n} propagated, order={'added' if ordered else 'kept'}")
    return graph
