"""动作候选冻结与确定性选择：只能选、全拒或请求证据。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from ._json import content_digest, freeze_json


class CandidateDecision(str, Enum):
    SELECT = "select"
    REJECT_ALL = "reject_all"
    REQUEST_EVIDENCE = "request_evidence"


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    """不可变候选：绑定节点、观测 revision/digest、感知轨与图约束。"""

    node_id: str
    observation_revision: str
    observation_digest: str
    perception_track: str
    frame: str
    tcp_pose: tuple[float, ...]
    graph_constraints: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    provenance_reference: str

    def __post_init__(self) -> None:
        for name, value in (
            ("node_id", self.node_id),
            ("observation_revision", self.observation_revision),
            ("observation_digest", self.observation_digest),
            ("perception_track", self.perception_track),
            ("frame", self.frame),
            ("provenance_reference", self.provenance_reference),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.observation_digest.startswith("sha256:"):
            raise ValueError("observation_digest must be sha256:...")
        if len(self.tcp_pose) not in {3, 7}:
            raise ValueError("tcp_pose must be xyz or xyz+quat")
        object.__setattr__(self, "tcp_pose", tuple(float(x) for x in self.tcp_pose))
        object.__setattr__(self, "graph_constraints", tuple(self.graph_constraints))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        if not self.graph_constraints:
            raise ValueError("ActionCandidate requires at least one graph constraint")
        if not self.evidence_ids:
            raise ValueError("ActionCandidate requires at least one evidence id")

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True, slots=True)
class SelectionResult:
    kind: CandidateDecision
    selected_index: int | None = None
    reason: str = ""
    candidate_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is CandidateDecision.SELECT:
            if self.selected_index is None or self.selected_index < 0:
                raise ValueError("SELECT requires a non-negative selected_index")
        elif self.selected_index is not None:
            raise ValueError(f"{self.kind.value} must not carry selected_index")
        object.__setattr__(self, "candidate_digests", tuple(self.candidate_digests))


class CandidateSelector:
    """只能选择、全部拒绝或请求额外证据；不能修改候选或直接执行。"""

    def select(
        self,
        candidates: Sequence[ActionCandidate],
        *,
        index: int = 0,
        reason: str = "deterministic first admissible",
    ) -> SelectionResult:
        frozen = tuple(candidates)
        if not frozen:
            return SelectionResult(
                kind=CandidateDecision.REJECT_ALL,
                reason="no candidates",
            )
        if index < 0 or index >= len(frozen):
            raise IndexError("candidate index out of range")
        # 冻结摘要，防止调用方事后改写候选集合而不被察觉。
        digests = tuple(item.digest for item in frozen)
        _ = freeze_json({"digests": digests})
        return SelectionResult(
            kind=CandidateDecision.SELECT,
            selected_index=index,
            reason=reason,
            candidate_digests=digests,
        )

    def reject_all(
        self,
        candidates: Sequence[ActionCandidate],
        *,
        reason: str,
    ) -> SelectionResult:
        if not reason.strip():
            raise ValueError("reject_all requires a reason")
        return SelectionResult(
            kind=CandidateDecision.REJECT_ALL,
            reason=reason,
            candidate_digests=tuple(item.digest for item in candidates),
        )

    def request_evidence(
        self,
        candidates: Sequence[ActionCandidate],
        *,
        reason: str,
    ) -> SelectionResult:
        if not reason.strip():
            raise ValueError("request_evidence requires a reason")
        return SelectionResult(
            kind=CandidateDecision.REQUEST_EVIDENCE,
            reason=reason,
            candidate_digests=tuple(item.digest for item in candidates),
        )
