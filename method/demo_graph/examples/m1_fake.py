"""Run the M1 graph against an in-memory, non-oracle fake runtime.

This proves the graph/runner/controller seam. Replace the three callables in
``PythonNodePolicy`` with the Knowin World adapter for a live run.
"""

from __future__ import annotations

import json
from pathlib import Path

from method.demo_graph import (
    ConstraintGraph,
    ControllerResult,
    Observation,
    Provenance,
    ProvenanceSource,
    PythonNodePolicy,
)


def run_example():
    graph = ConstraintGraph.load_json(Path(__file__).with_name("m1_graph.json"))
    graph.assert_action_sequence(("pick", "reorient", "align", "insert", "verify"))

    state = {
        "tube_attached": False,
        "tube_upright": False,
        "tube_aligned": False,
        "tube_inserted": False,
        "task_verified": False,
    }
    revision = 0
    perception = Provenance(
        source=ProvenanceSource.RUNTIME_PERCEPTION,
        reference="fake sensor observation for interface smoke test",
    )

    def observe(node):
        nonlocal revision
        revision += 1
        return Observation(
            revision=f"fake-{revision}",
            payload=dict(state),
            provenance=perception,
        )

    def goal_satisfied(node, observation):
        return bool(observation.payload[node.goal])

    def pick(node, observation):
        state["tube_attached"] = True
        # Models the M1 diagnostic finding: a high grasp lets gravity make the
        # tube upright, so the following reorient node should be skipped.
        state["tube_upright"] = True
        return ControllerResult(ok=True)

    def reorient(node, observation):
        state["tube_upright"] = True
        return ControllerResult(ok=True)

    def align(node, observation):
        state["tube_aligned"] = True
        return ControllerResult(ok=True)

    def insert_servo(node, observation):
        # A real implementation keeps this high-rate loop inside the trusted
        # runtime. The policy receives only the bounded terminal result.
        progress = 0
        for _ in range(5):
            progress += 1
        state["tube_inserted"] = progress == 5 and state["tube_aligned"]
        return ControllerResult(
            ok=state["tube_inserted"],
            reason="" if state["tube_inserted"] else "servo did not converge",
            recoverable=not state["tube_inserted"],
            constraint_id=None if state["tube_inserted"] else "insert_axis",
        )

    def verify(node, observation):
        state["task_verified"] = (
            state["tube_inserted"] and state["tube_upright"]
        )
        return ControllerResult(
            ok=state["task_verified"],
            reason="" if state["task_verified"] else "visual verification failed",
            constraint_id=None if state["task_verified"] else "inserted_and_upright",
        )

    policy = PythonNodePolicy(
        graph=graph,
        observe=observe,
        goal_satisfied=goal_satisfied,
        controllers={
            "trusted.pick": pick,
            "trusted.reorient": reorient,
            "trusted.align": align,
            "trusted.insert_servo": insert_servo,
            "trusted.verify": verify,
        },
    )
    return policy.run()


def main() -> None:
    result = run_example()
    print(
        json.dumps(
            {
                "succeeded": result.succeeded,
                "graph_digest": result.graph_digest,
                "nodes": [
                    {
                        "node_id": node.node_id,
                        "action": node.action,
                        "status": node.status.value,
                        "attempts": node.attempts,
                        "observed_revisions": node.observed_revisions,
                        "failure_constraint_id": node.failure_constraint_id,
                        "reason": node.reason,
                    }
                    for node in result.nodes
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
