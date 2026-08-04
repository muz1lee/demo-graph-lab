# Prompt: extract constraints for one stage

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

OBJECT REFERENCES: an OBJECT REGISTRY is provided below. Every object reference inside
`args` MUST use a registry id (optionally with a suffix like `<id>.long_axis`,
`<id>.center`). Never invent new object names; never use trace aliases directly.

APPROACH CONE SEMANTICS: `approach_direction.cone` is judged by the CONTACT direction of
the end-effector relative to gravity at the moment of contact — top_down = descending onto
the object/target from above; side = horizontal contact — NOT by the wrist's overhead
posture. (A pusher contacting an object's side face is `side` even if the wrist hangs
from above.)

TEMPORAL SCOPE: every constraint and acceptance item must carry
`"holds": "throughout" | "at_end"` — throughout = must stay true across the whole stage
window; at_end = achieved by the end of the stage. Judge from the frames: do NOT claim
`throughout` for a state that only appears near the boundary.

ARG SIGNATURES (use EXACTLY these key names inside `args`; never put arguments at the
top level of a constraint, never as a bare list):
axis_parallel(axis_a, axis_b) · axis_vertical(axis) · center_align(obj_a, obj_b) ·
region_grasp(obj, region) · approach_direction(cone[, target]) · above(obj_a, obj_b) ·
inside(obj_a, obj_b) · order(stage_sequence) · carry(relation) · clearance(obj_a, obj_b)

Example constraint (format reference only):
{"name": "region_grasp", "args": {"obj": "tube0", "region": "upper_body"},
 "confidence": 0.85, "evidence_frames": [3, 5]}

Output JSON schema:
{"stage": str,
 "stage_objects": {"manipulated": "<registry id or null>", "target": "<registry id or null>"},
 "constraints": [...], "acceptance": [...], "holes": [...],
 "notes": str (optional, <=50 words)}
