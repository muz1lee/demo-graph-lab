"""Offline checks for the measured camera-to-base extrinsics and its transforms.

The rotation, translation and lift facts used here are the 2026-08-06 head
calibration, not invented numbers.
"""

from __future__ import annotations

import math

import pytest

from demo_graph_lab.perception.frames import (
    CAMERA_EXTRINSICS_SCHEMA,
    LIFT_TOLERANCE_M,
    direction_to_base,
    load_camera_extrinsics,
    lift_offset,
    point_to_base,
    validate_camera_extrinsics_record,
)


# 2026-08-06 标定结果:camera_head_optical -> robot_base,OpenCV 光学系。
REAL_ROTATION = [
    [0.058257, -0.725275, 0.685990],
    [-0.998302, -0.041969, 0.040406],
    [-0.000515, -0.687179, -0.726488],
]
REAL_TRANSLATION = [0.097078, 0.037055, 1.161351]
# 同一个 R 的四元数写法(xyzw),用来验证两种写法解出同一个旋转。
REAL_QUATERNION_XYZW = [-0.675779913, 0.637624920, -0.253586643, 0.269164976]


def extrinsics_record(**overrides) -> dict:
    record = {
        "schema": CAMERA_EXTRINSICS_SCHEMA,
        "frame_from": "camera_head_optical",
        "frame_to": "robot_base",
        "axis_convention": {"x": "right", "y": "down", "z": "forward"},
        "rotation": [list(row) for row in REAL_ROTATION],
        "translation": list(REAL_TRANSLATION),
        "translation_unit": "meter",
        "lift_dependency": {
            "link": "lifting_link",
            "joint_type": "prismatic",
            "axis_base": [0.0, 0.0, 1.0],
            "limits_m": [-0.35, 0.0],
            "q_lift_assumed": 0.0,
            "correction": "translate_base_origin",
        },
        "method": "table_plane_and_marker_solve",
        "provenance": {
            "calibrated_at": "2026-08-06T09:00:00+00:00",
            "operator": "wenqian",
            "source_refs": ["calibration/2026-08-06/head_extrinsics_solve.json"],
        },
        "validation": {
            "table_normal_angle_deg": 0.055,
            "table_height_residual_m": 0.00069,
            "evidence_refs": ["calibration/2026-08-06/table_plane_residuals.json"],
        },
    }
    record.update(overrides)
    return record


def _angle_to_base_vertical(vector) -> float:
    cosine = max(-1.0, min(1.0, vector[2]))
    return math.degrees(math.acos(cosine))


def test_real_record_validates_as_a_right_handed_transform() -> None:
    extrinsics = validate_camera_extrinsics_record(extrinsics_record())

    assert extrinsics.frame_from == "camera_head_optical"
    assert extrinsics.frame_to == "robot_base"
    assert extrinsics.translation == tuple(REAL_TRANSLATION)
    assert extrinsics.q_lift_assumed == 0.0
    assert extrinsics.lift_limits_m == (-0.35, 0.0)
    assert extrinsics.lift_correction == "translate_base_origin"
    # 记录里自报的验证残差必须落在 0.1° / 1mm 预算内,否则这份标定不该被消费。
    validation = extrinsics.record["validation"]
    assert validation["table_normal_angle_deg"] < 0.1
    assert abs(validation["table_height_residual_m"]) < 0.001


def test_quaternion_and_matrix_forms_describe_the_same_rotation() -> None:
    record = extrinsics_record(quaternion_xyzw=list(REAL_QUATERNION_XYZW))
    record.pop("rotation")

    from_quaternion = validate_camera_extrinsics_record(record)
    from_matrix = validate_camera_extrinsics_record(extrinsics_record())

    for quaternion_row, matrix_row in zip(
        from_quaternion.rotation, from_matrix.rotation
    ):
        for first, second in zip(quaternion_row, matrix_row):
            assert abs(first - second) < 1e-5


def test_record_refuses_both_rotation_forms_at_once() -> None:
    record = extrinsics_record(quaternion_xyzw=list(REAL_QUATERNION_XYZW))

    with pytest.raises(ValueError, match="exactly one of"):
        validate_camera_extrinsics_record(record)


