from __future__ import annotations

import numpy as np
import pytest

from robot_subtask_seg.dense_tracking import (
    CoTrackerClient,
    DenseTrackingError,
    _resolve_existing_path,
    _sample_binary_mask,
    select_anchor_objects,
)


def test_select_anchor_objects_uses_first_grounded_frame_per_prompt() -> None:
    observations = [
        {
            "observation_id": "late",
            "prompt": "tube",
            "frame_index": 2,
            "timestamp_sec": 2.0,
            "center_xy": [100, 100],
        },
        {
            "observation_id": "right",
            "prompt": "tube",
            "frame_index": 0,
            "timestamp_sec": 0.0,
            "center_xy": [200, 100],
        },
        {
            "observation_id": "left",
            "prompt": "tube",
            "frame_index": 0,
            "timestamp_sec": 0.0,
            "center_xy": [50, 100],
        },
    ]

    anchors = select_anchor_objects(observations)

    assert [anchor["observation_id"] for anchor in anchors] == ["left", "right"]
    assert [anchor["dense_object_id"] for anchor in anchors] == [
        "tube_000",
        "tube_001",
    ]


def test_sample_binary_mask_spreads_points_deterministically() -> None:
    mask = np.zeros((20, 30), dtype=bool)
    mask[4:16, 8:24] = True

    points = _sample_binary_mask(mask, point_count=8)

    assert len(points) == 8
    assert len(set(points)) == 8
    assert all(mask[int(y), int(x)] for x, y in points)


def test_resolve_existing_path_walks_evidence_ancestors(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    video = project / "data" / "clip.mp4"
    evidence = project / "outputs" / "video_evidence" / "run"
    video.parent.mkdir(parents=True)
    evidence.mkdir(parents=True)
    video.write_bytes(b"video")
    monkeypatch.chdir(tmp_path)

    resolved = _resolve_existing_path(
        "data/clip.mp4",
        evidence_root=evidence,
    )

    assert resolved == video.resolve()


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "response detail"

    def json(self) -> dict[str, str]:
        return {"schema": "robot_video_perception.cotracker_points.v1"}


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls = 0

    def post(self, *_args, **_kwargs) -> _Response:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def test_cotracker_client_does_not_retry_bad_request() -> None:
    session = _Session([_Response(400)])
    client = CoTrackerClient(
        "http://tracker",
        max_retries=3,
        backoff_sec=0,
        session=session,
    )

    with pytest.raises(DenseTrackingError, match="HTTP 400"):
        client.track_points(
            video_path="/tmp/missing.mp4",
            queries=[],
            coordinate_width=640,
            coordinate_height=480,
            target_fps=10,
            inference_width=512,
            max_frames=100,
        )

    assert session.calls == 1


def test_cotracker_client_retries_transient_server_error() -> None:
    session = _Session([_Response(503), _Response(200)])
    client = CoTrackerClient(
        "http://tracker",
        max_retries=2,
        backoff_sec=0,
        session=session,
    )

    result = client.track_points(
        video_path="/tmp/video.mp4",
        queries=[],
        coordinate_width=640,
        coordinate_height=480,
        target_fps=10,
        inference_width=512,
        max_frames=100,
    )

    assert result["schema"] == "robot_video_perception.cotracker_points.v1"
    assert session.calls == 2
