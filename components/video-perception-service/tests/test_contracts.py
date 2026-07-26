from __future__ import annotations

import pytest
from pydantic import ValidationError

from video_perception_service.contracts import PointQuery, TrackPointsRequest


def test_track_request_accepts_explicit_coordinate_frame() -> None:
    request = TrackPointsRequest(
        video_path="/mnt/workspace/wht/demo.mp4",
        query_coordinate_width=640,
        query_coordinate_height=480,
        queries=[
            PointQuery(
                query_id="tube_0_point_0",
                object_id="tube_0",
                timestamp_sec=0.0,
                x=120.0,
                y=200.0,
            )
        ],
    )

    assert request.target_fps == 10.0
    assert request.backward_tracking is True


def test_track_request_rejects_duplicate_query_ids() -> None:
    query = PointQuery(
        query_id="same",
        object_id="tube_0",
        timestamp_sec=0.0,
        x=120.0,
        y=200.0,
    )

    with pytest.raises(ValidationError, match="query_id values must be unique"):
        TrackPointsRequest(
            video_path="/mnt/workspace/wht/demo.mp4",
            queries=[query, query],
        )
