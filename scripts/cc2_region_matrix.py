#!/usr/bin/env python3
"""CC-2′ 20 格 region 排序矩阵(**工程冒烟脚本 + 判据复核**)。

W2 交付物①。判据结构与阈值出处 experiments/causal/variants.json 的 CC-2′
(status=SIGNED,2026-08-03 PI 签字预注册);判据口径以该文件为准,本脚本**不复述阈值**,
从 variants.json 读 pair_vocabulary 与 threshold(零判据魔数)。

━━ 20 格口径(签字前修订,见 variants.json amendments_before_signature)━━
旧 24 格制(6 region × 4 任务,每格相对固定 ref_region 比 τ)含结构性死格:
  • rim/handle:UNCHECKABLE(v1 无几何特征检测,按设计)——不计分母;
  • top↔upper_body:偏好函数 s² 与 s 单调同向,离散候选上排序恒等价——非等价对里已排除。
故 CC-2′ 重预注册为 **5 个非等价可查 region 对 × 4 任务 = 20 格**,每格比较**该对两个标签
之间**的 top-1 是否改变与 Kendall τ(不再相对某个固定 ref_region)。

做什么:对 experiments/causal/graphs.lock 钉定语料里除 push_T 外的 4 个任务
(insert_tubes / stack_bowls / deposit_coin / push_T_random),× variants.json 的 5 个
non_equivalent_pairs,用一组**固定 mock 候选(K=5,固定种子)** 跑 harness.regions.rank_by_region,
报每格:
  • 两个标签各自的 top-1 候选 id;
  • 两标签排序之间的 Kendall τ;
  • 该格是否「产生不同 top-1 或 τ<1」(CC-2′ 计一格达标)。
偏好函数任务无关,故各任务同一 mock 候选集 —— 这正是 CC-2′ 要证的「排序由 region 标签
驱动、与任务名无关」(regions.py 任务名 grep 须 0 命中,门禁另有断言)。

离线、确定性、不触 sim/网络/LLM。python3 scripts/cc2_region_matrix.py 直接跑。
可选 --json <path> 落一份机读结果(供 D0 报告引用)。
"""

import argparse
import json
import random
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from harness import regions   # noqa: E402

LOCK = _REPO / "experiments" / "causal" / "graphs.lock"
VARIANTS = _REPO / "experiments" / "causal" / "variants.json"
SEED = 20260803          # 固定种子:同种子同候选集,矩阵可复现(md5 稳定)
K = 5                    # CC-0/CC-2′ 口径 K≥3;variants.json measurement 明写 K=5
PUSH_T_SUSPENDED = "push_T"   # 挂起,不计入 20 格(variants.json:push_T 挂起不计)


def _kendall_tau(rank_a, rank_b):
    """纯 Python 内联 τ∈[-1,1](与 test_constraint_causality 同实现,不引 scipy)。"""
    n = len(rank_a)
    if n < 2:
        return 1.0
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            sa = (rank_a[i] > rank_a[j]) - (rank_a[i] < rank_a[j])
            sb = (rank_b[i] > rank_b[j]) - (rank_b[i] < rank_b[j])
            p = sa * sb
            conc += p > 0
            disc += p < 0
    return (conc - disc) / (n * (n - 1) / 2)


def _rank_from_order(ordered_ids, all_ids):
    pos = {cid: r for r, cid in enumerate(ordered_ids)}
    return [pos[cid] for cid in all_ids]


def _mock_candidates(seed):
    """K 个候选,归一化高度 s 固定随机但同种子可复现;仅 s 维度可区分。"""
    rng = random.Random(seed)
    fracs = sorted(round(rng.uniform(0.05, 0.95), 3) for _ in range(K))
    return [{"id": f"cand_{i}", "height_fraction": s} for i, s in enumerate(fracs)]


def _tasks_from_lock():
    lock = json.loads(LOCK.read_text("utf-8"))
    out = []
    for g in lock.get("graphs", []):
        stem = Path(g["path"]).name.replace(".graph.json", "")
        if stem == PUSH_T_SUSPENDED:
            continue
        out.append(stem)
    return out


