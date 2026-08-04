"""Validate graph structure, closed vocabularies, and metric literals.

约束 args 中不得出现数值；度量量必须通过 typed hole 解析。
"""

from __future__ import annotations

import re

from ..common import artifacts
from . import vocab

_UNIT_RE = re.compile(r"\d+\.?\d*\s*(mm|cm|m\b|deg|°|rad)", re.I)


def _is_metric_literal(v) -> bool:
    """数值型 arg 即违规;含单位的数字串违规;'tube0' 这类带数字标识符不违规。"""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        s = v.strip()
        try:
            float(s)
            return True
        except ValueError:
            pass
        return bool(_UNIT_RE.search(s))
    if isinstance(v, (list, tuple)):
        return any(_is_metric_literal(x) for x in v)
    if isinstance(v, dict):
        return any(_is_metric_literal(x) for x in v.values())
    return False


def check_item(item: dict, stage_idx: int, field: str) -> list[str]:
    errs = []
    name = item.get("name")
    if name not in vocab.CONSTRAINT_VOCAB:
        errs.append(f"s{stage_idx}.{field}: 未知约束名 {name!r}")
    args = item.get("args", {}) or {}
    if not isinstance(args, dict):
        errs.append(f"s{stage_idx}.{field}: args 必须是对象")
        args = {}
    spec = vocab.CONSTRAINT_VOCAB.get(name)
    if spec:
        required = set(spec["args"])
        allowed = required | set(spec.get("optional", []))
        missing = sorted(required - set(args))
        extra = sorted(set(args) - allowed)
        if missing:
            errs.append(f"s{stage_idx}.{field}: {name} 缺少参数 {missing}")
        if extra:
            errs.append(f"s{stage_idx}.{field}: {name} 含未知参数 {extra}")
    if name == "region_grasp" and args.get("region") not in vocab.GRASP_REGIONS:
        errs.append(f"s{stage_idx}.{field}: 非法 region {args.get('region')!r}")
    if name == "approach_direction" and args.get("cone") not in vocab.APPROACH_CONES:
        errs.append(f"s{stage_idx}.{field}: 非法 cone {args.get('cone')!r}")
    if item.get("provenance") not in vocab.PROVENANCE_ALLOWED:
        errs.append(f"s{stage_idx}.{field}: 非法 provenance {item.get('provenance')!r}")
    pairs = args.items() if isinstance(args, dict) else enumerate(args)
    for key, v in pairs:
        if _is_metric_literal(v):
            errs.append(f"s{stage_idx}.{field}: 度量字面量 {name}.{key}={v!r}(必须留洞)")
    return errs


def run(task: str) -> dict:
    run_dir = artifacts.latest_run_dir(task)
    graph = artifacts.read_json(run_dir / "graph.json")
    fps = artifacts.read_json(run_dir / "meta.json")["video"]["fps"]
    errors, warnings, n_items = [], [], 0
    for st in graph["stages"]:
        idx = st["index"]
        lo, hi = st["start_sec"] - 1.0, st["end_sec"] + 1.0
        for field in ("constraints", "acceptance"):
            for it in st.get(field, []):
                n_items += 1
                errors.extend(check_item(it, idx, field))
                if it.get("holds") not in (None, *vocab.HOLDS_ALLOWED):
                    errors.append(f"s{idx}.{field}: 非法 holds {it.get('holds')!r}")
                ev = it.get("evidence_frames") or []
                if (it.get("provenance") != "derived" and ev
                        and all(not (lo <= f / fps <= hi) for f in ev)):
                    warnings.append(f"s{idx}.{field}: 时序错位? {it['name']} "
                                    f"证据帧 {ev} 全在阶段窗口外")
        for h in st.get("holes", []):
            if h.get("type") not in vocab.HOLE_TYPES:
                errors.append(f"s{idx}.holes: 非法 hole 类型 {h.get('type')!r}")
        if (sum(1 for h in st.get("holes", []) if h.get("type") == "axis_3d") >= 2
                and not any(c["name"] == "axis_parallel" for c in st.get("constraints", []))):
            warnings.append(f"s{idx}: 装配缺口——axis_3d 洞≥2 但无 axis_parallel 约束")
    result = {"task": task, "items_checked": n_items,
              "violations": errors, "warnings": warnings, "passed": not errors}
    artifacts.write_json(run_dir / "validation.json", result)
    print(f"[validate] {task}: {n_items} items, {len(errors)} violations, "
          f"{len(warnings)} warnings -> {'PASS' if result['passed'] else 'FAIL'}")
    return result