def test_record_refuses_a_mirrored_rotation() -> None:
    mirrored = [list(row) for row in REAL_ROTATION]
    mirrored[2] = [-item for item in mirrored[2]]

    with pytest.raises(ValueError, match="determinant"):
        validate_camera_extrinsics_record(extrinsics_record(rotation=mirrored))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"schema": "demo_graph_lab.camera_extrinsics.v2"}, "schema"),
        ({"frame_to": "world"}, "must map"),
        (
            {"axis_convention": {"x": "right", "y": "up", "z": "forward"}},
            "OpenCV optical",
        ),
        ({"translation_unit": "millimeter"}, "meter"),
        ({"translation": [0.1, 0.2]}, "3 numbers"),
    ],
)
def test_record_refuses_broken_physical_claims(overrides, message) -> None:
    with pytest.raises((ValueError, TypeError), match=message):
        validate_camera_extrinsics_record(extrinsics_record(**overrides))


def test_record_refuses_unknown_and_missing_fields() -> None:
    with pytest.raises(ValueError, match="extra="):
        validate_camera_extrinsics_record(extrinsics_record(scale=1.0))
    incomplete = extrinsics_record()
    incomplete.pop("validation")
    with pytest.raises(ValueError, match="missing="):
        validate_camera_extrinsics_record(incomplete)
    lift = dict(extrinsics_record()["lift_dependency"])
    lift["joint_type"] = "revolute"
    with pytest.raises(ValueError, match="prismatic"):
        validate_camera_extrinsics_record(extrinsics_record(lift_dependency=lift))


def test_table_normal_lands_on_base_vertical_within_a_tenth_of_a_degree() -> None:
    """Three table points measured in the optical frame must fit base vertical.

    The points are the calibration's own table plane sampled at 0.1 mm depth
    quantization, so this checks the transform and the ``SO(3)`` quality of the
    recorded ``R``.  The field residual against the real point cloud (0.055°)
    is a measurement and lives in the record's ``validation`` block.
    """

    extrinsics = validate_camera_extrinsics_record(extrinsics_record())
    table_points = (
        (0.1834, -0.0392, 0.6032),
        (0.0212, -0.1407, 0.6992),
        (-0.0979, -0.0292, 0.5939),
    )
    first = [b - a for a, b in zip(table_points[0], table_points[1])]
    second = [b - a for a, b in zip(table_points[0], table_points[2])]
    normal_cam = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )

    result = direction_to_base(normal_cam, extrinsics)

    assert result.status == "PASS"
    assert math.isclose(
        math.sqrt(sum(item * item for item in result.value)), 1.0, abs_tol=1e-9
    )
    oriented = result.value if result.value[2] > 0 else tuple(
        -item for item in result.value
    )
    assert _angle_to_base_vertical(oriented) < 0.1


def test_lift_correction_reproduces_the_measured_table_height_residual() -> None:
    """The 6.42 mm lift offset is what turns a 7.1 mm error into 0.69 mm."""

    extrinsics = validate_camera_extrinsics_record(extrinsics_record())
    table_height_m = 0.750
    # 该点由标定 R/t 从 base 系 [0.60, 0.00, 0.7571] 反投影得到:未修正时它比桌面
    # 高 7.1mm,正是当天观察到的残差。
    table_point_cam = (0.066498997, -0.085408794, 0.637185719)

    uncorrected = point_to_base(table_point_cam, extrinsics, 0.0)
    corrected = point_to_base(table_point_cam, extrinsics, -0.00642)

    assert uncorrected.status == corrected.status == "PASS"
    assert abs(uncorrected.value[2] - table_height_m - 0.0071) < 1e-4
    assert abs(corrected.value[2] - table_height_m - 0.00069) < 1e-4
    # 修正只沿 base z 移动,水平分量必须逐字不变。
    assert corrected.value[0] == uncorrected.value[0]
    assert corrected.value[1] == uncorrected.value[1]
    assert abs((uncorrected.value[2] - corrected.value[2]) - 0.00642) < 1e-9


