# Skill Boundary And Promotion

Use this evidence when ASPIRE-KW must decide whether a generated YAML is a reusable skill candidate or only an executable wrapper.

Calling existing KW skills is allowed. The boundary question is whether the candidate adds a reusable task-family interface, effect contract, or behavior that is not already represented by the called skill.

Candidate roles:

1. `reuse_existing_skill`
   - The YAML directly calls an existing mature skill such as `pickplace/semantic_pickplace.yaml`.
   - It may be useful as a baseline or runtime repair candidate, but it is not a new skill candidate and should not be promoted.
   - ASPIRE should mostly tune binding/runtime args for this role, not claim a new skill has been created.

2. `skill_specialization`
   - The YAML may call existing KW skills internally, but it defines a new reusable task-family interface.
   - It must expose stable args, expected effects, observable success, failure modes, and an `added_behavior_contract`.
   - Example: `stack_object_on_object(source_label, support_label)` may internally call pick/place, but its effect is `stacked(source, support)`, not generic relocation.
   - This is the preferred target when mature KW skills solve substeps but the task family needs extra ordering, gating, alignment, release, or verification semantics.

3. `new_behavior_skill`
   - Existing KW skills cannot express the key behavior, so the candidate changes the mechanism using available public actions.
   - Examples: controlled top release, alignment before insertion, place-on-support stabilization.
   - This is stronger than specialization. It must not be only a relabeled call sequence over the same selected existing skills.

4. `blocked_by_gap`
   - Public KW tools lack the primitive needed for the key behavior.
   - The candidate should report the gap rather than fabricate a fake YAML success path.

Promotion rule:
A candidate can be considered a skill candidate only if it is `skill_specialization` or `new_behavior_skill`. Direct reuse can seed experiments, but it is not the target artifact.

When history shows repeated failure of direct reuse, the next candidate should either:
- revise the runtime binding with a trace-grounded reason, or
- move to a specialization/new behavior candidate with a concrete behavior contract.

A concrete mechanism change must be expressible with public KW actions and declared subskill args. Do not invent parameters such as planner_config, gripper_open_angle, or hidden strategy maps to simulate a new capability. If the necessary control surface is not declared by the reused skill, report the gap or choose another public action sequence.
