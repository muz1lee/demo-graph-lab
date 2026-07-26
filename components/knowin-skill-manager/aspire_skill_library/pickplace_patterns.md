# Pickplace Patterns

Use this evidence for tasks that ask to move an object into, onto, or near another object.

When the task can be expressed as semantic pick and place, start from `pickplace/semantic_pickplace.yaml`. It is usually more stable than composing camera grounding, pose estimation, grasp, lift, move, and release manually.

Recommended top-level args:

- `arm_id`
- `pick_label`
- `place_label`

Recommended action:

```yaml
- action: pickplace/semantic_pickplace.yaml
  args:
    arm_id: = args.arm_id
    pick_label: = args.pick_label
    place_label: = args.place_label
```

Avoid:

- Calling private or cloud-only reasoning endpoints unless they are present in the public registry.
- Reimplementing local pick logic when the high-level pickplace skill matches the task.
- Passing custom `gripper` maps into the pickplace subskill.

If the target wording is ambiguous, normalize the label in task args rather than hard-coding scene internals.
