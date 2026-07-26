# KSM: Demo2Graph2Code

KSM studies how a coding agent can learn the structure of a manipulation task
from a demonstration, compile that structure into executable code, and recover
locally when a constraint fails.

The project deliberately separates relational task knowledge from metric
execution:

```text
demonstration video
  -> temporal/keyframe evidence
  -> constraint graph with typed holes
  -> generated Python node policy
  -> runtime perception fills holes
  -> reactive execution and trusted servo controllers
```

The simulator is an execution and evaluation backend, not an answer database.
Generated policies cannot access scene libraries, exact simulator state, task
predicates, or evaluator targets. See [SECURITY.md](SECURITY.md).

## Repository layout

- `components/`: byte-preserved WHT harness and perception components.
- `method/demo_graph/`: constraint graph, code-policy, candidate, and reactive
  execution contracts developed in this repository.
- `adapters/`: sanitized demonstration, grasp proposal, observability, and
  Knowin World integration boundaries.
- `experiments/insert_tubes/`: non-privileged M1 contract and experiment
  matrix.
- `AGENTS.md`, `ALGORITHM_PLAN.md`, `PROGRESS.md`: stable project contract,
  method design, and current status respectively.

Knowin World, task data, model repositories, checkpoints, and experiment runs
are external runtime dependencies and are never vendored here.

## Development checks

Run the imported component tests from each component directory, then run the
new method and adapter tests:

```bash
python -m pytest -q method/demo_graph/tests
python -m pytest -q adapters/tests tests/integration
python scripts/public_release_check.py
```

The initial repository intentionally has no open-source license while
authorship and redistribution permissions are being confirmed.

