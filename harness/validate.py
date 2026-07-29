"""validate:V1 结构/词表校验 + 度量字面量扫描。铁律:args 里不得出现数值。"""

from __future__ import annotations

import re

from . import util, vocab

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
    if name == "region_grasp" and args.get("region") not in vocab.GRASP_REGIONS:
        errs.append(f"s{stage_idx}.{field}: 非法 region {args.get('region')!r}")
    if name == "approach_direction" and args.get("cone") not in vocab.APPROACH_CONES:
        errs.append(f"s{stage_idx}.{field}: 非法 cone {args.get('cone')!r}")
    if item.get("provenance") not in vocab.PROVENANCE_ALLOWED:
        errs.append(f"s{stage_idx}.{field}: 非法 provenance {item.get('provenance')!r}")
    for key, v in args.items():
        if _is_metric_literal(v):
            errs.append(f"s{stage_idx}.{field}: 度量字面量 {name}.{key}={v!r}(必须留洞)")
    return errs


def run(task: str) -> dict:
    run_dir = util.latest_run_dir(task)
    graph = util.read_json(run_dir / "graph.json")
    errors, n_items = [], 0
    for st in graph["stages"]:
        for field in ("constraints", "acceptance"):
            for it in st.get(field, []):
                n_items += 1
                errors.extend(check_item(it, st["index"], field))
        for h in st.get("holes", []):
            if h.get("type") not in vocab.HOLE_TYPES:
                errors.append(f"s{st['index']}.holes: 非法 hole 类型 {h.get('type')!r}")
    result = {"task": task, "items_checked": n_items,
              "violations": errors, "passed": not errors}
    util.write_json(run_dir / "validation.json", result)
    print(f"[validate] {task}: {n_items} items, {len(errors)} violations "
          f"-> {'PASS' if result['passed'] else 'FAIL'}")
    return result
