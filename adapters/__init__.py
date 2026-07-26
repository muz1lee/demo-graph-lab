"""可信运行时适配器包。

子包边界：
- ``knowin_world``：EvalServer / pipeline / runtime doctor
- ``demo_bundle``：演示证据加载
- ``grasp_proposals``：GraspNet 候选边界
- ``observability``：审计与 RunManifest
"""

from .contracts import EvidenceRef, MethodResult
from .knowin_world import KnowinWorldAdapter, PipelineClient, RuntimeDoctor
from .m1_bindings import BrokerPolicyBindings
from .method_broker import MethodBroker

__all__ = [
    "BrokerPolicyBindings",
    "EvidenceRef",
    "KnowinWorldAdapter",
    "MethodBroker",
    "MethodResult",
    "PipelineClient",
    "RuntimeDoctor",
]
