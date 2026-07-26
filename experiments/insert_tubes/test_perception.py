"""感知解析单元测试。"""

from __future__ import annotations

from experiments.insert_tubes.perception import parse_place_response, parse_pick_response


def test_place_detects_point_cloud_insufficient_in_blob():
    parsed = parse_place_response(
        {"xquats": [[None]], "results": [{"message": "point cloud insufficient"}]},
        arm_id=0,
    )
    assert parsed.pose is None
    assert parsed.error == "point cloud insufficient"


def test_pick_requires_pose_and_angle():
    parsed = parse_pick_response(
        {"xquats": [[None]], "grasp_angles": [[None]]},
        arm_id=0,
    )
    assert parsed.pose is None
