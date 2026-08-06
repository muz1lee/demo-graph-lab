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
subsequence of the chain order given below.

The authoritative primitive closed set — every primitive, its arguments, which are optional
and what each one accepts — is the `## PRIMITIVE TABLE` section below. It is rendered from
the compiler's own tables, so only what appears there exists.

References are explicit:

- hole handle: `{"hole": "exact_declared_hole_name"}`;
- object name: `{"object": "exact_non_null_value_from_stage_objects"}`;
- cone is the only direct string argument.

Hard rules:

- `solve` is not an action. The deterministic compiler inserts one `rt.solve` per used
  hole and reuses its opaque handle.
- Do not invent holes, objects, primitives, parameters, helper fields, or explanations.
- `lower_until` only accepts a stop condition whose `purpose` is exactly `lower_stop`;
  never wire a scalar depth or a release/grasp condition.
- For cleanup or retreat, use `retreat` only after `release`, and only when the graph
  declares a compatible retract/retreat pose hole. If no safe primitive can represent a
  stage, do not substitute `release`; emit an empty `actions` list for that stage so
  validation fails explicitly.
- Do not use coordinates, distances, angles, thresholds, or any other numeric literal.
  Stage `index` is the only allowed number.
- Omit unused optional arguments. Never use `null` as an argument.
- Do not call gates or claim success; the trusted runner evaluates acceptance.

Return JSON only. Do not return Python or prose.
