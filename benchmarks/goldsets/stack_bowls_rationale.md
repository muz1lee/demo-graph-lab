# stack_bowls 标注理由

标注日期：2026-07-30。
Instruction: "Stack the three bowls together." Final pile (bottom-up): bowl_left -> bowl_mid_right -> bowl_top_right, confirmed at t0015_50.

Rules applied from `RUBRIC.md`:
- Grasp-side approach cone on a rotationally symmetric bowl = incidental (symmetry-free sample); rim grasp = core.
- Place-side top_down along the stack axis = core (grasp-side vs place-side asymmetry).
- Stacking core = center_align(upper, lower) + above during lowering + carried bowl level.

## Registry / coreference quality note

`objects.json` correctly separates the three bowls:

- `bowl_left` carries aliases "top-left green bowl" (s0/s1 labels) AND "center-left green bowl" (s3 target label). Frames confirm this is one object: the bowl picked at t0001_50 from the left position is placed at table center (t0005_00) and is the same bowl that receives bowl_mid_right at t0008_35. Cross-stage coreference resolves correctly.
- `bowl_mid_right` carries alias "green bowl stack" (s6 target). Resolving s6's target to bowl_mid_right is functionally right: it is the topmost bowl of the 2-stack and the actual contact/support target of the final nest (t0014_88).
- Minor registry defect: bowl_mid_right's distinguisher says "becomes stack base" — factually it becomes the *middle* of the pile; bowl_left is the base. Alias resolution is unaffected, but the distinguisher text would mislead a downstream planner about stack topology.
- All graph stage_objects ids (`bowl_left`, `bowl_mid_right`, `bowl_top_right`, `table`) resolve to registry entries; no orphan names. Registry quality: good, one wrong distinguisher string.

## Stage 0 — pick bowl_left (t0001_50, t0002_38, t0003_25)

- Rim grasp clearly visible (t0002_38/81): **correct**, core.
- `approach_direction top_down`: real in frames but grasp-side on a symmetric bowl → **incidental**. An angled side pinch of the rim is an equally valid class member.
- `above` + derived `clearance` vs table: t0003_25 shows the bowl unambiguously airborne with daylight underneath → both **correct**. Notable: this *derived* clearance is better frame-supported than the demo_video clearances of s2/s5 (see below).
- Derived `order` chain `s0<s1<s2<s3<s5<s6`: **correct** — bottom-up stacking is a hard dependency (s1 must precede s3, s3 must precede s6); cleanup s4 rightly excluded.
- Acceptance (rim held, above, clearance at_end): all supported at frame 81 → **correct**.

## Stage 1 — place bowl_left on table (t0003_69, t0004_56, t0005_00)

- `above(bowl_left, table)` holds=throughout: supported across t0003_69→t0005_00 → **correct**; scope plausible.
- `approach_direction top_down (target=table)`: **correct** — this is place-side; the bowl is lowered along gravity onto its rest plane (t0004_12→t0005_00).
- `axis_vertical(up_axis)` at_end: level seating at t0005_00 → **correct**; core because this bowl becomes the stack base.
- **Missing: `carry(gripper_holds bowl_left)`** — this stage is the transport+set-down of the bowl; grasp retention is as necessary here as in s3/s6 where carry was extracted. Asymmetric omission.

## Stage 2 — pick bowl_mid_right (t0006_00...t0007_00)

- Rim grasp (t0006_75, t0007_00): **correct**.
- `approach_direction top_down` (3/5 votes): shallow angled descent visible; grasp-side → **incidental**.
- `clearance vs table` (constraint AND acceptance): **unsure** — at the cited end frame 175 (t0013_00-analog t0007_00) the bowl still looks seated with the gripper freshly closed; no visible daylight, and the s3 transfer is a low, near-table carry (t0007_45/t0007_90). Lift-off by *end of this stage* is not confirmable; a mm-scale hover can't be distinguished at keyframe resolution, so flagged unsure rather than wrong. Scope issue recorded: the at_end scope is what makes it unverifiable.

## Stage 3 — stack bowl_mid_right onto bowl_left (t0007_45, t0007_90, t0008_35, t0008_80)

