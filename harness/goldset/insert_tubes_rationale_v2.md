# insert_tubes gold v2 — per-stage rationale (claude-bringup-v2, 2026-07-30)

Run: `harness/runs/harness_insert_tubes_20260730_003434`. Independent annotation; v1 gold NOT consulted.
Frames cited as `t<sec>_<centisec>` (approx. video frame index in parentheses where relevant, ~25 fps).

## Object registry / coreference quality (read first)

- **Three tubes are visible from t0000_00**: two on the LEFT (upper-left ~x195, lower-left ~x140) and one on the right. `objects.json` registers only `tube_left` / `tube_right` as present at frame 0 and claims `tube_mid` has `first_seen_frame: 83` with distinguisher "inserted first, appears in rack center". That is a **missed instance**: the first-inserted tube was on the table from frame 0 (the upper-left one). The registry's distinguishers cannot separate the two left-side tubes at t=0 (identical white body + orange cap; only relative position distinguishes them, and "starts on left side" matches both).
- **Graph coreference is internally inconsistent with the registry**: stages 0–1 manipulate the upper-left tube and call it `tube_left`; stages 4–5 manipulate the *remaining lower-left* tube and also call it `tube_left`. So `tube_left` denotes two different physical tubes across the graph. Under the registry's own semantics the stage-0/1 tube should be `tube_mid`. Per v0.2 instructions this is recorded as a registry/coreference defect rather than flipping per-item verdicts (within each stage, "the manipulated tube" is unambiguous and the relations are judged on that).
- `gripper` and `table` appear as constraint args; `table` is registered, `gripper` is not (minor registry gap, noted on the stage-5 clearance items).
- Hole assignment observed: tube 1 → center hole, tube 2 → right hole, tube 3 → left hole; consistent with the registry's "tube_mid appears in rack center".

## Stage 0 — pick first tube (0.0–1.5 s)

Frames: t0000_00 (three tubes on table, rack empty), t0000_75 (left gripper closing on upper end of the upper-left lying tube), t0001_12 / t0001_50 (tube hangs from gripper, settled vertical, base off table).

- `region_grasp(upper_body)` **correct**: grip is on the cap-side upper portion; the subsequent hang-and-settle to vertical (t0001_12→t0001_50) is the gravity self-reorientation that only an above-CoM grasp gives. Core.
- `clearance(tube, table)` **correct**: lifted clear by t0001_12; this is the stage goal.
- `axis_vertical` **correct with scope note**: no `holds` claimed; true only post-grasp (tube lies tilted at t0000_00). Read as at_end, matching frames 28/38.
- `approach_direction(top_down, tube)` **incidental**: frames do show an over-the-top descent, but pick-side approach cone on a rotationally symmetric lying tube is a symmetry-free DoF (rubric boss principle: the side-vs-top dispute itself is the canonical incidental).
- derived `approach_direction(side, tube)` **incidental**: no evidence frames and demo shows the opposite cone, but it lands in the same equivalence class as its top_down twin — incidental, not wrong, by the same symmetry ruling.
- derived `order(s0<s1<s2<s3<s4<s5)` **correct**: matches the observed serialized pick→insert×3 structure and the instruction's "one by one". Core dependency.
- Acceptance (clearance / region_grasp / axis_vertical, all at_end) all **correct** on t0001_50.
- Missing: none.

## Stage 1 — insert first tube (1.5–4.0 s)

Frames: t0002_12 (carry begins), t0002_75 (tube vertical, cap up, poised above rack), t0003_38 (tube in bore, gripper at rack top), t0004_00 (seated; cap+neck above rack plane; arm releasing).

- `axis_vertical`, `axis_parallel(tube↔hole)`, `inside`, `center_align` all **correct**: classic peg-in-hole core set, each visible across t0002_75→t0004_00. center_align args are slightly loose (seated tube centroid is below hole plane; intent = coaxial) — noted, not penalized.
- derived `approach_direction(top_down, rack)` **correct**: vertical descent visible t0002_75→t0003_38. Insertion-side elevation class toward a vertical bore is core — the symmetry exemption covers azimuth only.
- Acceptance (inside / axis_vertical / center_align at_end) all **correct** on t0004_00.
- **Missing `region_grasp(tube, upper_body)`**: the upper-body grip (visible t0002_75–t0003_38) is what lets the tube seat while the gripper stays above the rack; stage 3 lists it, stage 1 forgot it.
- **Missing `carry(gripper_holds(tube))`**: grip continuity from stage start to seating is load-bearing; encoded only in stage 4.

## Stage 2 — pick second tube (4.5–5.8 s)

Frames: t0004_50 (tube 1 seated center; right gripper at the lying right tube), t0004_83 (grasped at cap-side end, hanging tilted mid-swing), t0005_15 / t0005_80 (hanging near-vertical, clear of table).

