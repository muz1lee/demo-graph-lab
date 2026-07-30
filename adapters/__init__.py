"""可信运行时适配器包。

子包边界：
- ``knowin_world``：EvalServer / pipeline / runtime doctor
- ``demo_bundle``：演示证据加载
- ``grasp_proposals``：GraspNet 候选边界
- ``observability``：审计与 RunManifest

导入策略（2026-07-30 改为惰性）：本模块此前在包初始化时 eager import 全部子模块，
其中 ``m1_bindings`` 会从 ``method.demo_graph`` 拉起 13 个模块。后果是
``harness/kwadapter.py`` 只需要一个 66 行、纯 stdlib 的 ``PipelineClient``，
却把整棵 v1 方法树焊进 Phase 1 主链路——``method/`` 因此变得不可删、不可独立演化。

改用 PEP 562 的模块级 ``__getattr__`` 后，``from adapters import X`` 的写法完全不变，
但只有真正被访问到的符号才会触发其所在子模块的导入。
"""

from typing import TYPE_CHECKING

# 符号 → (子模块, 属性名)。新增导出项时在此登记即可。
_LAZY_EXPORTS = {
    "EvidenceRef": (".contracts", "EvidenceRef"),
    "MethodResult": (".contracts", "MethodResult"),
    "KnowinWorldAdapter": (".knowin_world", "KnowinWorldAdapter"),
    "PipelineClient": (".knowin_world", "PipelineClient"),
    "RuntimeDoctor": (".knowin_world", "RuntimeDoctor"),
    "BrokerPolicyBindings": (".m1_bindings", "BrokerPolicyBindings"),
    "MethodBroker": (".method_broker", "MethodBroker"),
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from importlib import import_module

    return getattr(import_module(module_name, __name__), attribute)


def __dir__() -> list[str]:
    return list(__all__)


if TYPE_CHECKING:  # 仅供类型检查与 IDE 跳转，运行期不执行
    from .contracts import EvidenceRef, MethodResult
    from .knowin_world import KnowinWorldAdapter, PipelineClient, RuntimeDoctor
    from .m1_bindings import BrokerPolicyBindings
    from .method_broker import MethodBroker
