"""[phase1 · graspfunnel] 候选 → 两层漏斗 → 三态选择(P0-10,离线 mock 候选)。

设计依据:docs/TODO.md §2 P0-10 行 + §1.2 C-4/C-5;docs/PROPOSAL.md v4 §2.1/§2.3。

━━ 漏斗只有两层(MVS 阶段的硬事实,报表不得写成三层)━━
  本任务组装的漏斗**只有 L1+L2**:
    • L1  硬可行(一票否决)——**唯一的淘汰层**。候选逐个过注入的可行性谓词
      (可达 / 无碰 / 开口合法),任一为假即被淘汰。离线用注入的 callable,
      不含任何机器人调用、不含 LLM。
    • L2  偏好排序(**不淘汰,只改序**)——region 经 regions.rank_by_region、
      cone 经 regions.rank_by_cone(PROPOSAL v4 §2.3:「L2 偏好排序」;§2.1:
      反传不生成数值、不淘汰,除 L1 硬可行外)。输出与 L1 存活集**等长**。
    • 选择——复用 method.demo_graph.candidates.CandidateSelector 的三态
      (SELECT / REJECT_ALL / REQUEST_EVIDENCE),**不重写**。

  **L3(k 步下游反传排序)不在本任务内**——它是后置任务 T-BP(PROPOSAL v4 §2.3
  升格为方法主体)。本模块只给它留一个参数位 `downstream_rank_fn=None`:默认 None
  时行为 = 纯 L1+L2;T-BP 交付前,任何 funnel 报表**只能写两层**(v3.1「报表只写
  两层」条款在 v4 §2.3 已升格,但 T-BP 未交付前如实两层)。

━━ 计数落盘(将来 funnel 首表 P1-09 与 CC-0 的数据接口)━━
  FunnelResult 携带 per-layer counts:
    L1_in            进 L1 的候选数(= 原始候选集大小)
    L1_out           过 L1 存活数(L1_in − 被淘汰数)
    L2_reordered     L2 是否改变了存活集次序(bool→int:1 改序 / 0 未改)
    top1_changed_by_L2  L2 排序后的 top-1 是否 ≠ L2 前的 top-1(CC-0 的原子信号)

━━ 空集处置(TODO 明令,不静默退化、不放宽重试)━━
  L1 后存活为空 → 选择返回 CandidateSelector.reject_all(REJECT_ALL) 且
  **抛 binding.UnsolvedHole(reason="funnel_empty")**。不回退、不放宽谓词重试。

━━ 红线━━
  • 零任务名 / 物体名 / 度量魔数(mock 候选的构造数值是调用方 fixture,不在本文件)。
  • 运行期零 LLM、零网络、零机器人:L1 谓词与 L3 反传都由调用方注入 callable。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import regions
from .binding import UnsolvedHole

# 复用 P0(⚙代码在)的候选选择三态,不重写。经 method.demo_graph 树引用
# (adapters 惰性导入路径同源于此包);本模块只消费其 SELECT/REJECT_ALL/
# REQUEST_EVIDENCE 语义与不可变 SelectionResult。
from method.demo_graph.candidates import (
    ActionCandidate,
    CandidateDecision,
    CandidateSelector,
    SelectionResult,
)

__all__ = [
    "FunnelCounts",
    "FunnelResult",
    "run_funnel",
    "CandidateDecision",
    "SelectionResult",
]


# ==========================================================================
# 计数对象:每层 in/out。将来 P1-09 funnel 首表与 CC-0 直接读它。
# ==========================================================================
@dataclass(frozen=True, slots=True)
class FunnelCounts:
    """两层漏斗每层的进/出计数。字段齐全是 P1-09 / CC-0 的硬接口。"""

    L1_in: int
    L1_out: int
    L2_reordered: int          # 0/1:L2 是否改变存活集次序
    top1_changed_by_L2: int    # 0/1:L2 后 top-1 是否变化(CC-0 原子信号)


@dataclass(frozen=True, slots=True)
class FunnelResult:
    """漏斗结果:选择决定 + 存活/排序后的候选 + per-layer 计数 + 参照标签。

    selection      : CandidateSelector 产出的 SelectionResult(三态之一)。
    ranked         : L2 排序后的候选列表(dict 原样,不改内容只改序);L1 淘汰后为空则 []。
    counts         : FunnelCounts。
    layers         : 本次实际运行的漏斗层名(固定 ("L1","L2"));L3 未交付前不写它。
    region         : 本次 L2 用到的 region 标签(无则 None)。
    cone           : 本次 L2 用到的 cone 标签(无则 None)。
    """

    selection: SelectionResult
    ranked: list = field(default_factory=list)
    counts: FunnelCounts | None = None
    layers: tuple[str, ...] = ("L1", "L2")
    region: str | None = None
    cone: str | None = None

    @property
    def selected(self):
        """便捷取被选中候选;非 SELECT 态返回 None。"""
        if self.selection.kind is CandidateDecision.SELECT:
            return self.ranked[self.selection.selected_index]
        return None


# ==========================================================================
# L1:硬可行一票否决。唯一淘汰层。
# ==========================================================================
def _apply_l1(candidates, feasibility_predicates):
    """逐候选过所有可行性谓词;任一为假即淘汰。返回存活子列表(保序)。

    feasibility_predicates : callable 列表,每个 (candidate)->bool。
        离线注入(可达 / 无碰 / 开口合法);None 或空列表 = 不淘汰(全存活)。
        谓词返回非真(含抛异常路径由调用方负责,本层不吞异常)即淘汰该候选。
    """
    preds = list(feasibility_predicates or [])
    if not preds:
        return list(candidates or [])
    survivors = []
    for c in candidates or []:
        if all(bool(p(c)) for p in preds):
            survivors.append(c)
    return survivors


# ==========================================================================
# L2:偏好排序。不淘汰,只改序。region 与 cone 可叠加(先 cone 后 region:
# region 是最后一次稳定排序 → 主键;cone 在等 region 分的候选内保序 → 次键)。
# ==========================================================================
def _apply_l2(candidates, *, region, cone, cone_dir_key):
    """按 region / cone 偏好稳定排序。两者都给时叠加(稳定排序保证次键语义)。

    返回排序后的新列表(与输入等长,不删候选)。region/cone 均为 None → 原样返回。
    rim/handle 等 UNCHECKABLE region:rank_by_region 内部退化为恒等(不排序)。
    """
    ranked = list(candidates or [])
    # 先按次键 cone 稳定排序,再按主键 region 稳定排序:
    # Python sorted 稳定,后一次排序在等主键分内保留前一次的相对次序,
    # 于是最终序 = region 主、cone 次(叠加且稳定,不互相抹除)。
    if cone is not None:
        ranked = regions.rank_by_cone(ranked, cone, dir_key=cone_dir_key)
    if region is not None:
        ranked = regions.rank_by_region(ranked, region)
    return ranked


def _order_changed(before, after):
    """两列表是否为不同次序(按对象同一性 `is` 逐位比较;等长前提)。"""
    if len(before) != len(after):
        return True
    return any(b is not a for b, a in zip(before, after))


def _top1_changed(before, after):
    """top-1 是否变化(空集或任一为空 → 视为未变化:无 top-1 可比)。"""
    if not before or not after:
        return False
    return before[0] is not after[0]


# ==========================================================================
# 选择:复用 CandidateSelector 三态。
# ==========================================================================
def _to_action_candidate(c):
    """把 mock 候选 dict 适配成 CandidateSelector 消费的 ActionCandidate。

    候选 dict 须自带 CandidateSelector 冻结所需的 provenance 字段
    (node_id / observation_revision / observation_digest / perception_track /
     frame / tcp_pose / graph_constraints / evidence_ids / provenance_reference)。
    这些是绑定/审计字段,不是几何答案;几何字段(xyz / height_fraction /
    approach_dir / closure_axis)供 L1/L2 消费,与选择器无关。
    ActionCandidate 的不变式校验(sha256 摘要、非空约束/证据)由其自身 __post_init__ 保证。
    """
    return ActionCandidate(
        node_id=c["node_id"],
        observation_revision=c["observation_revision"],
        observation_digest=c["observation_digest"],
        perception_track=c["perception_track"],
        frame=c["frame"],
        tcp_pose=tuple(c["tcp_pose"]),
        graph_constraints=tuple(c["graph_constraints"]),
        evidence_ids=tuple(c["evidence_ids"]),
        provenance_reference=c["provenance_reference"],
    )


# ==========================================================================
# 入口
# ==========================================================================
def run_funnel(
    candidates,
    *,
    hole=None,
    feasibility_predicates=None,
    region=None,
    cone=None,
    cone_dir_key="approach_dir",
    request_evidence_reason=None,
    downstream_rank_fn=None,
    selector=None,
):
    """跑两层漏斗并做三态选择。离线、零 LLM、零机器人。

    candidates : mock 候选 dict 列表(位置 / 接近方向 / 闭合轴 + 选择器所需 provenance 字段)。
    hole       : 该候选集对应的洞 dict(仅用于空集时给 UnsolvedHole 归因,可为 None)。
    feasibility_predicates : L1 可行性谓词 callable 列表(注入)。None/空 = 不淘汰。
    region     : L2 region 标签(vocab.GRASP_REGIONS 之一)或 None。
    cone       : L2 cone 标签(vocab.APPROACH_CONES 之一)或 None。
    cone_dir_key : 候选里 approach 方向向量的键名(默认 "approach_dir")。
    request_evidence_reason : 非空字符串 → 存活集非空时改走 REQUEST_EVIDENCE 态
                 (调用方判定证据不足;传 None 走正常 SELECT)。
    downstream_rank_fn : **L3 留位(T-BP 注入 k 步反传排序)**。默认 None = 纯 L1+L2。
                 非 None 时不在本任务实现其语义;此处仅拒绝静默忽略——传入即报错,
                 提醒调用方 L3 尚未交付,避免误以为跑了三层。
    selector   : 可注入的 CandidateSelector(测试用);默认新建一个。

    返回 FunnelResult。L1 后为空 → 选择 REJECT_ALL 且抛 UnsolvedHole(funnel_empty)。
    """
    if downstream_rank_fn is not None:
        # L3 未交付(T-BP)。不静默忽略参数,也不假装跑了三层。
        raise NotImplementedError(
            "L3 下游反传排序尚未交付(任务 T-BP);downstream_rank_fn 目前只留位,"
            "不许注入。MVS 阶段漏斗只有 L1+L2。")

    sel = selector or CandidateSelector()
    original = list(candidates or [])
    l1_in = len(original)

    # ---- L1:唯一淘汰层 ----
    survivors = _apply_l1(original, feasibility_predicates)
    l1_out = len(survivors)

    # ---- 空集处置:不静默退化、不放宽重试 ----
    if not survivors:
        counts = FunnelCounts(
            L1_in=l1_in, L1_out=0, L2_reordered=0, top1_changed_by_L2=0)
        decision = sel.reject_all([], reason="funnel_empty")
        result = FunnelResult(
            selection=decision, ranked=[], counts=counts,
            region=region, cone=cone)
        raise UnsolvedHole(
            "graspfunnel: L1 硬可行后候选集为空",
            hole=hole, reason="funnel_empty")

    # ---- L2:偏好排序,不淘汰 ----
    ranked = _apply_l2(survivors, region=region, cone=cone,
                       cone_dir_key=cone_dir_key)
    reordered = 1 if _order_changed(survivors, ranked) else 0
    top1_changed = 1 if _top1_changed(survivors, ranked) else 0
    counts = FunnelCounts(
        L1_in=l1_in, L1_out=l1_out,
        L2_reordered=reordered, top1_changed_by_L2=top1_changed)

    # ---- 选择:复用三态 ----
    action_candidates = [_to_action_candidate(c) for c in ranked]
    if request_evidence_reason:
        decision = sel.request_evidence(
            action_candidates, reason=request_evidence_reason)
    else:
        # SELECT:排序后 top-1 即确定性首选(index=0)。
        decision = sel.select(
            action_candidates, index=0,
            reason="funnel L1+L2 top-1")

    return FunnelResult(
        selection=decision, ranked=ranked, counts=counts,
        region=region, cone=cone)
