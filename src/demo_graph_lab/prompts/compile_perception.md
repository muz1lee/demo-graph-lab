# Prompt: propose a structured PerceptionProgram

---

You decide which closed-set perception chain publishes which geometric graph hole. A
trusted deterministic validator checks this JSON and a fake runtime dry-runs it before
anything is published; you must not write code, query text, per-step parameters or
numbers.

Output exactly one JSON object with this shape:

```json
{
  "schema": "demo_graph_lab.perception_program.v1",
  "task": "<task>",
  "programs": [
    {
      "stage": 0,
      "chain": ["localize", "segment", "fit_opening"],
      "provides": [
        {"field": "center", "hole": "<hole_a>"},
        {"field": "axis", "hole": "<hole_b>"}
      ]
    }
  ]
}
```

Copy `schema` and `task` exactly from `## TARGET HOLES`.

Hard rules:

- Every `chain` is a linear list of operators from `## OPERATOR TABLE`. It must start
  with `localize`, each operator must consume exactly what the previous one produced,
  and the last operator must produce `GEOMETRY` fields.
- Each `provides` entry is exactly `{"field", "hole"}`. `field` must be a published
  field of the chain's last operator, and `hole` must be a hole declared for that same
  `stage` in `## TARGET HOLES`. The hole's `type` must equal the field's type.
- A hole that declares a `resolver` also fixes which chain may publish it; see
  `## RESOLVER BINDINGS`. Two operators can produce the same hole type and still
  measure different quantities, so a matching type is not sufficient.
- Hole identity is `(stage, hole)`. The same hole name may appear in several stages,
  but one hole in one stage may be published by exactly one program, and one program
  must not repeat the same `(field, hole)` pair.
- Every hole published by one program must share the same `anchor`: one program
  observes one anchor. The anchor is already declared on the hole and is not part of
  this document — never write it, and never write query text for `localize`. Use a
  separate program for each anchor.
- No numeric literals anywhere in the document. The `stage` index is the only allowed
  number; no thresholds, tolerances, point counts, distances or units.
- Only the keys shown above. Programs have no `name`, no comments and no extra fields.
- Cover every hole listed in `## TARGET HOLES`. If some hole cannot be published by a
  legal chain, leave it out; an uncovered hole falls back to the existing resolver
  path, while a wrong chain is a violation.

Worked example over a different task, using its real target holes:

```json
{
  "schema": "demo_graph_lab.perception_program.v1",
  "task": "insert_tubes",
  "programs": [
    {
      "stage": 0,
      "chain": ["localize", "segment", "crop_points", "fit_axis"],
      "provides": [
        {"field": "axis", "hole": "tube_mid_long_axis"}
      ]
    },
    {
      "stage": 1,
      "chain": ["localize", "segment", "fit_opening"],
      "provides": [
        {"field": "center", "hole": "rack_center_hole_center"},
        {"field": "axis", "hole": "rack_center_hole_axis"}
      ]
    },
    {
      "stage": 1,
      "chain": ["localize", "segment", "crop_points", "fit_axis"],
      "provides": [
        {"field": "axis", "hole": "tube_mid_long_axis"}
      ]
    }
  ]
}
```

Stage 1 needs two programs because it observes two anchors; one of them publishes two
fields of the same chain. The same hole is republished in every stage that needs it.

Return JSON only. Do not return Python or prose.
