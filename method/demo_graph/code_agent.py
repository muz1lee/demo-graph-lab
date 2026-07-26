"""把安全约束图编译为受限 Python node policy。

这里的 Code Agent 边界只生成声明式 node handlers；生成物不能直接访问
机器人、网络、文件系统或 evaluator。实际动作只能通过调用方提供的可信
controller registry 绑定，运行语义继续由 :class:`PythonNodePolicy` 承担。
"""

from __future__ import annotations

import hashlib
import pprint
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .isolation import scan_policy_source
from .models import ConstraintGraph, Node
from .runner import (
    ControllerResult,
    GoalSatisfied,
    Observation,
    Observe,
    PythonNodePolicy,
)


class CodeAgentCompileError(ValueError):
    """输入图不能安全地编译为受限 node policy。"""


_ALLOWED_DECISIONS = (
    "request_evidence",
    "execute_controller",
    "retry",
    "complete",
    "fail",
)


@dataclass(frozen=True, slots=True)
class CompiledPolicyArtifact:
    """Code Agent 的可审计输出及其冻结身份。"""

    input_graph_digest: str
    graph_digest: str
    code_digest: str
    source: str
    node_ids: tuple[str, ...]
    controller_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "demo_graph.compiled_policy.v1",
            "input_graph_digest": self.input_graph_digest,
            "graph_digest": self.graph_digest,
            "code_digest": self.code_digest,
            "node_ids": list(self.node_ids),
            "controller_refs": list(self.controller_refs),
            "allowed_decisions": list(_ALLOWED_DECISIONS),
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.source, encoding="utf-8")
        actual = _source_digest(target.read_text(encoding="utf-8"))
        if actual != self.code_digest:
            raise CodeAgentCompileError(
                f"compiled policy write changed digest: {actual}"
            )
        return target

    def bind(
        self,
        graph: ConstraintGraph,
        *,
        observe: Observe,
        goal_satisfied: GoalSatisfied,
        controllers: Mapping[
            str, Callable[[Node, Observation], ControllerResult]
        ],
    ) -> PythonNodePolicy:
        """加载已静态扫描的生成物并绑定可信 runtime callables。"""

        if graph.digest != self.graph_digest:
            raise CodeAgentCompileError(
                "compiled policy graph digest does not match the supplied graph"
            )
        scan_policy_source(self.source)
        namespace: dict[str, Any] = {}
        exec(compile(self.source, "<compiled-policy>", "exec"), namespace)
        builder = namespace.get("build_policy")
        if not callable(builder):
            raise CodeAgentCompileError(
                "compiled policy does not expose build_policy"
            )
        policy = builder(graph, observe, goal_satisfied, controllers)
        if not isinstance(policy, PythonNodePolicy):
            raise CodeAgentCompileError(
                "compiled build_policy returned the wrong policy type"
            )
        return policy


class RestrictedCodeAgentCompiler:
    """确定性、可审计的 Code Agent 编译边界。

    该实现不在 runtime 调用 LLM。它把图中的 node/constraint/hole 结构
    编译成固定模板，并拒绝任何未登记的 controller。后续若接入模型，
    其输出仍必须落到同一模板和静态检查后才能执行。
    """

    def __init__(self, trusted_controller_refs: Sequence[str]) -> None:
        refs = tuple(sorted(set(trusted_controller_refs)))
        if not refs:
            raise ValueError("trusted controller registry must not be empty")
        if any(
            not isinstance(ref, str) or not ref.startswith("trusted.")
            for ref in refs
        ):
            raise ValueError(
                "trusted controller refs must be non-empty trusted.* names"
            )
        self._trusted_controller_refs = refs

    def compile(
        self,
        graph: ConstraintGraph,
        *,
        input_graph_digest: str | None = None,
    ) -> CompiledPolicyArtifact:
        nodes = _linear_nodes(graph)
        if len(nodes) != len(graph.nodes):
            raise CodeAgentCompileError(
                "all graph nodes must be reachable from entry_node"
            )
        used_refs = tuple(sorted({node.controller_ref for node in nodes}))
        unknown = set(used_refs) - set(self._trusted_controller_refs)
        if unknown:
            raise CodeAgentCompileError(
                f"graph requests untrusted controllers: {sorted(unknown)}"
            )

        source = _render_source(graph, nodes, used_refs)
        scan_policy_source(source)
        try:
            compile(source, "<compiled-policy>", "exec")
        except SyntaxError as exc:
            raise CodeAgentCompileError(
                f"generated policy is invalid Python: {exc}"
            ) from exc
        return CompiledPolicyArtifact(
            input_graph_digest=input_graph_digest or graph.digest,
            graph_digest=graph.digest,
            code_digest=_source_digest(source),
            source=source,
            node_ids=tuple(node.node_id for node in nodes),
            controller_refs=used_refs,
        )


