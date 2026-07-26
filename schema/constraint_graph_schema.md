# Demonstration Constraint Graph v0.2

The graph is a small executable specification between demonstration evidence
and a Python node policy. It describes required relations and unresolved
runtime quantities; it does not contain simulator answers or motion
waypoints.

The executable example is
`method/demo_graph/examples/m1_graph.json`. Its loader is
`method.demo_graph.ConstraintGraph`.

## Minimum shape

```json
{
  "schema_version": "0.2",
  "graph_id": "m1_single_tube",
  "entry_node": "pick",
  "provenance": {
    "source": "task_instruction",
    "reference": "pick, align and insert one tube"
  },
  "nodes": [
    {
      "node_id": "pick",
      "action": "pick",
      "goal": "tube_attached",
      "controller_ref": "trusted.pick",
      "max_attempts": 2,
      "next_node": "align",
      "provenance": {
        "source": "demo_video",
        "reference": "grasp keyframe"
      },
      "holes": [
        {
          "hole_id": "grasp_pose",
          "value_type": "pose_se3",
          "solver": "runtime_grasp_proposals",
          "search_domain": {"region": "tube_middle"},
          "provenance": {
            "source": "runtime_perception",
            "reference": "current RGB-D"
          }
        }
      ],
      "constraints": [
        {
          "constraint_id": "grasp_region",
          "description": "grasp the perceived middle region",
          "hole_ids": ["grasp_pose"],
          "provenance": {
            "source": "demo_video",
            "reference": "relative contact location"
          }
        }
      ]
    }
  ]
}
```

Every node has one observable `goal`. The runner observes before execution,
skips a node whose goal is already satisfied, calls one trusted controller,
then observes again. `max_attempts` bounds local recovery.

Every metric quantity unavailable from the demonstration is a typed hole with
a runtime solver. A constraint may reference hole IDs so execution failures
can identify the violated constraint without rewriting the full policy.

## Provenance

Main-method artifacts allow:

```text
demo_video
task_instruction
runtime_perception
generic_prior
derived
```

`derived` must recursively name its parents. `privileged_oracle` can label
evaluator artifacts but is rejected anywhere in a main-method graph,
including below a renamed derived value.

Scene libraries, asset geometry, exact simulator state, evaluator predicates,
target bindings, and values derived from them are not valid graph inputs.

