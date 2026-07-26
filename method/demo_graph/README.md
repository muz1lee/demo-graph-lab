# Minimal Demo Graph runner

This directory is the first executable vertical slice, not a general robot
framework. It contains:

- a small JSON `ConstraintGraph` loader with recursive provenance rejection;
- `PythonNodePolicy`, which observes before every node/retry, skips an already
  satisfied goal, calls one trusted controller, re-observes, and retries within
  the node budget;
- an M1 graph and fake runtime that exercise
  `pick → reorient/skip → align → insert → verify`.

Run the local smoke test from the repository root:

```bash
python3 -m method.demo_graph.examples.m1_fake
python3 -m unittest discover -s method/demo_graph/tests -v
```

For a live adapter, provide only:

```python
PythonNodePolicy(
    graph=graph,
    observe=knowin_adapter.observe,
    goal_satisfied=method_visible_goal_check,
    controllers=trusted_controller_registry,
)
```

The insertion controller owns its high-frequency loop. Generated policy code
receives only its bounded `ControllerResult`; evaluator and simulator state are
not part of this API.
