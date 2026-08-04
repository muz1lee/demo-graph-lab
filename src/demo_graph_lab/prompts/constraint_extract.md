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
   Hole `name` MUST be plain snake_case with underscores and no dots. Prefix an object
   id with `_`, for example `tube_left_grasp_pose`, never `tube_left.grasp_pose`.

   Every GEOMETRIC hole (`pose_se3`, `axis_3d`, `point_3d`) must also contain:
   `"resolver": <closed resolver>` and
   `"anchor": {"object_id": <registry id>, "part": "whole" | "hole",
                "instance": <optional non-empty string>,
                "selection": <optional non-empty string>}`.
   `anchor.part` is a CLOSED two-value enum, NOT a part name: write exactly `"whole"`
   (the object as a whole) or `"hole"` (a cavity/opening on it). Never invent values
   such as `"whole_object"`, `"top"`, `"body"`, or `"upper_body"`; a grasp region is
   expressed by the `region_grasp` constraint, never by `anchor.part`.
   Resolver/type compatibility is closed:
   - `grasp_candidate` -> `pose_se3`
   - `principal_axis` -> `axis_3d`
   - `part_center` -> `point_3d`
   - `part_axis` -> `axis_3d`
   - `motion_derived` -> `pose_se3`, `point_3d`, or `axis_3d`
   `anchor.object_id` must be one of the non-null `stage_objects` ids. Use `instance`
   for a known part instance (for example center/right/left rack hole), and `selection`
   for a runtime part choice (for example an empty hole). A `grasp_candidate` must anchor
   the whole manipulated object; express upper-body/top/middle grasp preference with the
   `region_grasp` constraint, not by asking segmentation to crop the object. Principal-axis
   holes for that object should reuse the same whole-object anchor. Center and axis holes
   for the same physical part MUST use the same anchor fields.

   All geometric holes publish in `"frame": "robot_base"`. A camera/source frame and
   its transform lineage belong in perception artifacts, not in the graph. Scalar and
   runtime-condition holes do not use `resolver` or `anchor`, but `frame` is REQUIRED
   on EVERY hole with no exception, including `scalar` and `runtime_condition`: use
   `"frame": "robot_base"` for a scalar length and `"frame": "runtime"` for a
   runtime_condition. A hole that omits `frame` is rejected outright.

   LEGAL HOLE SHAPES — copy these shapes exactly; each one validates as written:
   ```json
   {"name": "tube_left_grasp_pose", "type": "pose_se3", "frame": "robot_base",
    "solver_hint": "antipodal grasp on the manipulated tube",
    "resolver": "grasp_candidate",
    "anchor": {"object_id": "tube_left", "part": "whole"}}

   {"name": "tube_left_long_axis", "type": "axis_3d", "frame": "robot_base",
    "solver_hint": "principal axis of the manipulated tube",
    "resolver": "principal_axis",
    "anchor": {"object_id": "tube_left", "part": "whole"}}

   {"name": "rack_target_slot_center", "type": "point_3d", "frame": "robot_base",
    "solver_hint": "center of the chosen rack opening",
    "resolver": "part_center",
    "anchor": {"object_id": "rack", "part": "hole", "selection": "empty_slot"}}

   {"name": "rack_target_slot_axis", "type": "axis_3d", "frame": "robot_base",
    "solver_hint": "insertion axis of the chosen rack opening",
    "resolver": "part_axis",
    "anchor": {"object_id": "rack", "part": "hole", "selection": "empty_slot"}}

   {"name": "tube_left_release_pose", "type": "pose_se3", "frame": "robot_base",
    "solver_hint": "pose reached at the end of the commanded motion",
    "resolver": "motion_derived",
    "anchor": {"object_id": "tube_left", "part": "whole"}}

   {"name": "tube_left_lift_height", "type": "scalar", "frame": "robot_base",
    "solver_hint": "vertical clearance above the rack rim before transport"}

   {"name": "insert_lower_stop", "type": "runtime_condition", "frame": "runtime",
    "solver_hint": "contact or motion plateau while lowering"}
   ```

   BEFORE YOU OUTPUT, re-read every entry of your `holes` list and confirm:
   - all four of `name`, `type`, `solver_hint`, `frame` are present;
   - `anchor.part` is exactly `"whole"` or `"hole"`;
   - `grasp_candidate` and `principal_axis`: `part` is `"whole"` AND neither
     `instance` nor `selection` is present;
   - `part_center` and `part_axis`: `part` is `"hole"` AND exactly one of `instance`
     or `selection` is present;
   - the resolver matches the type per the closed table above;
   - no key outside {name, type, solver_hint, frame, purpose, resolver, anchor}.

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
 "holds": "throughout", "confidence": 0.85, "evidence_frames": [3, 5]}

Output JSON schema:
{"stage": str,
 "stage_objects": {"manipulated": "<registry id or null>", "target": "<registry id or null>"},
 "constraints": [...], "acceptance": [...], "holes": [...],
 "notes": str (optional, <=50 words)}
