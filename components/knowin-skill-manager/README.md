# knowin-skill-manager

KSM is the ASPIRE-KW workspace for generating, evaluating, and iterating Knowin World YAML skills without modifying the Knowin World runtime repository.

Current working host/workspace:

```text
1021
/mnt/workspace/wht/kw-aspire-robodojo/knowin-skill-manager
```

Runtime boundary:

- Read KW skill/tool information from `/mnt/workspace/wht/kw-aspire-robodojo/runtime/knowin-world`.
- Publish generated executable YAML only under `knowin_skills/knowin_skill_manager_tests/<experiment_name>/`.
- Do not modify mature KW source, built-in skills, scenes, or runtime services from this workspace.

## Current Architecture

The project now has two layers:

```text
outer agent controller
-> inspect history / registry / feedback
-> choose route and next node
-> call existing KSM nodes
   -> suite runner
   -> ASPIRE suite loop
   -> RoboDojo task runner
   -> visual/trace feedback collection
```

ASPIRE is no longer treated as the whole system brain. It is an internal optimizer node that the controller may call when the current route is appropriate.

## Main Commands

Run from the KSM workspace:

```bash
export PYTHONPATH=$PWD
```

Offline agent-controller planning from prior runs:

```bash
python3 -m ksm.cli agent-plan \
  --objective "<objective>" \
  --history-root <run-or-suite-root> \
  --out <plan.json>
```

Unified ASPIRE suite loop:

```bash
python3 -m ksm.cli aspire-suite \
  --config configs/local_1021.yaml \
  --suite <suite.yaml> \
  --candidate-prefix <candidate_id> \
  --population-size 2 \
  --generations 1
```

RoboDojo full-task experiment:

```bash
python3 -m ksm.cli robodojo-auto \
  --config configs/local_1021.yaml \
  --task-class <task_class> \
  --tier 4 \
  --candidate-prefix <candidate_id>
```

Task-level reuse/new/gap decision probe:

```bash
python3 -m ksm.cli robodojo-decision \
  --config configs/local_1021.yaml \
  --task-class <task_class>
```

Diagnostic staged experiment, retained as a task-segmentation entry point:

```bash
python3 -m ksm.cli robodojo-staged \
  --config configs/local_1021.yaml \
  --task-class <task_class>
```

Low-level utilities for fixtures and debugging:

```text
registry
generate
validate
publish
pipeline-status
run
smoke
package-candidate
leaderboard
aspire
```

`aspire` is the older single-candidate loop. Keep it for compatibility and tests; prefer `aspire-suite` for current multi-candidate work.

## Key Modules

- `ksm.agent_state`: controller state, observations, route decision, and prompt-safety checks.
- `ksm.agent_nodes`: node catalog and read-only planning nodes.
- `ksm.aspire`: unified ASPIRE suite/population loop.
- `ksm.suite_runner`: publish/execute/evaluate task-candidate suites.
- `ksm.feedback_attribution`: converts episode reports into agent-observable feedback and strips evaluator-only fields.
- `ksm.visual_feedback`: attaches video/frame model observations.
- `ksm.robodojo_auto`: RoboDojo task selection, candidate generation, execution, and artifact collection.
- `ksm.robodojo_decision`: offline task-level route classification.
- `ksm.staged_experiment`, `ksm.skill_candidates`, `ksm.staged_state`: retained diagnostic/staged task-segmentation path.

## Evidence Boundary

Controller/evaluator artifacts may contain task success, predicate reports, and geometry measurements. These are allowed for experiment management and final scoring.

Skill-generation prompts must only receive agent-observable feedback:

- execution trace;
- failed action path;
- runtime argument deltas;
- WebUI frame/video observations;
- VLM summaries;
- non-privileged logs.

Do not pass predicate names, predicate values, geometry thresholds, or ground-truth scoring details into agent prompt context.

## Documentation Map

- `docs/README.md`: current document index.
- `docs/agent_controller_migration.md`: staged migration into the outer agent-controller architecture.
- `docs/legacy/`: archived 20260710 ASPIRE migration notes and old run artifacts.

## Verification

Run the full KSM test suite:

```bash
python3 -m pytest -q
```

Recent baseline after adding the agent controller skeleton:

```text
70 passed
```
