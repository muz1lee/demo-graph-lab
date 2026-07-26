from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from method.demo_graph.keyframe_relations import (  # noqa: E402
    compare_annotations,
    extract_relation_from_arrays,
)


def test_extracts_upper_axial_cross_axis_relation_from_one_keyframe():
    frame = np.full((100, 100, 3), (140, 160, 180), dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[30:76, 47:54] = 255
    frame[30:76, 47:54] = (245, 245, 245)
    frame[15:39, 30:71] = (10, 10, 10)
    points = np.asarray(
        [
            [48, 34],
            [50, 34],
            [52, 34],
            [48, 44],
            [50, 44],
            [52, 44],
            [48, 56],
            [50, 56],
            [52, 56],
            [48, 68],
            [50, 68],
            [52, 68],
        ],
        dtype=float,
    )

    relation = extract_relation_from_arrays(
        relation_id="tube_1_grasp",
        event="grasp",
        segment_index=0,
        object_id="tube_track_1",
        timestamp_sec=1.0,
        frame_bgr=frame,
        object_points_xy=points,
        seed_points_xy=points,
        object_mask=mask,
        total_track_count=len(points),
    )

    assert relation["region"]["label"] == "upper_body"
    assert relation["approach_axis"]["relation"] == "axial"
    assert relation["closing_direction"]["relation"] == "cross_axis"
    assert relation["confidence"] > 0.7
    assert "relative_pose" not in relation


def test_human_annotation_comparison_requires_all_three_coarse_fields():
    relation = {
        "relation_id": "r",
        "region": {"label": "upper_body"},
        "approach_axis": {"relation": "axial"},
        "closing_direction": {"relation": "cross_axis"},
    }
    report = compare_annotations(
        [relation],
        [
            {
                "relation_id": "r",
                "region_label": "upper_body",
                "approach_relation": "axial",
                "closing_relation": "cross_axis",
            }
        ],
    )
    assert report["all_match"] is True
    assert report["passed"] == 1
