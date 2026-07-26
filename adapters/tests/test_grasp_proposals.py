"""grasp_proposals 适配器测试。"""

from __future__ import annotations

import pytest

from adapters.grasp_proposals import GraspProposal, GraspProposalError, GraspProposalService


def test_unconfigured_service_fails_closed():
    service = GraspProposalService(endpoint=None)
    with pytest.raises(GraspProposalError):
        service.propose_from_rgbd(
            rgb_digest="sha256:" + ("a" * 64),
            depth_digest="sha256:" + ("b" * 64),
        )


def test_proposals_convert_to_action_candidates():
    service = GraspProposalService()
    proposals = (
        GraspProposal(
            proposal_id="g0",
            tcp_pose=(0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0),
            score=0.9,
            frame="base",
        ),
    )
    candidates = service.to_action_candidates(
        proposals,
        node_id="pick",
        observation_revision="r1",
        observation_digest="sha256:" + ("c" * 64),
        graph_constraints=("grasp_region",),
        evidence_ids=("ev-1",),
    )
    assert len(candidates) == 1
    assert candidates[0].perception_track == "graspnet"
    assert candidates[0].node_id == "pick"
