from __future__ import annotations

import gzip
import json

from robot_subtask_seg.demonstration_bundle import (
    DEMONSTRATION_BUNDLE_SCHEMA,
    build_demonstration_bundle,
)


def test_build_demonstration_bundle_keeps_graph_sized_summaries(tmp_path) -> None:
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "trace_id": "trace_demo",
                "task_id": "task_demo",
                "task_class": "insert_demo",
                "instruction": "Insert the object.",
                "video": {"path": "/source/video.mp4", "duration_sec": 2.0},
                "segments": [
                    {
                        "index": 0,
                        "start_sec": 0.0,
                        "end_sec": 1.0,
                        "label": "move object",
                    },
                    {
                        "index": 1,
                        "start_sec": 1.0,
                        "end_sec": 2.0,
                        "label": "insert object",
                    },
                ],
                "model": "test",
                "provider": "test",
            }
        ),
        encoding="utf-8",
    )
    frames_path = tmp_path / "artifacts" / "dense_frames.json.gz"
    frames_path.parent.mkdir()
    with gzip.open(frames_path, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "robot_subtask_seg.dense_track_frames.v1",
                "timestamps_sec": [0.0, 0.5, 1.0, 1.5, 2.0],
                "objects": {
                    "object_000": [
                        {"timestamp_sec": 0.0, "center_xy": [0.0, 0.0]},
                        {"timestamp_sec": 0.5, "center_xy": [3.0, 4.0]},
                        {"timestamp_sec": 1.0, "center_xy": [6.0, 8.0]},
                        {"timestamp_sec": 1.5, "center_xy": None},
                        {"timestamp_sec": 2.0, "center_xy": [9.0, 12.0]},
                    ]
                },
            },
            handle,
        )
    evidence_path = tmp_path / "dense_video_evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema": "robot_subtask_seg.video_evidence.v1",
                "evidence_gaps": [{"capability": "metric_depth", "reason": "RGB only"}],
                "provenance": {
                    "claims_metric_3d": False,
                    "claims_6d_pose": False,
                    "claims_dense_tracking": True,
                },
                "dense_tracking": {
                    "provider": "cotracker3_offline",
                    "coordinate_frame": "image_pixels",
                    "frame_count": 5,
                    "association": {"linked_observation_count": 2},
                    "objects": [
                        {
                            "object_id": "object_000",
                            "prompt": "object",
                            "reliable_frame_count": 4,
                            "reliable_frame_fraction": 0.8,
                        }
                    ],
                    "artifacts": {
                        "dense_frames": "artifacts/dense_frames.json.gz",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    bundle = build_demonstration_bundle(
        trace_path=trace_path,
        dense_evidence_path=evidence_path,
    )

    assert bundle["schema"] == DEMONSTRATION_BUNDLE_SCHEMA
    assert len(bundle["trace"]["segments"]) == 2
    assert len(bundle["objects"]) == 1
    first = bundle["segment_evidence"][0]["object_observations"][0]
    assert first["visible_fraction"] == 1.0
    assert first["net_displacement_px"] == 10.0
    second = bundle["segment_evidence"][1]["object_observations"][0]
    assert second["visible_fraction"] == 0.6667
    assert second["evidence_ref"] == "dense_track:object_000:segment:1"
    assert "timestamps_sec" not in json.dumps(bundle)
    assert bundle["provenance"]["claims_dense_tracking"] is True
