# KW YAML Contract

Use this evidence when a generated candidate fails static policy checks, YAML parsing, or runtime output binding.

KW YAML candidates are workflow programs, not Python programs. A candidate should stay inside the public KW runtime surface:

- Use `action`, `args`, `output`, `assert`, `if`, `while`, and `del`.
- Keep action arguments as primitives, primitive lists, or expressions resolving to those shapes.
- Do not pass nested maps into subskill action arguments.
- Do not invent Python imports, shell calls, simulator private state, or seed-specific branches.
- Quote strings that contain `:` or begin with `=`.
- Prefer output dictionaries returned by an action over top-level variable names.

Repair pattern:

1. If an assertion references an undefined name, bind the action result with `output` first.
2. If a subskill already owns structured defaults such as gripper configuration, omit that arg.
3. If a value is task-level input, route it through `args` and `= args.<name>`.
4. Keep candidate YAML continuous and inspectable before introducing a planner.