- Stacking core triple all present and supported: `center_align` (concentric nest, t0008_35/t0008_80) **correct**; `above` **correct** with an evidence caveat — frame 198 (t0007_90) actually shows the bowls *side by side* at similar height, so "above" only becomes true in the final lift-over-and-lower between frames 198 and 209; core for the insertion phase; `axis_vertical(stack_axis)` (derived) **correct** — level carry visible t0007_90, level nest t0008_80. Arg-naming drift noted: s1 uses `up_axis`, s3/s6 use `stack_axis` for the same physical notion.
- `region_grasp rim` maintained (t0007_45/t0007_90): **correct**. `carry` explicit here: **correct**.
- `inside` (0.36 constraint + 0.67 acceptance): nesting visible → **correct**.
- Derived `approach_direction top_down (target=bowl_left)`: **correct** — place-side along stack axis; indirectly evidenced (adjacent at t0007_90, nested with gripper directly overhead at t0008_35 implies the top-down insertion between keyframes).
- Acceptance (inside, center_align, axis_vertical at_end): all visible at t0008_80 → **correct**.

## Stage 4 — cleanup: release + retract (t0008_98, t0009_15, t0009_50)

Cleanup is judged by the same rules as the other stages.
- `inside` holds=throughout: nest stays intact through release and retraction (t0008_98→t0009_50) → **correct**; this is exactly the release semantics that matters (don't drag the bowl back out).
- `clearance(bowl_mid_right, table)`: true and visible (nested bowl bottom off the table, t0009_50). Largely entailed by `inside`, but its violation = bowl knocked onto the table during retract = the cleanup failure mode → kept **correct** with redundancy noted. Same for both acceptance items.
- Gripper-vs-stack retreat clearance is not expressible in the closed vocab (gripper is not a registry object); the `retract_pose` hole covers it, so no missing entry.

## Stage 5 — pick bowl_top_right (t0010_50, t0011_75, t0013_00)

- `approach_direction top_down`: the cleanest descent of the demo (t0010_50→t0011_75→t0013_00), yet still grasp-side on a symmetric bowl → **incidental**.
- Rim grasp: **correct** (t0013_00).
- `clearance vs table` (constraint + acceptance): **unsure**, same pattern as s2 — at frame 325 (t0013_00) the bowl appears still in table contact; the visible lift happens in s6.

## Stage 6 — stack bowl_top_right onto the stack (t0013_62, t0014_25, t0014_88, t0015_50)

- `above(bowl_top_right, bowl_mid_right)`: carried bowl over the stack before lowering (t0014_25) → **correct**.
- `approach_direction top_down (target=bowl_mid_right)`: place-side, final descent into the cavity (t0014_25→t0014_88) → **correct**.
- `center_align`: concentric 3-bowl nest (t0014_88) → **correct**.
- `region_grasp rim`: held through carry (t0013_62, t0014_25) → **correct**.
- `axis_vertical(stack_axis)`: **correct** with scope caveat — evidence only at frame 388 (t0015_50, seated level). A throughout reading would be *contradicted*: t0013_62/t0014_25 show clear tilt during the low carry. The claim as evidenced (level during lowering/at end) is the core part.
- Derived `carry` and derived `inside`: both frame-supported (hold through t0014_88; nest at t0015_50) → **correct**.
- Acceptance (inside, center_align, derived axis_vertical at_end): all visible at t0015_50 → **correct**.

## Cross-cutting observations

1. **Derived propagation performed well in this run**: all 7 derived items (s0 clearance, s0 order, s3 approach_direction, s3 axis_vertical, s6 carry, s6 inside, s6 acceptance axis_vertical) pass on merits — and s0's derived clearance is ironically better evidenced than the two demo_video clearances (s2, s5) that got real votes but sit on frames showing the bowl still on the table.
2. **The demo's transfer style is a low slide-carry**: picks end with the grasp secured but no visible lift; lift-over happens inside the following stack stage. This systematically undermines `clearance(bowl, table)` *at_end* claims on pick stages (both marked unsure) and is a scope-assignment problem for the extractor, not a perception hallucination.
3. **holds-scope audit**: s1 `above` throughout — plausible, supported; s4 `inside` throughout — supported; s6 `axis_vertical` (no holds) — only at_end supported, mid-carry tilt visible; s3 `above` — frame 198 predates the relation becoming true.
