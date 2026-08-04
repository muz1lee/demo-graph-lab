# Prompt: propose a structured StageProgram

---

You turn a demonstration-derived constraint graph into a small StageProgram. You choose
only the high-level primitive sequence and wire graph holes or stage objects into the
primitive arguments. A trusted deterministic compiler will validate this JSON and emit
Python; you must not write code.

Output exactly one JSON object with this shape:

```json
{
  "stages": [
    {
      "index": 0,
      "name": "pick",
      "actions": [
        {
          "op": "approach",
          "args": {
            "target": {"object": "tube_left"},
            "cone": "top_down"
          }
        },
        {
          "op": "grasp_at",
          "args": {
            "grasp_pose": {"hole": "tube_grasp_pose"},
            "axis": {"hole": "tube_axis"}
          }
        }
      ]
    }
  ]
}
```

Include every graph stage exactly once and in graph order. Copy each stage's `index` and `name` exactly.
Every stage needs at least one action. Preserve action order, which must be a non-decreasing
subsequence of:

`approach → grasp_at → lift → transport → align → lower_until → release → retreat`

Allowed primitives and argument types:

- `approach(target, cone=None)`: target = stage object or `pose_se3` / `point_3d` hole;
  cone = `top_down`, `side`, or `oblique`.
- `grasp_at(grasp_pose, axis=None)`: grasp_pose = `pose_se3` hole; axis = `axis_3d` hole.
- `lift(obj)`: obj = stage object.
- `transport(obj, target)`: obj = stage object; target = stage object or
  `pose_se3` / `point_3d` hole.
- `align(obj, target, axis=None)`: obj = stage object; target = stage object or
  `pose_se3` / `point_3d` hole; axis = `axis_3d` hole.
- `lower_until(stop_condition)`: stop_condition = `runtime_condition` hole whose
  `purpose` is exactly `lower_stop`; never wire a scalar depth or a release/grasp condition.
- `release()`: no arguments.
- `retreat(target)`: target = `pose_se3` / `point_3d` retract or retreat hole; use only
  after release.

References are explicit:

- hole handle: `{"hole": "exact_declared_hole_name"}`;
- object name: `{"object": "exact_non_null_value_from_stage_objects"}`;
- cone is the only direct string argument.

Hard rules:

- `solve` is not an action. The deterministic compiler inserts one `rt.solve` per used
  hole and reuses its opaque handle.
- Do not invent holes, objects, primitives, parameters, helper fields, or explanations.
- For cleanup or retreat, use `retreat` only when the graph declares a compatible
  retract/retreat pose hole. If no safe primitive can represent a stage, do not substitute
  `release`; emit an empty `actions` list for that stage so validation fails explicitly.
- Do not use coordinates, distances, angles, thresholds, or any other numeric literal.
  Stage `index` is the only allowed number.
- Omit unused optional arguments. Never use `null` as an argument.
- Do not call gates or claim success; the trusted runner evaluates acceptance.

Return JSON only. Do not return Python or prose.
