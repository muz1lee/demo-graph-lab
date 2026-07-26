"""策略后端接口：主方法为 Python 节点策略，YAML 仅作 baseline。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from .models import ConstraintGraph
from .runner import (
    GoalSatisfied,
    Observe,
    PolicyRunResult,
    PythonNodePolicy,
    TrustedController,
)


class PolicyBackend(ABC):
    """生成或装载可执行策略的后端。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def is_primary(self) -> bool: ...

    @abstractmethod
    def generate_policy(self, spec: Mapping[str, Any]) -> Any:
        """根据图/证据生成策略工件（主方法）或拒绝（legacy）。"""


class PythonNodePolicyBackend(PolicyBackend):
    """主方法：受限 Python node policy。"""

    @property
    def name(self) -> str:
        return "python_node_policy"

    @property
    def is_primary(self) -> bool:
        return True

    def generate_policy(self, spec: Mapping[str, Any]) -> PythonNodePolicy:
        graph = spec.get("graph")
        if not isinstance(graph, ConstraintGraph):
            raise TypeError("PythonNodePolicyBackend requires ConstraintGraph in spec['graph']")
        observe = spec.get("observe")
        goal_satisfied = spec.get("goal_satisfied")
        controllers = spec.get("controllers")
        if not callable(observe) or not callable(goal_satisfied):
            raise TypeError("observe/goal_satisfied callables are required")
        if not isinstance(controllers, Mapping) or not controllers:
            raise TypeError("controllers mapping is required")
        return PythonNodePolicy(
            graph=graph,
            observe=observe,  # type: ignore[arg-type]
            goal_satisfied=goal_satisfied,  # type: ignore[arg-type]
            controllers=controllers,  # type: ignore[arg-type]
        )

    def run(self, policy: PythonNodePolicy) -> PolicyRunResult:
        return policy.run()


class LegacyYamlBackend(PolicyBackend):
    """包装现有 WHT YAML 路径；不改原算法，不作为当前主线。"""

    @property
    def name(self) -> str:
        return "legacy_yaml"

    @property
    def is_primary(self) -> bool:
        return False

    def generate_policy(self, spec: Mapping[str, Any]) -> Any:
        raise NotImplementedError(
            "LegacyYamlBackend 仅保留为 baseline 入口占位；"
            "请通过 components/knowin-skill-manager 原路径运行，不在主方法中生成 YAML。"
        )
