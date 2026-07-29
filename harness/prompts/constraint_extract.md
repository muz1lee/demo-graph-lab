# Prompt: per-stage constraint extraction (Opus, compile-time only)

版本: v0 (2026-07-29)。改动走 git。运行参数: temperature≤0.2, k=5 自一致性采样, JSON-only 输出。

---

You are extracting the **task-invariant structure** of a robot manipulation demonstration.
You will be given: the task instruction, the current STAGE name, and {N} keyframes from that stage
(with frame indices).

Extract THREE things for this stage, as strict JSON:

1. `constraints`: geometric relations that MUST hold or be achieved in this stage.
   - You may ONLY use these constraint names: axis_parallel, axis_vertical, center_align,
     region_grasp, approach_direction, above, inside, order, carry, clearance.
   - `region_grasp.region` must be one of: top, upper_body, middle, bottom, rim, handle.
   - `approach_direction.cone` must be one of: top_down, side, oblique.
   - Arguments are SYMBOLIC references (e.g. "tube0.long_axis", "hole.axis", "bowl_a.center").
2. `acceptance`: how to check this stage succeeded, expressed with the SAME vocabulary
   (these become runtime verifiers; they must be checkable from perception, not from
   "the code ran without error").
3. `holes`: every metric quantity execution will need, as typed holes:
   `{"name": ..., "type": pose_se3|axis_3d|point_3d|scalar|runtime_condition,
     "solver_hint": <perception source>, "frame": <coordinate frame>}`.

HARD RULES:
- NEVER output numeric values for positions, offsets, sizes, angles-as-targets, or coordinates.
  Every metric quantity is a hole. If you are tempted to write a number, it is a hole.
  (Exception: none. Discrete labels from the closed lists above are not numbers.)
- Every constraint must cite `evidence_frames` (frame indices that support it) and a
  `confidence` in [0,1]. If the demo does not visibly support a constraint, do not invent it.
- Prefer FEWER, load-bearing constraints over exhaustive lists.
- If the demo shows a trick (e.g. the object self-reorients under gravity after grasping
  above its center of mass), record it as constraints (e.g. region_grasp=upper_body +
  axis_vertical as acceptance), not as a trajectory.

Output JSON schema:
{"stage": str, "constraints": [...], "acceptance": [...], "holes": [...],
 "notes": str (optional, <=50 words)}
