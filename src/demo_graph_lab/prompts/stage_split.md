# Prompt: propose stage boundaries when no upstream trace exists

---

You are given {N} uniformly sampled frames (with indices) from a robot/human manipulation
demonstration, plus the task instruction.

Segment the demonstration into stages. You may ONLY use these stage names:
approach, grasp, lift, reorient, transport, pre_align, insert, place, release, retreat.
(Skip names that do not occur; a stage list of 4-7 entries is typical.)

For each stage output: {"stage": str, "start_frame": int, "end_frame": int,
"boundary_event": str  (what visible event marks the boundary, e.g. "gripper closes on tube",
"tube becomes vertical", "tube bottom touches rack"), "confidence": float}.

Rules:
- Boundaries must be justified by VISIBLE events, not assumed script order.
- If two adjacent frames are ambiguous, place the boundary at the earlier frame and lower
  confidence.
- Output strict JSON list only.
