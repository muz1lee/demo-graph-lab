"""Pure-local contracts for mask assignment, object clouds, and openings."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from demo_graph_lab.perception.object_pipeline import (
    GeometryStatus,
    MASK_SCHEMA,
    OBJECT_POINT_CLOUD_SCHEMA,
    build_object_point_cloud,
    estimate_planar_opening_geometry,
    make_object_assignment_record,
    project_masked_depth,
    validate_mask_record,
    validate_object_assignment_record,
)


def _np():
    return pytest.importorskip("numpy")


def _intrinsics(*, width: int = 4, height: int = 3) -> dict:
    return {
        "width": width,
        "height": height,
        "fx": 1.0,
        "fy": 1.0,
        "cx": 1.0,
        "cy": 1.0,
        "baseline": 0.08,
    }


def _mask():
    np = _np()
    value = np.zeros((3, 4), dtype=np.bool_)
    value[0, 0] = True
    value[1, 1] = True
    value[2, 2] = True
    return value


def _mask_record(mask=None) -> dict:
    np = _np()
    mask = _mask() if mask is None else mask
    return {
        "schema": MASK_SCHEMA,
        "observation_id": "obs-1",
        "image_ref": "sensor/rgb.npy",
        "grounding_ref": "grounding/tube-0.json",
        "proposal_id": "proposal-0",
        "mask_ref": "masks/tube-0.npy",
        "shape": list(mask.shape),
        "encoding": "bool",
        "foreground_pixels": int(np.count_nonzero(mask)),
    }


def _assignment() -> dict:
    return make_object_assignment_record(
        observation_id="obs-1",
        object_id="tube_left",
        part="whole",
        instance=None,
        selection=None,
        grounding_ref="grounding/tube-0.json",
        mask_ref="masks/tube-0.npy",
        cloud_ref="pointcloud/tube-left.npz",
        cloud_manifest_ref="pointcloud/tube-left.manifest.json",
        frame="camera_head_optical",
        calibration_ref="calibration/head.json",
    )


def test_module_import_does_not_load_numpy() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    statement = (
        "import sys; "
        "import demo_graph_lab.perception.object_pipeline; "
        "assert 'numpy' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", statement],
        env={**os.environ, "PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_mask_record_is_exact_and_matches_boolean_payload() -> None:
    np = _np()
    mask = _mask()

    result = validate_mask_record(
        _mask_record(mask), mask, expected_observation_id="obs-1"
    )

    assert result["foreground_pixels"] == 3
    assert "object_id" not in result

    extra = _mask_record(mask)
    extra["pose"] = [0.0, 0.0, 1.0]
    with pytest.raises(ValueError, match="extra"):
        validate_mask_record(extra, mask)

    with pytest.raises(TypeError, match="boolean"):
        validate_mask_record(_mask_record(mask), mask.astype(np.uint8))

    wrong_count = _mask_record(mask)
    wrong_count["foreground_pixels"] += 1
    with pytest.raises(ValueError, match="foreground count"):
        validate_mask_record(wrong_count, mask)

    with pytest.raises(ValueError, match="another observation"):
        validate_mask_record(
            _mask_record(mask), mask, expected_observation_id="obs-stale"
        )


def test_mask_is_applied_before_projection_and_pixels_keep_row_column_lineage() -> None:
    np = _np()
    intrinsics = {
        **_intrinsics(width=3, height=2),
        "cy": 0.0,
    }
    depth = np.array([
        [1.0, np.nan, 2.0],
        [0.0, 1.0, 3.0],
    ], dtype=np.float32)
    mask = np.array([
        [True, True, False],
        [False, True, True],
    ], dtype=np.bool_)

    points, pixels = project_masked_depth(depth, mask, intrinsics)

    np.testing.assert_array_equal(pixels, np.array([
        [0, 0],
        [1, 1],
        [1, 2],
    ], dtype=np.int32))
    np.testing.assert_allclose(points, np.array([
        [-1.0, 0.0, 1.0],
        [0.0, 1.0, 1.0],
        [3.0, 3.0, 3.0],
    ], dtype=np.float32))
    assert points.dtype == np.float32
    assert not points.flags.writeable
    assert not pixels.flags.writeable


def test_assignment_binds_graph_anchor_and_mask_lineage() -> None:
    mask_record = _mask_record()
    expected = {
        "object_id": "tube_left",
        "part": "whole",
        "instance": None,
        "selection": None,
    }

    result = validate_object_assignment_record(
        _assignment(),
        expected_observation_id="obs-1",
        expected_graph_object=expected,
        mask_record=mask_record,
    )

    assert result["graph_object"] == expected
    assert result["identity_status"] == "MODEL_PROPOSED"
    assert result["unit"] == "meter"

    wrong_anchor = copy.deepcopy(_assignment())
    wrong_anchor["graph_object"]["object_id"] = "tube_right"
    with pytest.raises(ValueError, match="graph anchor"):
        validate_object_assignment_record(
            wrong_anchor,
            expected_graph_object=expected,
        )

    wrong_mask = copy.deepcopy(_assignment())
    wrong_mask["mask_ref"] = "masks/other.npy"
    with pytest.raises(ValueError, match="mask_ref"):
        validate_object_assignment_record(wrong_mask, mask_record=mask_record)

    with pytest.raises(ValueError, match="instance"):
        make_object_assignment_record(
            **{
                **{
                    "observation_id": "obs-1",
                    "object_id": "rack",
                    "part": "hole",
                    "instance": None,
                    "grounding_ref": "grounding/rack.json",
                    "mask_ref": "masks/hole.npy",
                    "cloud_ref": "pointcloud/hole.npz",
                    "cloud_manifest_ref": "pointcloud/hole.manifest.json",
                    "frame": "camera_head_optical",
                    "calibration_ref": "calibration/head.json",
                }
            }
        )


def test_object_cloud_manifest_traces_mask_depth_assignment_and_pixels() -> None:
    np = _np()
    mask = _mask()
    depth = np.ones(mask.shape, dtype=np.float32)
    depth[1, 1] = np.nan
    graph_object = {
        "object_id": "tube_left",
        "part": "whole",
        "instance": None,
        "selection": None,
    }

    result = build_object_point_cloud(
        depth,
        mask,
        _intrinsics(),
        mask_record=_mask_record(mask),
        assignment_record=_assignment(),
        assignment_ref="assignments/tube-left.json",
        depth_ref="sensor/depth.npy",
        pixel_lineage_ref="pointcloud/tube-left.pixels.npy",
        expected_graph_object=graph_object,
    )

    assert result.points.shape == (2, 3)
    np.testing.assert_array_equal(result.pixels_rc, [[0, 0], [2, 2]])
    manifest = result.manifest_record()
    assert manifest["schema"] == OBJECT_POINT_CLOUD_SCHEMA
    assert manifest["graph_object"] == graph_object
    assert manifest["identity_status"] == "MODEL_PROPOSED"
    assert manifest["point_count"] == 2
    assert manifest["masked_pixel_count"] == 3
    assert manifest["assignment_ref"] == "assignments/tube-left.json"
    assert manifest["pixel_layout"] == "row_col"
    assert manifest["frame"] == "camera_head_optical"
    assert manifest["unit"] == "meter"
    json.dumps(manifest)


def _hole_scene(*, depth_contrast: float = 0.05):
    np = _np()
    height = width = 11
    intrinsics = {
        "width": width,
        "height": height,
        "fx": 100.0,
        "fy": 100.0,
        "cx": 5.0,
        "cy": 5.0,
        "baseline": 0.08,
    }
    rgb = np.full((height, width, 3), 200, dtype=np.uint8)
    depth = np.ones((height, width), dtype=np.float32)
    roi = np.zeros((height, width), dtype=np.bool_)
    roi[4:7, 6:9] = True
    rgb[roi] = 10
    depth[roi] += np.float32(depth_contrast)
    return rgb, depth, roi, intrinsics


def _estimate(rgb, depth, roi, intrinsics, **kwargs):
    roi_record = _mask_record(roi)
    roi_record.update({
        "image_ref": "sensor/rgb.npy",
        "grounding_ref": "grounding/opening.json",
        "proposal_id": "opening-0",
        "mask_ref": "masks/opening.npy",
    })
    return estimate_planar_opening_geometry(
        rgb,
        depth,
        roi,
        intrinsics,
        observation_id="obs-1",
        frame="camera_head_optical",
        calibration_ref="calibration/head.json",
        rgb_ref="sensor/rgb.npy",
        depth_ref="sensor/depth.npy",
        roi_record=roi_record,
        **kwargs,
    )


def test_opening_geometry_is_recomputed_from_rgbd_roi_and_support_plane() -> None:
    np = _np()
    rgb, depth, roi, intrinsics = _hole_scene()

    result = _estimate(rgb, depth, roi, intrinsics)

    assert result.status is GeometryStatus.PASS
    np.testing.assert_allclose(result.center, [0.02, 0.0, 1.0], atol=1e-6)
    np.testing.assert_allclose(result.axis, [0.0, 0.0, 1.0], atol=1e-6)
    record = result.to_record()
    assert record["frame"] == "camera_head_optical"
    assert record["unit"] == "meter"
    assert record["metrics"]["depth_contrast_m"] > 0.04


def test_opening_geometry_accepts_a_protruding_opening_and_keeps_the_sign() -> None:
    np = _np()
    # 同一个开口,但 ROI 比周围支撑面更近(凸起而不是凹陷)。深度对比门只看
    # 幅度,所以它同样是 PASS;metrics 里的符号必须保留,上层要靠它判凹凸。
    rgb, depth, roi, intrinsics = _hole_scene(depth_contrast=-0.05)

    result = _estimate(rgb, depth, roi, intrinsics)

    assert result.status is GeometryStatus.PASS
    assert result.reason == "estimated_from_rgbd_roi_and_local_support_plane"
    np.testing.assert_allclose(result.center, [0.02, 0.0, 1.0], atol=1e-6)
    np.testing.assert_allclose(result.axis, [0.0, 0.0, 1.0], atol=1e-6)
    assert result.to_record()["metrics"]["depth_contrast_m"] < -0.04


def test_opening_geometry_fails_closed_without_depth_evidence() -> None:
    rgb, depth, roi, intrinsics = _hole_scene(depth_contrast=0.0)

    result = _estimate(rgb, depth, roi, intrinsics)

    assert result.status is GeometryStatus.UNKNOWN
    assert result.reason == "insufficient_depth_contrast"
    assert result.center is None
    assert result.axis is None
    assert result.to_record()["center"] is None


def test_opening_geometry_rejects_model_pose_and_malformed_rgb() -> None:
    np = _np()
    rgb, depth, roi, intrinsics = _hole_scene()

    with pytest.raises(TypeError, match="unexpected keyword"):
        _estimate(
            rgb,
            depth,
            roi,
            intrinsics,
            model_pose=[0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        )
    with pytest.raises(TypeError, match="uint8"):
        _estimate(rgb.astype(np.float32), depth, roi, intrinsics)
