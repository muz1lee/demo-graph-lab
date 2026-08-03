#!/usr/bin/env python3
"""CC-2′ 24 格 region 排序矩阵(**工程冒烟,非正式 E-CAUSAL 数字**)。

W2 交付物①。判据结构出处 docs/TODO.md §1.3 CC-2′;正式判据须 PI 签字预注册
(experiments/causal/variants.json,status=DRAFT),本脚本产出**不得**被称为「CC-2′ 通过」。

做什么:对 experiments/causal/graphs.lock 钉定语料里除 push_T 外的 4 个任务
(insert_tubes / stack_bowls / deposit_coin / push_T_random),× 6 个 region,用一组
**固定 mock 候选(K=5,固定种子)** 跑 harness.regions.rank_by_region,报每格:
  • top-1 候选 id;
  • 相对参照 region(upper_body)的 Kendall τ。
偏好函数任务无关,故各任务同一 mock 候选集 —— 这正是 CC-2′ 要证的「排序由 region 标签
驱动、与任务名无关」(regions.py 任务名 grep 须 0 命中,门禁另有断言)。

离线、确定性、不触 sim/网络/LLM。python3 scripts/cc2_region_matrix.py 直接跑。
"""

import json
import random
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from harness import regions, vocab   # noqa: E402

LOCK = _REPO / "experiments" / "causal" / "graphs.lock"
SEED = 20260803          # 固定种子:同种子同候选集,矩阵可复现(md5 稳定)
K = 5                    # CC-0/CC-2′ 口径 K≥3;取 5
REF_REGION = "upper_body"
PUSH_T_SUSPENDED = "push_T"   # 挂起,不计入 24 格


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


def main():
    tasks = _tasks_from_lock()
    cands = _mock_candidates(SEED)
    all_ids = [c["id"] for c in cands]

    ref_order = [c["id"] for c in regions.rank_by_region(cands, REF_REGION)]
    ref_rank = _rank_from_order(ref_order, all_ids)

    print(f"# CC-2′ 24 格 region 排序矩阵(工程冒烟,非正式 E-CAUSAL 数字)")
    print(f"# seed={SEED} K={K} ref_region={REF_REGION}")
    print(f"# mock s = {[c['height_fraction'] for c in cands]}")
    print(f"# 每格: top1=<候选id> tau=<相对 {REF_REGION} 的 Kendall τ> [status]")
    print()

    header = f"{'task':<16}" + "".join(f"{r:>16}" for r in vocab.GRASP_REGIONS)
    print(header)
    print("-" * len(header))

    changed = 0
    total = 0
    for task in tasks:
        row = f"{task:<16}"
        for region in vocab.GRASP_REGIONS:
            ranked, meta = regions.rank_by_region(cands, region, with_meta=True)
            top1 = ranked[0]["id"]
            if meta["uncheckable"]:
                cell = f"{top1}/UNCK"
            else:
                order = [c["id"] for c in ranked]
                tau = _kendall_tau(ref_rank, _rank_from_order(order, all_ids))
                cell = f"{top1}/τ={tau:+.2f}"
                total += 1
                # 「产生不同 top-1 或 τ<1」计一格有效(CC-2′ 结构)
                if top1 != ref_order[0] or tau < 1.0:
                    changed += 1
            row += f"{cell:>16}"
        print(row)

    print()
    print(f"# 可检查格(排除 rim/handle UNCHECKABLE): {total} 格")
    print(f"# 其中相对 {REF_REGION} 产生不同 top-1 或 τ<1: {changed} 格")
    print(f"# 结论口径: 工程冒烟 —— 正式 CC-2′ 判据待 PI 签字预注册,不得据此宣称『CC-2′ 通过』")


if __name__ == "__main__":
    main()
