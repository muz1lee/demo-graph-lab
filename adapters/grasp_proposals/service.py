"""GraspNet 提案的方法侧薄封装。

真实模型与 `graspnet-baseline` 源码/权重不得进入本仓；本模块只定义
候选数据结构、排序接口，以及可选的外部 HTTP/本地服务调用边界。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from method.demo_graph.candidates import ActionCandidate

from .._json import content_digest
from ..contracts import EvidenceRef, MethodResult, assert_method_payload_safe


class GraspProposalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GraspProposal:
    proposal_id: str
    tcp_pose: tuple[float, ...]
    score: float
    frame: str
    source: str = "graspnet"

    def __post_init__(self) -> None:
        if not self.proposal_id.strip():
            raise ValueError("proposal_id must not be empty")
        if len(self.tcp_pose) not in {3, 7}:
            raise ValueError("tcp_pose must be xyz or xyz+quat")
        object.__setattr__(self, "tcp_pose", tuple(float(x) for x in self.tcp_pose))


@dataclass(slots=True)
class GraspProposalService:
    """可选外部 GraspNet 服务客户端；不可用时显式失败，不回退 GT。"""

    endpoint: str | None = None
    timeout_s: float = 10.0

    def propose_from_rgbd(
        self,
        *,
        rgb_digest: str,
        depth_digest: str,
        mask_digest: str | None = None,
        top_k: int = 5,
    ) -> MethodResult:
        if self.endpoint is None:
            raise GraspProposalError(
                "GraspNet endpoint is not configured; "
                "install/runtime-provide the external service, do not use GT poses"
            )
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        body = {
            "rgb_digest": rgb_digest,
            "depth_digest": depth_digest,
            "mask_digest": mask_digest,
            "top_k": top_k,
        }
        assert_method_payload_safe(body)
        request = urllib.request.Request(
            self.endpoint.rstrip("/") + "/propose",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraspProposalError(f"grasp proposal service failed: {exc}") from exc
        proposals = _parse_proposals(payload)
        value = {
            "proposals": [
                {
                    "proposal_id": item.proposal_id,
                    "tcp_pose": list(item.tcp_pose),
                    "score": item.score,
                    "frame": item.frame,
                    "source": item.source,
                }
                for item in proposals
            ]
        }
        evidence = EvidenceRef.from_value(
            evidence_id=f"grasp:{content_digest(value)[7:19]}",
            source="runtime_perception",
            value=value,
        )
        return MethodResult(value=value, evidence=(evidence,))

    def to_action_candidates(
        self,
        proposals: Sequence[GraspProposal],
        *,
        node_id: str,
        observation_revision: str,
        observation_digest: str,
        graph_constraints: Sequence[str],
        evidence_ids: Sequence[str],
    ) -> tuple[ActionCandidate, ...]:
        return tuple(
            ActionCandidate(
                node_id=node_id,
                observation_revision=observation_revision,
                observation_digest=observation_digest,
                perception_track="graspnet",
                frame=item.frame,
                tcp_pose=item.tcp_pose,
                graph_constraints=tuple(graph_constraints),
                evidence_ids=tuple(evidence_ids),
                provenance_reference=f"{item.source}:{item.proposal_id}",
            )
            for item in proposals
        )


def _parse_proposals(payload: Any) -> tuple[GraspProposal, ...]:
    if not isinstance(payload, Mapping):
        raise GraspProposalError("proposal response must be an object")
    rows = payload.get("proposals")
    if not isinstance(rows, list) or not rows:
        raise GraspProposalError("proposal response missing proposals[]")
    out: list[GraspProposal] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise GraspProposalError(f"proposal[{index}] must be an object")
        pose = row.get("tcp_pose") or row.get("pose")
        if not isinstance(pose, (list, tuple)):
            raise GraspProposalError(f"proposal[{index}] missing tcp_pose")
        out.append(
            GraspProposal(
                proposal_id=str(row.get("proposal_id") or f"g{index}"),
                tcp_pose=tuple(float(x) for x in pose),
                score=float(row.get("score", 0.0)),
                frame=str(row.get("frame") or "base"),
                source=str(row.get("source") or "graspnet"),
            )
        )
    return tuple(out)
