"""反事实测试:约束标签今天对抓取排序**没有**因果力(P0-01,先红后绿)。

判据出处:`docs/TODO.md` §1.3 表(CC-1′)与 §2 P0-01 行;上位形式化 `docs/PROPOSAL.md` v4 §2.1;
名实落差 `docs/DECISIONS.md` §4-G1(「把图里 region_grasp 的 region 改成 bottom,产生的抓取位姿逐比特相同」)。

**红是本文件当前的成功状态。** 它把三条断言钉在同一根因上:
  今天 `harness/kwadapter.py:295-321` 的 `solve()` 只对 hole **名字字符串**做子串匹配,
  抓取点 = oracle 质心 xy + AABB 顶 − 硬编码常量,`stage['constraints']` 一行不读。
  于是 `region_grasp(obj, region)` 里的 region 标签对输出**零影响**。

P0-02(binding.py:solve 按 type 派发 + 消费本阶段 constraints)与
P0-03(regions.py:region → 单调偏好排序函数)交付后,本文件三条断言全部转绿。

风格对齐 `tests/test_harness_units.py`:纯逻辑、离线、pytest 或直接 python3 皆可跑;
用最小 fake 提供 `KWRuntime.solve` 需要的 oracle 实体态(不触 sim/网络/LLM/EvalServer)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.kwadapter import KWRuntime


# --------------------------------------------------------------------------
# fixture:一个候选集,K 个候选沿物体竖直轴分布,归一化高度分数 s 两两可区分。
# 这些数值是 **fixture 构造参数**(候选长什么样),不是「正确答案」的度量魔数——
# 断言只比较**排序与符号**,不比较任何绝对数值(AGENTS.md §6:不许事后移阈值)。
# --------------------------------------------------------------------------

# 候选沿归一化高度 s∈[0,1] 采样:s 越大越靠物体上部。K=5(≥3)。
CANDIDATE_HEIGHT_FRACTIONS = [0.10, 0.30, 0.50, 0.70, 0.90]


def _make_candidates():
    """K 个抓取候选,仅在归一化高度 s 上可区分(其余维度不影响本判据)。"""
    return [{"id": f"cand_{i}", "height_fraction": s}
            for i, s in enumerate(CANDIDATE_HEIGHT_FRACTIONS)]


# --------------------------------------------------------------------------
# 纯 Python 内联 Kendall τ(不新增 scipy 等依赖,见 TODO §1.3 硬约束)。
# 输入为两个等长排名向量(同一批候选在两种约束下的名次);返回 τ∈[-1,1]。
# --------------------------------------------------------------------------

def _kendall_tau(rank_a, rank_b):
    assert len(rank_a) == len(rank_b) and len(rank_a) >= 2
    n = len(rank_a)
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            sign_a = (rank_a[i] > rank_a[j]) - (rank_a[i] < rank_a[j])
            sign_b = (rank_b[i] > rank_b[j]) - (rank_b[i] < rank_b[j])
            prod = sign_a * sign_b
            if prod > 0:
                concordant += 1
            elif prod < 0:
                discordant += 1
    denom = n * (n - 1) / 2
    return (concordant - discordant) / denom


def _rank_from_order(ordered_ids, all_ids):
    """把「从优到劣的候选 id 序列」转成与 all_ids 对齐的名次向量(名次小=更优)。"""
    pos = {cid: r for r, cid in enumerate(ordered_ids)}
    return [pos[cid] for cid in all_ids]


def test_kendall_tau_self_consistency():
    """内联 τ 的护栏:同序 τ=+1、逆序 τ=−1。保证断言②里的 τ<0 判据可信。"""
    a = [0, 1, 2, 3, 4]
    assert _kendall_tau(a, a) == 1.0
    assert _kendall_tau(a, list(reversed(a))) == -1.0


# --------------------------------------------------------------------------
# 最小 fake:给真实 KWRuntime.solve() 喂 oracle 实体态,不起 EvalServer。
# solve() 只经由 self.eval.state()["entities"] 读物体(见 kwadapter.py:175-180,
# :291-321),从不触 self.pipe。把 rt.eval 换成本 fake 即可离线走真代码路径。
# --------------------------------------------------------------------------

class _FakeEval:
    """只实现 solve() 用到的 state();aabb 用 dict 形态(kwadapter 两种形态都读)。"""

    def __init__(self, entities):
        self._entities = entities

    def state(self):
        return {"entities": self._entities}


# 一个竖直物体:质心在 (x,y,z),AABB 覆盖其整个竖直范围。
_OBJ_NAME = "tube_left"
_FAKE_ENTITIES = {
    _OBJ_NAME: {
        "pos": [0.42, 0.11, 0.80],
        "quat": [1.0, 0.0, 0.0, 0.0],           # wxyz,竖直
        "aabb": {"min": [0.40, 0.09, 0.72], "max": [0.44, 0.13, 0.88]},
    }
}


def _graph_with_region(region_label):
    """一个单阶段图:grasp_pose 洞 + region_grasp(obj, region) 约束。
    两次调用只有 region_label 不同,其余逐字节相同——这是反事实的唯一自变量。"""
    return {
        "stages": [{
            "index": 0,
            "name": "grasp",
            "stage_objects": {"manipulated": _OBJ_NAME, "target": "rack"},
            "holes": [{"name": "tube_left_grasp_pose", "type": "pose_se3",
                       "solver_hint": "region_grasp"}],
            "constraints": [{
                "name": "region_grasp",
                "args": {"obj": _OBJ_NAME, "region": region_label},
                "provenance": "demo_video", "confidence": 0.9,
            }],
            "acceptance": [],
        }]
    }


def _runtime_for(region_label):
    """构造真实 KWRuntime,但把 eval/pipe 换成离线 fake,只驱动 solve() 这条真实代码路径。"""
    rt = KWRuntime(_graph_with_region(region_label))
    rt.eval = _FakeEval(_FAKE_ENTITIES)
    rt.pipe = None                               # solve() 不触 pipe;置 None 兜住误用
    return rt


def _solve_grasp_pose(region_label):
    return _runtime_for(region_label).solve("tube_left_grasp_pose")


# ==========================================================================
# 附加断言(直击 G1):红的病理演示。
# 今天 solve() 不读 constraints → 只改 region 标签,产出**逐比特相同**。
# 断言「两者不同」,故今天必 fail(红)。P0-02 让 solve 消费 constraints 后转绿。
# ==========================================================================

def test_region_label_changes_solve_output():
    """CC-1′ 的病理前提:仅改 region 标签,solve 产出必须不同(今天相同 → 红)。"""
    upper = _solve_grasp_pose("upper_body")
    bottom = _solve_grasp_pose("bottom")
    assert upper != bottom, (
        "G1 名实落差(DECISIONS §4-G1):今天 solve() 不读 stage['constraints'],"
        "region_grasp 的 region 从 upper_body 改到 bottom 后抓取产出逐比特相同——"
        f"约束今天没有因果力。upper={upper!r} bottom={bottom!r}。"
        "P0-02 让 solve 委派 binding 消费 constraints 后此断言转绿。"
    )


# ==========================================================================
# CC-1′ ①:两种 region 下 top-1 候选的 height_fraction 满足 s_upper > s_bottom。
# ==========================================================================

def test_top1_height_fraction_orders_by_region():
    """CC-1′ ①:upper_body 的 top-1 应更高、bottom 的 top-1 应更低(s_upper > s_bottom)。

    这要求一个「按 region 偏好对候选集排序」的路径。该路径由 P0-03 的 harness.regions
    提供(单调偏好函数:upper_body→f(s)=s、bottom→f(s)=1−s,见 TODO §1.2 C-4)。
    今天该模块不存在 → 依 P0-01 硬约束转成带说明的 assert False(不用 skip/xfail,红要可见)。
    """
    candidates = _make_candidates()
    try:
        from harness.regions import rank_by_region
    except ImportError:
        assert False, (
            "harness.regions 尚未实现,P0-03 交付后此断言转绿(TODO §2 P0-03 / §1.2 C-4)。"
            "红的根因:今天没有任何『按 region 偏好排序候选集』的代码路径——"
            "solve() 对固定 hole 只返回单一 oracle 位姿,不消费 region、不对候选集排序。"
        )
    top1_upper = rank_by_region(candidates, "upper_body")[0]
    top1_bottom = rank_by_region(candidates, "bottom")[0]
    assert top1_upper["height_fraction"] > top1_bottom["height_fraction"], (
        "CC-1′ ①:upper_body 的 top-1 高度分数应严格大于 bottom 的 top-1。"
        f"实测 s_upper={top1_upper['height_fraction']} s_bottom={top1_bottom['height_fraction']}。"
    )


# ==========================================================================
# CC-1′ ②:两个排序之间 Kendall τ < 0(纯 Python 内联 τ)。
# ==========================================================================

def test_ranking_kendall_tau_negative_between_regions():
    """CC-1′ ②:upper_body 与 bottom 两种约束下的候选排序应负相关(τ<0)。

    同 ①,依赖 P0-03 的 harness.regions 提供排序路径;未交付前红要可见(assert False)。
    """
    candidates = _make_candidates()
    all_ids = [c["id"] for c in candidates]
    try:
        from harness.regions import rank_by_region
    except ImportError:
        assert False, (
            "harness.regions 尚未实现,P0-03 交付后此断言转绿(TODO §2 P0-03 / §1.2 C-4)。"
            "红的根因同上:今天不存在按 region 偏好排序候选集的代码路径,"
            "无从计算两种 region 下的排序、更谈不上两排序的 Kendall τ。"
        )
    order_upper = [c["id"] for c in rank_by_region(candidates, "upper_body")]
    order_bottom = [c["id"] for c in rank_by_region(candidates, "bottom")]
    rank_upper = _rank_from_order(order_upper, all_ids)
    rank_bottom = _rank_from_order(order_bottom, all_ids)
    tau = _kendall_tau(rank_upper, rank_bottom)
    assert tau < 0, (
        "CC-1′ ②:upper_body 与 bottom 的候选排序应负相关(τ<0),"
        f"实测 τ={tau}。若 τ≥0 说明 region 标签未真正翻转排序。"
    )


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {str(e).splitlines()[0]}")
    print(f"{len(fns) - failed} passed, {failed} failed")
