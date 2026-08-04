# Prompt: build one object registry for the whole video

---

You are building the OBJECT REGISTRY for a robot manipulation demonstration: the single
canonical list of physical object instances that all later per-stage annotations must
reference by id.

You get {N} frames sampled across the WHOLE video (with indices), the task instruction,
and the per-segment object mentions from an upstream trace (noisy names — unify them).

Output a strict JSON list; one entry per PHYSICAL object instance (not per appearance):
{"id": "<snake_case, stable, e.g. tube_left, bowl_green, coin_bank>",
 "category": "<object type>",
 "distinguishers": "<how to tell it apart from same-category peers: color/position at
   start/markings; <=12 words>",
 "trace_aliases": ["<upstream names that refer to this same instance>"],
 "first_seen_frame": <int>}

Rules:
- Start with an inventory of the earliest frame that shows the workspace. Count and
  enumerate EVERY visible instance of each repeated category before following motion.
  If three tubes are visible there, the registry must contain three tube entries whose
  `first_seen_frame` is that frame; a later destination is not a first appearance.
- Same physical object across the whole video = ONE entry, even if it moves or is
  restacked. Identity persistence over time is the whole point.
- Track each initial instance forward across every stage. Never reuse one id for a
  different remaining instance after the original object is placed or occluded.
- Include manipulable objects, receptacles/fixtures (rack, pad, bank, table), and any
  distractor that a detector could confuse (e.g. a transparent stand near a coin).
- For identical-looking instances (three same tubes), disambiguate by START position
  (tube_left/tube_mid/tube_right) and say so in distinguishers. Keep those ids even
  after the objects move to center/right/left destination slots.
- Do not invent objects not visible in frames. Output the JSON list only.