def _pairs_and_threshold_from_variants():
    """从 SIGNED variants.json 读 CC-2′ 的 non_equivalent_pairs 与阈值(零判据魔数)。"""
    v = json.loads(VARIANTS.read_text("utf-8"))
    pairs = [tuple(p) for p in v["pair_vocabulary"]["non_equivalent_pairs"]]
    cc2 = next(c for c in v["criteria"] if c["id"] == "CC-2′")
    return pairs, cc2["threshold"], v.get("status", "UNKNOWN")


def _rank_ids(cands, region):
    return [c["id"] for c in regions.rank_by_region(cands, region)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, default=None,
                    help="可选:把机读结果落到该路径(供 D0 报告引用)")
    args = ap.parse_args()

    tasks = _tasks_from_lock()
    pairs, threshold, status = _pairs_and_threshold_from_variants()
    cands = _mock_candidates(SEED)
    all_ids = [c["id"] for c in cands]

    print("# CC-2′ 20 格 region 排序矩阵(5 非等价可查对 × 4 任务)")
    print(f"# 判据源 experiments/causal/variants.json CC-2′(status={status})")
    print(f"# seed={SEED} K={K}")
    print(f"# mock s = {[c['height_fraction'] for c in cands]}")
    print(f"# non_equivalent_pairs(来自 variants.json,零判据魔数): {[list(p) for p in pairs]}")
    print(f"# threshold: {threshold}")
    print("# 每格: pairA_top1 vs pairB_top1 | τ(两标签排序间) | [达标? top-1改变 或 τ<1]")
    print()

    pair_labels = [f"{a}|{b}" for a, b in pairs]
    header = f"{'task':<16}" + "".join(f"{lbl:>22}" for lbl in pair_labels)
    print(header)
    print("-" * len(header))

    cells = []            # 机读:每格结果
    passed_cells = 0
    total_cells = 0
    for task in tasks:
        row = f"{task:<16}"
        for (ra, rb) in pairs:
            order_a = _rank_ids(cands, ra)
            order_b = _rank_ids(cands, rb)
            tau = _kendall_tau(_rank_from_order(order_a, all_ids),
                               _rank_from_order(order_b, all_ids))
            top1_changed = order_a[0] != order_b[0]
            # CC-2′:一格「产生不同 top-1 或 τ<1」计达标
            cell_pass = top1_changed or tau < 1.0
            total_cells += 1
            passed_cells += int(cell_pass)
            mark = "✓" if cell_pass else "✗"
            cell = f"{order_a[0]}/{order_b[0]} τ{tau:+.2f}{mark}"
            row += f"{cell:>22}"
            cells.append({
                "task": task, "pair": [ra, rb],
                "top1_a": order_a[0], "top1_b": order_b[0],
                "top1_changed": top1_changed, "tau": round(tau, 6),
                "cell_pass": cell_pass,
            })
        print(row)

    print()
    print(f"# 20 格中达标(top-1 改变 或 τ<1): {passed_cells}/{total_cells}")
    print(f"# regions.py 任务名扫描须 0 命中(门禁 public_release_check.py 断言,本脚本不重复)")
    print(f"# 判定口径见 variants.json CC-2′;阈值 threshold='{threshold}'")

    if args.json:
        payload = {
            "experiment": "CC-2′ (20-cell region matrix)",
            "criteria_source": "experiments/causal/variants.json",
            "variants_status": status,
            "seed": SEED, "K": K,
            "mock_height_fractions": [c["height_fraction"] for c in cands],
            "non_equivalent_pairs": [list(p) for p in pairs],
            "tasks": tasks,
            "threshold": threshold,
            "passed_cells": passed_cells,
            "total_cells": total_cells,
            "cells": cells,
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        print(f"# 机读结果已落: {args.json}")


if __name__ == "__main__":
    main()
