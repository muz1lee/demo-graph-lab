"""Causal regressions: qualitative constraints must change the value path.

纯逻辑、离线，由 pytest 运行；
用最小 fake 提供 `OracleRuntime.solve` 需要的 oracle 实体态(不触 sim/网络/LLM/EvalServer)。
"""

from demo_graph_lab.execution.oracle_runtime import OracleRuntime


# --------------------------------------------------------------------------
# fixture:一个候选集,K 个候选沿物体竖直轴分布,归一化高度分数 s 两两可区分。
# 这些数值是 **fixture 构造参数**(候选长什么样),不是「正确答案」的度量魔数——
# 断言只比较**排序与符号**,不比较任何绝对数值。
# --------------------------------------------------------------------------

# 候选沿归一化高度 s∈[0,1] 采样:s 越大越靠物体上部。K=5(≥3)。
CANDIDATE_HEIGHT_FRACTIONS = [0.10, 0.30, 0.50, 0.70, 0.90]


def _make_candidates():
    """K 个抓取候选,仅在归一化高度 s 上可区分(其余维度不影响本判据)。"""
    return [{"id": f"cand_{i}", "height_fraction": s}
            for i, s in enumerate(CANDIDATE_HEIGHT_FRACTIONS)]


# --------------------------------------------------------------------------
# 纯 Python 内联 Kendall τ，避免增加 scipy 依赖。
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
    """内联 τ 的护栏:同序 τ=+1、逆序 τ=−1。"""
    a = [0, 1, 2, 3, 4]
    assert _kendall_tau(a, a) == 1.0
    assert _kendall_tau(a, list(reversed(a))) == -1.0


# --------------------------------------------------------------------------
# 最小 fake:给真实 OracleRuntime.solve() 喂 oracle 实体态,不起 EvalServer。
# solve() 只经由 self.eval.state()["entities"] 读物体,不触发 self.pipe。
# 把 rt.eval 换成本 fake 即可离线走真代码路径。
# --------------------------------------------------------------------------

class _FakeEval:
    """只实现 solve() 用到的 state();aabb 使用 dict 形态。"""

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
    """构造真实 OracleRuntime,但把 eval/pipe 换成离线 fake,只驱动 solve() 代码路径。"""
    rt = OracleRuntime(_graph_with_region(region_label))
    rt.eval = _FakeEval(_FAKE_ENTITIES)
    rt.pipe = None                               # solve() 不触 pipe;置 None 兜住误用
    return rt


def _solve_grasp_pose(region_label):
    return _runtime_for(region_label).solve("tube_left_grasp_pose")


# ==========================================================================
# 反事实回归:仅改变 region 标签时,solve() 的输出必须随之变化。
# ==========================================================================

def test_region_label_changes_solve_output():
    """仅改 region 标签时,solve 产出必须不同。"""
    upper = _solve_grasp_pose("upper_body")
    bottom = _solve_grasp_pose("bottom")
    assert upper != bottom, (
        "region_grasp must affect the solved pose: "
        f"upper={upper!r} bottom={bottom!r}"
    )


# ==========================================================================
# 两种 region 下 top-1 候选的 height_fraction 应满足 s_upper > s_bottom。
# ==========================================================================

def test_top1_height_fraction_orders_by_region():
    """upper_body 的 top-1 应更高、bottom 的 top-1 应更低。

    该路径由 selection.regions 的任务无关单调偏好函数提供。
    """
    candidates = _make_candidates()
    from demo_graph_lab.selection.regions import rank_by_region
    top1_upper = rank_by_region(candidates, "upper_body")[0]
    top1_bottom = rank_by_region(candidates, "bottom")[0]
    assert top1_upper["height_fraction"] > top1_bottom["height_fraction"], (
        "upper_body 的 top-1 高度分数应严格大于 bottom 的 top-1。"
        f"实测 s_upper={top1_upper['height_fraction']} s_bottom={top1_bottom['height_fraction']}。"
    )


# ==========================================================================
# upper_body 与 bottom 的排序应负相关(Kendall τ < 0)。
# ==========================================================================

def test_ranking_kendall_tau_negative_between_regions():
    """upper_body 与 bottom 两种约束下的候选排序应负相关。

    排序路径由 selection.regions 的任务无关偏好函数提供。
    """
    candidates = _make_candidates()
    all_ids = [c["id"] for c in candidates]
    from demo_graph_lab.selection.regions import rank_by_region
    order_upper = [c["id"] for c in rank_by_region(candidates, "upper_body")]
    order_bottom = [c["id"] for c in rank_by_region(candidates, "bottom")]
    rank_upper = _rank_from_order(order_upper, all_ids)
    rank_bottom = _rank_from_order(order_bottom, all_ids)
    tau = _kendall_tau(rank_upper, rank_bottom)
    assert tau < 0, (
        "upper_body 与 bottom 的候选排序应负相关(τ<0),"
        f"实测 τ={tau}。若 τ≥0 说明 region 标签未真正翻转排序。"
    )