def select_linear_action_cycle(
    graph: ConstraintGraph,
    action_sequence: Sequence[str],
    *,
    cycle_index: int = 0,
) -> ConstraintGraph:
    """从线性图中选一个连续 action 周期，不改写任何约束或 hole。"""

    actions = tuple(action_sequence)
    if not actions or any(
        not isinstance(action, str) or not action.strip()
        for action in actions
    ):
        raise ValueError("action_sequence must contain non-empty actions")
    if cycle_index < 0:
        raise ValueError("cycle_index must be >= 0")

    nodes = _linear_nodes(graph)
    matches = [
        index
        for index in range(len(nodes) - len(actions) + 1)
        if tuple(node.action for node in nodes[index : index + len(actions)])
        == actions
    ]
    if cycle_index >= len(matches):
        raise CodeAgentCompileError(
            f"graph contains {len(matches)} matching action cycle(s), "
            f"cannot select index {cycle_index}"
        )
    start = matches[cycle_index]
    selected = list(nodes[start : start + len(actions)])
    if (
        start == 0
        and len(selected) == len(nodes)
        and selected[-1].next_node is None
    ):
        return graph

    selected[-1] = replace(selected[-1], next_node=None)
    return ConstraintGraph(
        graph_id=f"{graph.graph_id}__cycle_{cycle_index + 1}",
        entry_node=selected[0].node_id,
        nodes=tuple(selected),
        provenance=graph.provenance,
        schema_version=graph.schema_version,
    )


def _linear_nodes(graph: ConstraintGraph) -> tuple[Node, ...]:
    nodes: list[Node] = []
    visited: set[str] = set()
    current: str | None = graph.entry_node
    while current is not None:
        if current in visited:
            raise CodeAgentCompileError(
                f"graph contains a cycle at node {current!r}"
            )
        visited.add(current)
        node = graph.node(current)
        nodes.append(node)
        current = node.next_node
    return tuple(nodes)


def _handler_tuple(node: Node) -> tuple[Any, ...]:
    return (
        node.node_id,
        node.action,
        node.goal,
        node.controller_ref,
        tuple(item.constraint_id for item in node.constraints),
        tuple(item.hole_id for item in node.holes),
        node.max_attempts,
        node.next_node,
    )


def _render_source(
    graph: ConstraintGraph,
    nodes: Sequence[Node],
    controller_refs: Sequence[str],
) -> str:
    handlers = tuple(_handler_tuple(node) for node in nodes)
    return (
        '"""Generated restricted node policy. Do not edit."""\n\n'
        "from method.demo_graph.runner import PythonNodePolicy\n\n"
        f"COMPILED_GRAPH_DIGEST = {graph.digest!r}\n"
        f"NODE_HANDLERS = {pprint.pformat(handlers, width=100, sort_dicts=True)}\n"
        f"TRUSTED_CONTROLLER_REFS = {tuple(controller_refs)!r}\n"
        f"ALLOWED_DECISIONS = {_ALLOWED_DECISIONS!r}\n\n"
        "def _handler_tuple(node):\n"
        "    return (\n"
        "        node.node_id,\n"
        "        node.action,\n"
        "        node.goal,\n"
        "        node.controller_ref,\n"
        "        tuple(item.constraint_id for item in node.constraints),\n"
        "        tuple(item.hole_id for item in node.holes),\n"
        "        node.max_attempts,\n"
        "        node.next_node,\n"
        "    )\n\n"
        "def build_policy(graph, observe, goal_satisfied, controllers):\n"
        "    if graph.digest != COMPILED_GRAPH_DIGEST:\n"
        "        raise ValueError('compiled graph digest mismatch')\n"
        "    actual = tuple(_handler_tuple(node) for node in graph.nodes)\n"
        "    if actual != NODE_HANDLERS:\n"
        "        raise ValueError('compiled node handlers mismatch')\n"
        "    missing = tuple(\n"
        "        ref for ref in TRUSTED_CONTROLLER_REFS if ref not in controllers\n"
        "    )\n"
        "    if missing:\n"
        "        raise ValueError(f'missing trusted controllers: {missing}')\n"
        "    selected = {\n"
        "        ref: controllers[ref] for ref in TRUSTED_CONTROLLER_REFS\n"
        "    }\n"
        "    return PythonNodePolicy(\n"
        "        graph=graph,\n"
        "        observe=observe,\n"
        "        goal_satisfied=goal_satisfied,\n"
        "        controllers=selected,\n"
        "    )\n"
    )


def _source_digest(source: str) -> str:
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"
