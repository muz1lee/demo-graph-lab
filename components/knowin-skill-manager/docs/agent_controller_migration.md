# Agent Controller Migration

This document tracks the staged migration from a fixed ASPIRE runner into an outer agent controller.

## Phase 0: Baseline Freeze

Baseline scope:

- Keep the existing KSM suite runner, ASPIRE suite loop, RoboDojo task selection, trace feedback, visual feedback sidecar, and evaluator report pipeline intact.
- Treat prior stack runs as historical evidence, not as editable intermediate products.
- Preserve the safety boundary:
  - evaluator reports and predicate reports are controller/evaluation artifacts;
  - skill-generation prompts may only receive agent-observable feedback.

Current baseline cases:

- Simple reuse tasks: expected to keep using existing KW skills when the task matches a mature skill family.
- Complex stack task: current evidence shows repeated reuse-style candidates can run the KW pipeline but do not yet establish a robust reusable stack behavior.

## Phase 1: Node Surface

The first controller layer exposes existing capabilities as named nodes:

- `inspect_registry`
- `summarize_experiment_history`
- `plan_next_action`
- `generate_candidate`
- `run_suite`
- `run_aspire_iteration`
- `request_feedback_enrichment`
- `stop_and_report`

Only `summarize_experiment_history` and `plan_next_action` are implemented as read-only offline nodes in this phase. Execution nodes remain existing KSM functionality and are not reimplemented here.

## Phase 2: State And Decision Contract

New controller data contracts:

- `ExperimentState`: objective, history roots, observations, and coarse controller summary.
- `CandidateObservation`: one candidate/task episode, split into controller and agent-prompt views.
- `AgentDecision`: route, next node, rationale, required inputs, safe agent context, and controller evidence.

Supported route labels:

- `reuse_existing_skill`
- `adjust_binding_or_parameters`
- `wrap_existing_skill`
- `compose_existing_skills`
- `new_skill_candidate`
- `primitive_gap`
- `need_more_observation`
- `stop_success`

Minimal strategy labels used by the current controller:

- `reuse`: use an existing mature skill seed and run/evaluate it.
- `iterate_reuse`: the reuse path failed early, so iterate binding/parameters/failed action path first.
- `new_skill`: repeated reuse-style candidates can reach runtime but do not satisfy the controller evaluation gate, so expand to a new reusable behavior candidate.
- `need_observation`: history is missing or failure attribution is not actionable enough.

The detailed route labels remain for compatibility, but these four strategy labels are the intended top-level branch vocabulary for the next implementation phase.

## Phase 3: New Skill Spec Node

The controller now has a read-only `propose_new_skill_spec` node.

Purpose:

- Convert a `new_skill` strategy into a reusable skill boundary proposal.
- Produce design intent only; it does not generate YAML and does not execute KW.
- Refuse to propose a concrete spec when agent-observable visual evidence is missing.

For stack-like histories with enough structured visual feedback, the node proposes a `stack_step(source_label, support_label, arm_id)` style interface. The proposal may reuse stable existing skills for acquisition, but it must not be only another `semantic_pickplace` wrapper.

For current real stack histories without structured visual feedback, the node returns `needs_observation` and requests `request_feedback_enrichment`. The controller does not interpret raw videos directly; visual/trace interpretation belongs to the ASPIRE/evaluator feedback layer, and the controller consumes the resulting agent-observable summary.

## Phase 4: Spec To ASPIRE Input

The ASPIRE prompt path can now consume `agent_controller.new_skill_spec` from task YAML.

Current behavior:

- `build_aspire_prompt` injects the sanitized new-skill spec into the ASPIRE prompt.
- `build_llm_generation_prompt` does the same for the lower-level LLM generator path.
- The target task payload is also sanitized before prompting, so raw evaluator-only fields nested under task context cannot leak.
- Candidate roles now use a three-way model:
  - `reuse_existing_skill`: direct reuse/baseline, not a new skill.
  - `skill_specialization`: may reuse existing KW skills internally, but adds a task-family interface, constraints, effects, observable success, and failure modes.
  - `new_behavior_skill`: introduces a new mechanism using public KW actions because existing skills do not express the key behavior.
- `validate_new_skill_spec_contract` rejects direct-reuse responses when a proposed specialization/new-behavior spec is active, but does not forbid reuse inside a specialization.
- A stack-like proposed spec can package a `stack_step` style candidate in mock tests.

Important boundary:

- This phase still does not execute KW or call the real LLM.
- The spec is a design brief for ASPIRE, not executable code.
- If the real history lacks structured visual feedback, the controller should request feedback enrichment before asking ASPIRE to generate YAML.

Safety rule:

The controller may use a coarse evaluation gate to decide whether to stop or switch route. It must not pass predicate names, predicate values, geometry thresholds, or ground-truth scoring details into the agent prompt context.
