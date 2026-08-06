# Prompt: propose a structured StageProgram

---

You turn a demonstration-derived constraint graph into a small executable StageProgram.
You choose the high-level primitive sequence, wire graph holes or stage objects into the
primitive arguments, and explicitly compose the candidate-selection calls that make a
grasp respect the selected experiment arm. A trusted deterministic compiler validates
this JSON and emits the corresponding Python calls.

Output exactly one JSON object with this shape:

```json
{
  "stages": [
    {
      "index": 0,
      "name": "pick",
      "selection": {
        "grasp_hole": "tube_grasp_pose",
        "current_constraints": [
          "s0:c0:region_grasp",
          "s0:c1:approach_direction"
        ],
        "downstream_constraints": [
          "s1:c0:axis_parallel",
          "s1:c1:inside"
        ]
      },
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

For every stage listed in `## SELECTION CONTEXT`, add exactly one `selection` object and:

- copy its only `grasp_hole` from `grasp_holes`;
- copy `current_constraints` exactly and in order;
- copy `downstream_constraints` exactly and in order.

The selected `grasp_hole` must be wired to `grasp_at.grasp_pose` in that stage.

Do not add `selection` to stages absent from that context. The three experiment arms differ
only in those lists: `vanilla` has neither, `local` has current constraints only, and
`backchain` also carries later constraints about the same manipulated object. The compiler
emits explicit `begin_candidates / rank_by / require_future / choose` calls from this object.

The authoritative primitive closed set — every primitive, its arguments, which are optional
and what each one accepts — is the `## PRIMITIVE TABLE` section below. It is rendered from
the compiler's own tables, so only what appears there exists.

References are explicit:

- hole handle: `{"hole": "exact_declared_hole_name"}`;
- object name: `{"object": "exact_non_null_value_from_stage_objects"}`;
- cone is the only direct string argument.

Hard rules:

- Candidate selection methods are not actions. Declare them only through `selection`.
  The compiler uses `choose` for its grasp hole and inserts one `rt.solve` for every other
  used hole; all returned values remain opaque handles.
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
