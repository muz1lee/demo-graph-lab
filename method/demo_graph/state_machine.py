"""节点内状态机：低频闭环阶段，不展开伺服 tick。"""

from __future__ import annotations

from enum import Enum


class NodePhase(str, Enum):
    READY = "READY"
    RESOLVING_HOLES = "RESOLVING_HOLES"
    CANDIDATES_READY = "CANDIDATES_READY"
    ADMITTED = "ADMITTED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    RECOVERABLE = "RECOVERABLE"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            NodePhase.SUCCEEDED,
            NodePhase.RECOVERABLE,
            NodePhase.FAILED,
        }


_ALLOWED: dict[NodePhase, frozenset[NodePhase]] = {
    NodePhase.READY: frozenset({NodePhase.RESOLVING_HOLES, NodePhase.FAILED}),
    NodePhase.RESOLVING_HOLES: frozenset(
        {
            NodePhase.CANDIDATES_READY,
            NodePhase.SUCCEEDED,  # 目标已满足，直接跳过
            NodePhase.RECOVERABLE,
            NodePhase.FAILED,
        }
    ),
    NodePhase.CANDIDATES_READY: frozenset(
        {
            NodePhase.ADMITTED,
            NodePhase.RESOLVING_HOLES,  # 请求更多证据
            NodePhase.RECOVERABLE,
            NodePhase.FAILED,
        }
    ),
    NodePhase.ADMITTED: frozenset(
        {NodePhase.EXECUTING, NodePhase.RECOVERABLE, NodePhase.FAILED}
    ),
    NodePhase.EXECUTING: frozenset(
        {NodePhase.VERIFYING, NodePhase.RECOVERABLE, NodePhase.FAILED}
    ),
    NodePhase.VERIFYING: frozenset(
        {
            NodePhase.SUCCEEDED,
            NodePhase.RECOVERABLE,
            NodePhase.FAILED,
            NodePhase.RESOLVING_HOLES,  # 有界重试
        }
    ),
    NodePhase.SUCCEEDED: frozenset(),
    NodePhase.RECOVERABLE: frozenset(),
    NodePhase.FAILED: frozenset(),
}


def advance_phase(current: NodePhase, nxt: NodePhase) -> NodePhase:
    """校验并推进节点阶段；非法跳转直接失败。"""

    if not isinstance(current, NodePhase) or not isinstance(nxt, NodePhase):
        raise TypeError("phase must be NodePhase")
    allowed = _ALLOWED[current]
    if nxt not in allowed:
        raise ValueError(f"illegal phase transition {current.value} -> {nxt.value}")
    return nxt
