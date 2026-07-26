# robot-subtask-seg

Standalone reproduction project for robot video subtask segmentation.

The project turns ordinary MP4 robot demonstration videos into timestamped
subtask traces:

```text
video + instruction
-> timestamped contact sheets
-> VLM JSON segments
-> normalized trace.json
```

The implementation is intentionally small and independent from Refiner's full
pipeline stack. It follows the public Macrodata Labs report, "Segmenting Robot
Video into Actionable Subtasks", and reuses the same core design: timestamped
contact sheets plus completed-manipulation-event prompting.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Build a manifest from a filtered RoboDojo video directory:

```bash
robot-subtask-seg build-manifest \
  --input-dir /path/to/filtered_1024_task_videos \
  --output data/robodojo_1024_filtered.json
```

Run a fake-provider smoke test:

```bash
robot-subtask-seg segment \
  --manifest data/robodojo_1024_filtered.json \
  --output-dir outputs/fake \
  --provider fake \
  --limit 1
```

Run Gemini segmentation:

```bash
export GOOGLE_GENERATIVE_AI_API_KEY=...
robot-subtask-seg segment \
  --manifest data/robodojo_1024_filtered.json \
  --output-dir outputs/gemini \
  --provider google_gemini
```

Audit an existing run and write a machine-readable quality report:

```bash
robot-subtask-seg audit-run \
  --output-dir outputs/gemini35_eef_trace_v2
```

Archive a structurally failed run:

```bash
robot-subtask-seg audit-run \
  --output-dir outputs/old_run \
  --archive-failed
```

Materialize an execution-oriented action view from a video-subtask trace:

```bash
robot-subtask-seg materialize-actions \
  --output-dir outputs/gemini35_eef_trace_v2
```

The action materializer filters `role=cleanup` segments and automatically splits
common compound segments such as `pick up X and place/drop/stack/insert X`. The
generated boundaries are marked as `boundary_source=heuristic_split` so they are
usable for debugging without being confused with model-observed boundaries.

Refine compound segments with visual evidence from the original video:

```bash
export GOOGLE_GENERATIVE_AI_API_KEY=...
robot-subtask-seg refine-actions \
  --config configs/robodojo_1024_filtered.yaml \
  --output-dir outputs/gemini35_eef_trace_v2 \
  --refined-dir outputs/gemini35_eef_trace_v2_refined
```

`refine-actions` is the preferred path for execution-quality traces. For each
compound parent segment, it renders `previous.jpg`, `current.jpg`, and `next.jpg`
contact sheets from the original video, asks the vision model to split only
inside the parent time window, and writes `refinement_manifest.json` beside each
refined `trace.json`. The original trace is left untouched.

Extract object-level evidence from monocular video:

```bash
robot-subtask-seg extract-video-evidence \
  --video data/filtered_1024_task_videos/insert_tubes/insert-tubes.mp4 \
  --trace outputs/gemini35_eef_trace_v2_refined/insert_tubes/robodojo_insert_tubes__insert-tubes/trace.json \
  --output-dir outputs/video_evidence/insert_tubes \
  --task-id robodojo_insert_tubes__insert-tubes \
  --instruction "Insert the three tubes into the rack one by one." \
  --prompt "white tube" \
  --prompt "tube rack"
```

This path uses SAM3 text grounding on sampled frames and links detections into
2D image-space tracklets. Its `video_evidence.json` records masks, boxes,
provenance, service errors, and explicit evidence gaps. It does not claim metric
depth, robot-frame coordinates, dense tracking, or object 6D pose.

Enrich an existing evidence bundle with continuous CoTracker point tracks:

```bash
robot-subtask-seg enrich-dense-tracking \
  --evidence-dir outputs/video_evidence/insert_tubes_limited_v1 \
  --tracker-url http://127.0.0.1:8093 \
  --points-per-object 16 \
  --target-fps 10
```

This command does not rerun SAM3. It seeds point queries from the saved object
masks, tracks them jointly over the full video, associates later sampled
detections back to stable object IDs, and writes `dense_video_evidence.json`.
Full per-frame trajectories are compressed under `artifacts/dense_tracking`;
the top-level JSON keeps only summaries suitable for Graph initialization and
Agent context.

Export a compact Harness input without copying per-frame trajectories into the
Agent context:

```bash
robot-subtask-seg export-demonstration-bundle \
  --trace outputs/refined/insert_tubes/trace.json \
  --dense-evidence outputs/video_evidence/insert_tubes/dense_video_evidence.json \
  --output outputs/video_evidence/insert_tubes/demonstration_bundle.json
```

The bundle embeds the refined segments, stable object summaries, per-segment
observable motion, evidence gaps, and artifact references. It does not classify
object roles or convert image-space motion into robot control targets.

Extract a finer, reuse-aware operational structure without replacing the source
trace:

```bash
robot-subtask-seg refine-operation-structure \
  --trace outputs/refined/insert_tubes/trace.json \
  --video data/filtered_1024_task_videos/insert_tubes/insert-tubes.mp4 \
  --demonstration-bundle outputs/video_evidence/insert_tubes/demonstration_bundle.json \
  --output-dir outputs/operation_structure/insert_tubes/evidence_guided \
  --mode evidence_guided \
  --config configs/robodojo_1024_filtered.yaml
```

The output separates a reusable canonical operation from its episode-local
instances and gives each instance evidence-linked operational phases. Use
`--mode visual_only` without a demonstration bundle to compare against a
contact-sheet-only result. This remains an offline evidence artifact; it is not
a final Skill Graph or robot control program.

## Attribution

This reproduction is based on public descriptions and source from:

- Macrodata Labs, "Segmenting Robot Video into Actionable Subtasks"
- `macrodata-labs/refiner`, Apache-2.0

This repository is not a fork of Refiner and does not depend on Refiner.
