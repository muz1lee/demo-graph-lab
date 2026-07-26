from __future__ import annotations

import json
import unittest
from pathlib import Path

from method.demo_graph import (
    Constraint,
    ConstraintGraph,
    ControllerResult,
    Node,
    NodeStatus,
    Observation,
    Provenance,
    ProvenanceError,
    ProvenanceSource,
    PythonNodePolicy,
)
from method.demo_graph.examples.m1_fake import run_example


EXAMPLE_GRAPH = (
    Path(__file__).parents[1] / "examples" / "m1_graph.json"
)


def source(kind=ProvenanceSource.TASK_INSTRUCTION, reference="test"):
    return Provenance(source=kind, reference=reference)


def one_node_graph(*, max_attempts=1):
    constraint = Constraint(
        constraint_id="goal_constraint",
        description="observable goal must hold",
        provenance=source(),
    )
    node = Node(
        node_id="node",
        action="pick",
        goal="done",
        controller_ref="trusted.node",
        constraints=(constraint,),
        provenance=source(),
        max_attempts=max_attempts,
    )
    return ConstraintGraph(
        graph_id="graph",
        entry_node="node",
        nodes=(node,),
        provenance=source(),
    )


class GraphTests(unittest.TestCase):
    def test_example_loads_and_round_trips_with_stable_digest(self):
        graph = ConstraintGraph.load_json(EXAMPLE_GRAPH)
        round_trip = ConstraintGraph.from_json(graph.to_json())
        self.assertEqual(graph.digest, round_trip.digest)
        graph.assert_action_sequence(
            ("pick", "reorient", "align", "insert", "verify")
        )

    def test_derived_oracle_provenance_is_rejected_recursively(self):
        oracle = source(
            ProvenanceSource.PRIVILEGED_ORACLE,
            "simulator exact object pose",
        )
        derived = Provenance.derived("renamed pose estimate", oracle)
        base = one_node_graph()
        with self.assertRaises(ProvenanceError):
            ConstraintGraph(
                graph_id=base.graph_id,
                entry_node=base.entry_node,
                nodes=base.nodes,
                provenance=derived,
            )

    def test_unknown_transition_is_rejected(self):
        base = one_node_graph()
        node = base.nodes[0]
        bad = Node(
            node_id=node.node_id,
            action=node.action,
            goal=node.goal,
            controller_ref=node.controller_ref,
            constraints=node.constraints,
            provenance=node.provenance,
            next_node="missing",
        )
        with self.assertRaises(ValueError):
            ConstraintGraph(
                graph_id="bad",
                entry_node="node",
                nodes=(bad,),
                provenance=source(),
            )


class RunnerTests(unittest.TestCase):
    def _observation(self, revision, done):
        return Observation(
            revision=revision,
            payload={"done": done},
            provenance=source(
                ProvenanceSource.RUNTIME_PERCEPTION,
                f"sensor frame {revision}",
            ),
        )

    def test_m1_example_runs_and_skips_unnecessary_reorientation(self):
        result = run_example()
        self.assertTrue(result.succeeded)
        self.assertEqual(
            [node.action for node in result.nodes],
            ["pick", "reorient", "align", "insert", "verify"],
        )
        self.assertEqual(result.nodes[1].status, NodeStatus.SKIPPED)
        self.assertEqual(result.nodes[1].attempts, 0)

    def test_recoverable_failure_reobserves_and_respects_attempt_bound(self):
        graph = one_node_graph(max_attempts=2)
        state = {"done": False, "revision": 0, "calls": 0}

        def observe(node):
            state["revision"] += 1
            return self._observation(str(state["revision"]), state["done"])

        def controller(node, observation):
            state["calls"] += 1
            if state["calls"] == 1:
                return ControllerResult(
                    ok=False,
                    reason="temporary grasp miss",
                    recoverable=True,
                    constraint_id="goal_constraint",
                )
            state["done"] = True
            return ControllerResult(ok=True)

        result = PythonNodePolicy(
            graph,
            observe,
            lambda node, observation: bool(observation.payload["done"]),
            {"trusted.node": controller},
        ).run()
        self.assertTrue(result.succeeded)
        self.assertEqual(result.nodes[0].attempts, 2)
        self.assertEqual(result.nodes[0].observed_revisions, ("1", "2", "3"))
        self.assertEqual(state["calls"], 2)

    def test_failure_is_attributed_to_graph_constraint(self):
        graph = one_node_graph(max_attempts=1)
        result = PythonNodePolicy(
            graph,
            lambda node: self._observation("1", False),
            lambda node, observation: False,
            {
                "trusted.node": lambda node, observation: ControllerResult(
                    ok=False,
                    reason="unreachable",
                    constraint_id="goal_constraint",
                )
            },
        ).run()
        self.assertFalse(result.succeeded)
        self.assertEqual(
            result.nodes[0].failure_constraint_id, "goal_constraint"
        )
        self.assertEqual(result.nodes[0].attempts, 1)

    def test_unknown_failure_attribution_is_a_contract_error(self):
        graph = one_node_graph()
        result = PythonNodePolicy(
            graph,
            lambda node: self._observation("1", False),
            lambda node, observation: False,
            {
                "trusted.node": lambda node, observation: ControllerResult(
                    ok=False,
                    reason="bad attribution",
                    constraint_id="not_in_graph",
                )
            },
        ).run()
        self.assertFalse(result.succeeded)
        self.assertEqual(
            result.nodes[0].failure_constraint_id, "contract:node"
        )

    def test_goal_already_satisfied_skips_controller(self):
        graph = one_node_graph()
        called = []
        result = PythonNodePolicy(
            graph,
            lambda node: self._observation("1", True),
            lambda node, observation: True,
            {
                "trusted.node": lambda node, observation: (
                    called.append(True) or ControllerResult(ok=True)
                )
            },
        ).run()
        self.assertTrue(result.succeeded)
        self.assertEqual(result.nodes[0].status, NodeStatus.SKIPPED)
        self.assertEqual(called, [])

    def test_missing_controller_fails_before_execution(self):
        with self.assertRaises(ValueError):
            PythonNodePolicy(
                one_node_graph(),
                lambda node: self._observation("1", False),
                lambda node, observation: False,
                {},
            )


if __name__ == "__main__":
    unittest.main()
