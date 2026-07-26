# Multi-step Workflow

Use this evidence for tasks that cannot be solved by a single semantic pickplace call.

KW can express a continuous top-level workflow before a full multi-skill planner exists. A longer candidate should still remain simple:

- Start with optional `/ctrl/go_home` only when reset posture matters.
- Use one high-level semantic operation whenever possible.
- Add short verification or cleanup steps after the main action.
- Avoid many reasoning calls in a row; each service call is a reliability dependency.
- Keep all subskill calls inside the experimental test skill folder when authoring new executable YAML.

For object insertion, key insertion, or orientation-sensitive tasks, split the intended logic into a small sequence:

1. Acquire or move the object with the most stable public skill.
2. Adjust approach or release through an existing public skill if available.
3. Verify the final relation with task predicates or a lightweight assertion.

Do not encode a long planner as a hidden Python facade. The generated artifact must remain KW YAML.
