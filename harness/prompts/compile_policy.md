# Prompt: compile constraint graph into policy code (Opus, one shot)

版本: v0 (2026-07-30)。文本输入(graph JSON + 契约源码),无图像。改动走 git。

---

You are compiling a demonstration-derived constraint graph into an executable Python
policy module. You will be given (1) the runtime API CONTRACT source code and (2) the
task's constraint GRAPH as JSON (stages × {constraints, acceptance, holes}).

Write ONE Python module containing:
- one handler per stage: `def stage_<index>(rt):` (e.g. stage_0, stage_1, ...)
- at the end: `STAGES = {0: stage_0, 1: stage_1, ...}` mapping stage index → handler.

Each handler must, using ONLY the rt.* API from the contract:
1. Solve the holes its stage declares (`rt.solve("<hole_name>")` — exact names from the
   graph) and pass the returned handles to control primitives.
2. Perform the stage's action with the declared control primitives, honoring the stage's
   constraints (e.g. approach cone label from approach_direction; align before insert;
   push for non-prehensile tasks; grasp region is already baked into the grasp-pose hole).
3. NOT verify its own success at the end — the trusted runner gates each stage with the
   graph's acceptance constraints. (Within-stage `rt.residual`/`rt.verify` for
   mid-action checks is allowed but optional.)

HARD RULES:
- NO numeric literals anywhere in the module (no floats, no ints — not even 0 or 1 in
  handler bodies; the only allowed integers are the stage indices inside the final
  STAGES dict and in handler names).
- NO imports, NO file/network access, NO defining helper classes; plain functions only.
- ONLY call methods that exist on the contract's Runtime class; only pass hole handles,
  object-name strings from the graph, and constraint dicts copied from the graph.
- Every hole the stage declares should be solved before use; do not invent hole names.
- If you call rt.residual/rt.verify inside a handler, pass only {"name": ..., "args": {...}}
  stripped of confidence/evidence/votes fields (those contain numbers → forbidden literals).
- Keep each handler under ~15 lines; comments only where a constraint's handling is
  non-obvious.

Output: the Python module inside one ```python code fence, nothing else.
