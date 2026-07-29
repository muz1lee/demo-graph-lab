# Prompt: stage segmentation proposal (Opus, only when no upstream trace exists)

版本: v0 (2026-07-29)。优先复用 robot-subtask-seg 的 refined trace;本 prompt 仅在无 trace 或
trace 过粗时使用,产出必须经人审(report.html)确认后方可下游消费。

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