def test_direction_never_takes_translation_or_lift() -> None:
    extrinsics = validate_camera_extrinsics_record(extrinsics_record())
    axis_cam = (0.0, 0.0, 1.0)

    direction = direction_to_base(axis_cam, extrinsics)
    point_at_zero = point_to_base(axis_cam, extrinsics, 0.0)
    point_at_lift = point_to_base(axis_cam, extrinsics, -0.2)

    assert direction.reason == "rotated_without_translation"
    # 同一个相机系向量当点看会被 t 与升降位移搬走,当方向看则完全不受影响。
    assert point_at_zero.value != point_at_lift.value
    assert direction.value == direction_to_base(axis_cam, extrinsics).value
    assert any(
        abs(direction.value[index] - point_at_zero.value[index]) > 0.1
        for index in range(3)
    )
    # 非单位输入必须重新归一化,而不是把长度带进 axis hole。
    scaled = direction_to_base((0.0, 0.0, 7.0), extrinsics)
    assert scaled.status == "PASS"
    assert all(
        abs(first - second) < 1e-9
        for first, second in zip(scaled.value, direction.value)
    )


def test_missing_q_lift_refuses_instead_of_assuming_the_calibration_pose() -> None:
    extrinsics = validate_camera_extrinsics_record(extrinsics_record())

    refused = point_to_base((0.0, 0.0, 0.5), extrinsics, None)

    assert refused.status == "UNKNOWN"
    assert refused.value is None
    assert refused.reason == "q_lift_unavailable"
    assert point_to_base((0.0, 0.0, 0.5), extrinsics, float("nan")).reason == (
        "q_lift_unavailable"
    )


def test_out_of_limit_lift_reading_is_refused() -> None:
    extrinsics = validate_camera_extrinsics_record(extrinsics_record())

    assert point_to_base((0.0, 0.0, 0.5), extrinsics, -0.5).reason == (
        "q_lift_out_of_limits"
    )
    assert point_to_base((0.0, 0.0, 0.5), extrinsics, 0.4).reason == (
        "q_lift_out_of_limits"
    )
    assert point_to_base((0.0, 0.0, 0.5), extrinsics, -0.35).status == "PASS"


def test_static_record_refuses_only_when_the_lift_actually_moved() -> None:
    """A record without a lift model may still be used at its calibration pose."""

    lift = dict(extrinsics_record()["lift_dependency"])
    lift["correction"] = "none"
    extrinsics = validate_camera_extrinsics_record(
        extrinsics_record(lift_dependency=lift)
    )

    inside = lift_offset(extrinsics, -LIFT_TOLERANCE_M / 2)
    outside = lift_offset(extrinsics, -0.00642)

    assert inside.status == "PASS"
    assert inside.value == (0.0, 0.0, 0.0)
    assert outside.status == "UNKNOWN"
    assert outside.reason == "q_lift_correction_unavailable"


def test_malformed_vectors_are_refused_without_raising() -> None:
    extrinsics = validate_camera_extrinsics_record(extrinsics_record())

    assert point_to_base((0.0, 0.0), extrinsics, 0.0).reason == "invalid_camera_point"
    assert point_to_base("abc", extrinsics, 0.0).reason == "invalid_camera_point"
    assert point_to_base(
        (0.0, 0.0, float("inf")), extrinsics, 0.0
    ).reason == "invalid_camera_point"
    assert direction_to_base((0.0, 0.0, 0.0), extrinsics).reason == (
        "degenerate_camera_direction"
    )
    assert direction_to_base(
        {"x": 1}, extrinsics
    ).reason == "invalid_camera_direction"


def test_loader_records_where_the_calibration_came_from(tmp_path) -> None:
    import json

    path = tmp_path / "head_extrinsics.json"
    path.write_text(json.dumps(extrinsics_record()), encoding="utf-8")

    extrinsics = load_camera_extrinsics(path)

    assert extrinsics.ref == str(path.resolve())
    assert extrinsics.rotation[0][0] == REAL_ROTATION[0][0]
