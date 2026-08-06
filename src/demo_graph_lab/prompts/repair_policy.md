# Prompt: repair your own StageProgram from one failed episode

---

You proposed the StageProgram shown below. A trusted compiler turned it into Python, a
trusted runner executed it, and one episode failed. You now see a deterministic summary of
that episode. Propose one revision of your own program.

You are repairing the program, not the judgment. The constraint graph, its stage
constraints and its acceptance conditions are the demonstration's testimony, and the gate
verdict is the trusted evaluator's conclusion. They are evidence, not code you may edit,
soften, or route around.

Output exactly one JSON object with this shape:

```json
{
  "attribution": "one sentence naming the most likely cause of this failure",
  "program": {
    "stages": [
      {
        "index": 0,
        "name": "pick",
        "actions": [
          {"op": "approach", "args": {"target": {"object": "tube_left"}, "cone": "top_down"}}
        ]
      }
    ]
  }
}
```

`attribution` is one sentence of plain text; it is kept for review and never becomes part
of any artifact. `program` is a complete StageProgram in exactly the shape the compiler
already accepted: every graph stage exactly once and in graph order, each with `index`,
`name` and a non-empty `actions` list, copied names unchanged.

What you may change:

- the action sequence inside a stage: add, drop or reorder actions, as long as the order
  stays a non-decreasing subsequence of the primitive order below;
- which declared hole or which stage object is wired into which primitive argument.

What you may not change:

- the graph: its stages, names, holes, stage objects, constraints or acceptance
  conditions. They are not in your output and must not be described there either;
- each stage's `selection` object: it is fixed by the experiment arm. Copy it exactly,
  including `grasp_hole`, `current_constraints`, and `downstream_constraints`;
- the closed set: only the primitives and arguments in the table below exist. Do not
  invent primitives, parameters, holes, objects, helper fields or explanations;
- numbers: no coordinates, distances, angles, thresholds or any other numeric literal.
  Stage `index` is the only allowed number. Every value comes from a typed hole solved at
  runtime.

Hard rules, unchanged from the original compile:

- `solve` is not an action. The compiler inserts one `rt.solve` per used hole and reuses
  its opaque handle.
- `lower_until` only accepts a `runtime_condition` hole whose `purpose` is exactly
  `lower_stop`; never wire a scalar depth or a release/grasp condition.
- `retreat` only appears after `release`, and only when the graph declares a compatible
  retract/retreat pose hole.
- Omit unused optional arguments. Never use `null` as an argument.
- Do not call gates or claim success; the trusted runner decides acceptance.

If the summary does not support any change expressible in this closed set, return your
current program unchanged and say so in `attribution`. That is recorded as "no repair
proposed" and nothing is published — an unrepresentable repair is a real result, not a
reason to step outside the set.

Return JSON only. Do not return Python, and no prose outside the `attribution` string.
