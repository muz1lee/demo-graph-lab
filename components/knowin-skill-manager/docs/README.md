# knowin-skill-manager Documentation Index

This directory is intentionally small. Generated run artifacts and older migration logs live under `docs/legacy/` so normal reading/search starts from current architecture documents.

**Operating boundary:** work only under 1022 `/mnt/data/wenqian/demo-graph-lab` (repo name `demo-graph-lab`). The Python package may still be named `ksm`; that is not a license to deploy or run against 1024 `/mnt/nas/knowin_sim/sim_workspace/`. NAS base trees are read-only borrow (data/venv) only.

## Current Documents

- `agent_controller_migration.md`
  - Current migration plan from fixed ASPIRE flow to an outer agent controller.
  - Covers Phase 0/1/2 contracts, node surface, and feedback safety boundary.

## Current Code Entry Points

- `ksm.agent_state`
  - `ExperimentState`, `CandidateObservation`, `AgentDecision`.
  - Keeps controller evidence separate from agent prompt context.

- `ksm.agent_nodes`
  - Node catalog for the outer controller.
  - Phase 1/2 only implements read-only planning nodes.

- `ksm.aspire`
  - Current unified ASPIRE suite/population loop.
  - Use via `python3 -m ksm.cli aspire-suite`.

- `ksm.suite_runner`
  - Shared suite execution/evaluation infrastructure.

- `ksm.feedback_attribution`
  - Converts reports into agent-observable feedback and strips evaluator-only fields.

- `ksm.robodojo_auto`
  - RoboDojo task selection and full-task experiment path.

## Legacy Documents

Older 20260710 ASPIRE migration notes and their generated run artifacts were archived under:

```text
docs/legacy/20260710_aspire_migration/
```

They are retained for provenance only. They mention old 1024 / 1021 paths and older command names; treat them as obsolete history, never as an operating guide.
