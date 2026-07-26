# Standalone GraspNet Proposal Tools

This directory is intentionally independent from the Codex Skill Harness
runtime. It is not imported by the harness tool registry and does not modify
agent prompts, task graphs, behavior IR, function-call adapters, skill
translation, KW, KSM, or knowin-world core code.

## Purpose

The tool normalizes outputs from GraspNet / AnyGrasp-style grasp detectors into
serializable proposal evidence. It does not choose a robot route, select the
final grasp for a task, transform coordinates, or infer ground truth.

The intended downstream use is:

```text
RGB-D / point cloud / mask / object hint
-> external grasp detector
-> top-K grasp proposal evidence
-> later architecture layer decides how to use it
```

## Commands

Build a request:

```bash
python -m independent_tools.graspnet.cli build-request \
  --image examples/frame_left.png \
  --depth examples/depth_left.png \
  --mask examples/object_mask.png \
  --object-hint "target object" \
  --frame-id "demo_keyframe_003" \
  --coordinate-frame "camera_left" \
  --output /tmp/grasp_request.json
```

Normalize a raw service response:

```bash
python -m independent_tools.graspnet.cli normalize \
  --raw-response independent_tools/graspnet/examples/sample_graspnet_response.json \
  --config independent_tools/graspnet/examples/config.json \
  --output /tmp/grasp_proposals.json
```

Call a configured HTTP service:

```bash
python -m independent_tools.graspnet.cli call \
  --request /tmp/grasp_request.json \
  --config independent_tools/graspnet/examples/config.json \
  --output-dir /tmp/grasp_call
```

Project one RGB-D/depth observation to a point cloud:

```bash
python -m independent_tools.graspnet.cli rgbd-to-pointcloud \
  --depth /tmp/depth.npy \
  --intrinsics /tmp/intrinsics.json \
  --mask /tmp/object_mask.npy \
  --coordinate-frame camera_left \
  --output /tmp/grasp_probe/point_cloud.npz \
  --manifest /tmp/grasp_probe/point_cloud_manifest.json
```

Run an end-to-end real-frame probe against a deployed service:

```bash
python -m independent_tools.graspnet.cli real-frame-probe \
  --depth /tmp/depth.npy \
  --intrinsics /tmp/intrinsics.json \
  --mask /tmp/object_mask.npy \
  --coordinate-frame camera_left \
  --service-url http://127.0.0.1:8092 \
  --max-grasps 10 \
  --output-dir /tmp/grasp_probe
```

## Output Contract

The normalized result has schema `kw_independent.graspnet.proposals.v1`.
Important fields:

- `source_policy`: always records that this is proposal evidence, not a route
  decision.
- `input_reference`: preserves the image/depth/mask/point-cloud reference.
- `raw_response_path`: points to the unmodified service response when available.
- `proposals`: candidate grasps with score, pose fields, width/depth, raw index,
  coordinate frame, and original raw candidate.
- `warnings`: missing pose fields, unknown coordinate frame, or other evidence
  limitations.

Coordinates are passed through as supplied by the detector. If the detector or
config does not specify a frame, the output records `coordinate_frame: unknown`.

The point-cloud adapter emits schema
`kw_independent.graspnet.pointcloud_manifest.v1`. It preserves source paths,
camera intrinsics, depth scale, mask usage, output point count, and the point
cloud path. It does not infer camera calibration, object identity, grasp
selection, or robot/world transforms.

## Integration Boundary

Architecture-side integration can import:

```python
from independent_tools.graspnet import build_request, normalize_grasp_response
```

No registration is performed here. A future integration layer should explicitly
decide when this proposal evidence is exposed to a planner, writer, graph node,
or execution-time plan tool.
