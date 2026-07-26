"""抓取候选适配器：对接 WHT GraspNet wrapper，不含禁止再分发的源码/权重。"""

from .service import GraspProposal, GraspProposalService, GraspProposalError

__all__ = ["GraspProposal", "GraspProposalError", "GraspProposalService"]
