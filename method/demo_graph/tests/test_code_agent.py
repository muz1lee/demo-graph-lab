from __future__ import annotations

from dataclasses import replace

import pytest

from method.demo_graph import (
    CodeAgentCompileError,
    Constraint,
    ConstraintGraph,
    ControllerResult,
    Node,
    Observation,
    Provenance,
    ProvenanceSource,
    RestrictedCodeAgentCompiler,
    select_linear_action_cycle,
)
from method.demo_graph.metric_scan import scan_paths


_ACTIONS = ("pick", "reorient", "align", "insert", "verify")


def _source(
    kind: ProvenanceSource = ProvenanceSource.DEMO_VIDEO,
) -> Provenance:
    return Provenance(source=kind, reference="test evidence")


def _two_cycle_graph() -> ConstraintGraph:
    nodes = []
    for cycle in (1, 2):
        for index, action in enumerate(_ACTIONS):
            node_id = f"{action}_{cycle}"
            if index + 1 < len(_ACTIONS):
                next_node = f"{_ACTIONS[index + 1]}_{cycle}"
            elif cycle == 1:
                next_node = "pick_2"
            else:
                next_node = None
            nodes.append(
                Node(
                    node_id=node_id,
                    action=action,
                    goal=f"tube_{cycle}_{action}_done",
                    controller_ref=f"trusted.{action}",
                    constraints=(
                        Constraint(
                            constraint_id=f"{action}_constraint_{cycle}",
                            description=f"{action} relation from demo",
                            provenance=_source(),
                        ),
                    ),
                    provenance=_source(),
                    next_node=next_node,
                )
            )
    return ConstraintGraph(
        graph_id="extracted_two_cycle_graph",
        entry_node="pick_1",
        nodes=tuple(nodes),
        provenance=_source(),
    )


def _compiler() -> RestrictedCodeAgentCompiler:
    return RestrictedCodeAgentCompiler(
        tuple(f"trusted.{action}" for action in _ACTIONS)
    )


def test_select_cycle_preserves_constraints_and_terminates() -> None:
    graph = _two_cycle_graph()
    selected = select_linear_action_cycle(
        graph,
        _ACTIONS,
        cycle_index=1,
    )

    assert [node.node_id for node in selected.nodes] == [
        "pick_2",
        "reorient_2",
        "align_2",
        "insert_2",
        "verify_2",
    ]
    assert selected.nodes[-1].next_node is None
    assert (
        selected.nodes[0].constraints[0].constraint_id
        == graph.node("pick_2").constraints[0].constraint_id
    )
    assert graph.node("verify_1").next_node == "pick_2"


def test_compiled_source_is_clean_and_executes_selected_graph(
    tmp_path,
) -> None:
    input_graph = _two_cycle_graph()
    graph = select_linear_action_cycle(input_graph, _ACTIONS)
    compiled = _compiler().compile(
        graph,
        input_graph_digest=input_graph.digest,
    )
    repeated = _compiler().compile(
        graph,
        input_graph_digest=input_graph.digest,
    )

    assert compiled.code_digest == repeated.code_digest
    assert compiled.input_graph_digest == input_graph.digest
    assert compiled.graph_digest == graph.digest
    path = compiled.write(tmp_path / "compiled_policy.py")
    assert scan_paths((path,)).clean

    state = {node.goal: False for node in graph.nodes}
    revision = 0

    def observe(node):
        nonlocal revision
        revision += 1
        return Observation(
            revision=f"fake-{revision}",
            payload=dict(state),
            provenance=_source(ProvenanceSource.RUNTIME_PERCEPTION),
        )

    def controller(node, observation):
        del observation
        state[node.goal] = True
        return ControllerResult(ok=True)

    policy = compiled.bind(
        graph,
        observe=observe,
        goal_satisfied=lambda node, observation: bool(
            observation.payload[node.goal]
        ),
        controllers={
            f"trusted.{action}": controller for action in _ACTIONS
        },
    )
    result = policy.run()

    assert result.succeeded is True
    assert [node.node_id for node in result.nodes] == list(compiled.node_ids)


def test_compiler_rejects_untrusted_controller() -> None:
    graph = _two_cycle_graph()
    first = graph.nodes[0]
    bad = replace(first, controller_ref="trusted.unregistered")
    bad_graph = ConstraintGraph(
        graph_id=graph.graph_id,
        entry_node=graph.entry_node,
        nodes=(bad, *graph.nodes[1:]),
        provenance=graph.provenance,
    )

    with pytest.raises(CodeAgentCompileError, match="untrusted"):
        _compiler().compile(bad_graph)


def test_compiled_policy_rejects_graph_substitution() -> None:
    graph = select_linear_action_cycle(_two_cycle_graph(), _ACTIONS)
    compiled = _compiler().compile(graph)
    changed = ConstraintGraph(
        graph_id=f"{graph.graph_id}_changed",
        entry_node=graph.entry_node,
        nodes=graph.nodes,
        provenance=graph.provenance,
    )

    with pytest.raises(CodeAgentCompileError, match="digest"):
        compiled.bind(
            changed,
            observe=lambda node: None,  # type: ignore[arg-type]
            goal_satisfied=lambda node, observation: False,
            controllers={},
        )
