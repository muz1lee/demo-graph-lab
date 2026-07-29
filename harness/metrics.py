"""metrics:对照 report.html 导出的金标 JSON 计算提取指标。

金标格式(report.exportGold 产物): stages{<idx>: {constraints:[{key,verdict,note}],
acceptance:[...], missing:[{name,args,note}]}}。
precision = correct/(correct+wrong);recall = correct/(correct+missing)。unsure 不计入。
"""

from __future__ import annotations

from . import util


def score(gold: dict) -> dict:
    per_stage, tot = {}, {"correct": 0, "wrong": 0, "unsure": 0, "missing": 0}
    for si, g in gold.get("stages", {}).items():
        c = {"correct": 0, "wrong": 0, "unsure": 0,
             "missing": len(g.get("missing", []))}
        for field in ("constraints", "acceptance"):
            for it in g.get(field, []):
                v = it.get("verdict")
                if v in c:
                    c[v] += 1
        per_stage[si] = c
        for k in tot:
            tot[k] += c[k]
    p_den = tot["correct"] + tot["wrong"]
    r_den = tot["correct"] + tot["missing"]
    return {
        "per_stage": per_stage, "totals": tot,
        "precision": round(tot["correct"] / p_den, 3) if p_den else None,
        "recall": round(tot["correct"] / r_den, 3) if r_den else None,
    }


def run(task: str, gold_path: str) -> dict:
    result = score(util.read_json(gold_path))
    run_dir = util.latest_run_dir(task)
    util.write_json(run_dir / "metrics.json", result)
    print(f"[metrics] {task}: P={result['precision']} R={result['recall']} "
          f"{result['totals']}")
    return result