- `clearance(tube, table)` **correct** (t0005_15/t0005_80), `region_grasp(upper_body)` **correct** (t0004_50/t0004_83 grip near cap; swing-to-vertical confirms above-CoM), `axis_vertical` **correct with scope note** (post-grasp only; tilted at t0004_83 mid-swing; no holds claimed, acceptance at_end matches t0005_80).
- `approach_direction(side, tube)` (demo, 3/5) **incidental** and derived `approach_direction(top_down, tube)` **incidental**: the mirrored pair of the stage-0 dispute; symmetric-tube pick cone is symmetry-free either way. (Amusingly, the derived propagation here copied stage 0's top_down while the local voters saw side — both equally incidental.)
- Acceptance all **correct** at t0005_80.
- Missing: none — pick set is complete here.

## Stage 3 — insert second tube (5.8–8.0 s)

Frames: t0006_35 (~159, tube carried in from the right, lateral to rack), t0006_90 (~172, vertical, cap up, above the right hole), t0007_45 (~186, seated next to tube 1), t0008_00 (~200, both caps level, arm withdrawing).

- `inside`, `axis_vertical`, `axis_parallel`, `approach_direction(top_down, rack)` all **correct**: same core insertion set as stage 1, all frame-supported.
- `above(tube, rack)` **correct**: satisfied at t0006_90/t0007_45; frame 159 shows it not yet above, but no throughout scope is claimed, and above-the-hole is the necessary waypoint of top-down insertion.
- `region_grasp(upper_body)` **correct**: grip below cap visible t0006_35–t0006_90; enables seating with gripper above rack plane.
- derived `center_align` **correct**: clean entry at t0007_45 entails prior coaxial alignment; mirrors the demo-voted stage-1/5 items.
- Acceptance (inside / axis_vertical at_end) **correct** at t0008_00.
- **Missing `clearance(tube_right, tube_mid)`**: tube 1's cap protrudes from the adjacent center hole during the whole approach/descent; contact would eject it. Canonical placed-object clearance miss (rubric list). Registry id caveat: the first tube is `tube_mid` per objects.json, `tube_left` per the graph's own stage-0 usage.
- **Missing `carry(gripper_holds(tube_right))`**: as in stage 1.

## Stage 4 — transport third tube (8.5–10.5 s)

Frames: t0008_50 (~212, lower-left tube freshly grasped, hanging **tilted ~30–40°**), t0009_00 (~225, near-vertical, cap right under gripper), t0009_50 (~238, carried), t0010_00 (~250, **tilted again mid-swing**), t0010_50 (~262, arm high left of rack, tube occluded by forearm).

- `axis_vertical` holds=**throughout** → **wrong (scope)**: its own evidence frames contradict it — tilted at 212 (still reorienting after grasp) and at 250 (pendulum swing during motion). The constraint is real at_end (settled by t0011_00), and the acceptance item encodes exactly that; the throughout claim is what fails. This is the cleanest v0.2 scope-rule application in the task.
- `clearance(tube, table)` throughout **correct**: airborne in all five frames.
- `carry(gripper_holds)` throughout **correct**: held in all frames; core (drop = fail).
- `region_grasp(upper_body)` throughout **correct**: cap directly below gripper at t0009_00; a grasp cannot migrate, so throughout is safe.
- `clearance(tube, rack)` throughout **correct**: approach at t0010_00–t0010_50 passes clear of the rack now holding two tubes; trivially true earlier, so not contradicted.
- Acceptance: `axis_vertical` at_end **correct** (262 occluded, but t0011_00 half a second later shows it settled vertical); `carry` at_end **correct**; `above(tube, rack)` at_end **unsure** — at t0010_50 the tube is occluded by the forearm and the gripper appears still left of the rack footprint; "above" is only confirmable at t0011_00, i.e. inside stage 5. Occlusion → unsure per rubric, not wrong.
- Missing: none (above is already in acceptance; transport set is otherwise complete).

## Stage 5 — insert third tube + retract (10.5–12.5 s)

Frames: t0011_00 (~275, tube vertical, cap up, coaxial over the empty left hole), t0011_50 (~288, descending into bore beside two seated caps), t0012_00 (~300, three caps level, released), t0012_50 (~312, both arms home, rack intact).

- `inside`, `axis_parallel`, `center_align` **correct**: core insertion set, all visible t0011_00→t0012_00.
- `axis_vertical` holds=throughout **correct**: vertical at 275/288/300; 262 occluded but nothing contradicts — by this stage the swing has settled, so throughout is plausible (contrast with stage 4).
- `approach_direction(top_down, rack)` throughout **correct**: hover→vertical descent is the whole visible motion; core.
- `clearance(gripper, rack)` at_end **correct** (constraint and acceptance): t0012_50 shows arms at home with all tubes seated; snagging the rack on retract would undo the task. `gripper` is not a registry id — registry gap, noted.
- Acceptance (inside / axis_vertical / clearance at_end) all **correct** at t0012_00/t0012_50.
- **Missing `clearance(tube_left, tube_mid)`**: the left target hole is adjacent to the seated center tube; the descent at t0011_50 passes within a hole pitch of its cap.
- **Missing `carry(gripper_holds(tube_left))`**: must hold until seated (release only after t0012_00); noted as holds-until-release, not to stage end.

## Tallies

Per-stage (constraints + acceptance): s0 7C/2I, s1 8C (miss 2), s2 6C/2I, s3 9C (miss 2), s4 6C/1W/1U, s5 9C (miss 2).
Total: 45 correct, 4 incidental, 1 wrong, 1 unsure; 6 missing.
Derived subset (5 items): 3 correct (s0 order, s1 top_down insertion, s3 center_align), 2 incidental (s0 side pick cone, s2 top_down pick cone), 0 wrong.
