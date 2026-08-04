"""Offline tests for the explicit object-grounding record chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from demo_graph_lab.execution.object_record import (
    ground_record,
    predict_record,
    project_record,
    segment_record,
)
from demo_graph_lab.execution.planning_record import plan_record


_ANCHOR = {
    "object_id": "tube_left",
    "part": "whole",
    "instance": None,
    "selection": None,
}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _record(
    tmp_path: Path,
    *,
    resolver: str = "grasp_candidate",
    anchor: dict | None = None,
    hole_name: str = "tube_grasp_pose",
) -> tuple[Path, np.ndarray]:
    anchor = dict(_ANCHOR if anchor is None else anchor)
    root = (tmp_path / "record").resolve()
    height, width = 8, 10
    graph_path = (tmp_path / "graph.json").resolve()
    objects_path = (tmp_path / "objects.json").resolve()
    intrinsics_path = (tmp_path / "intrinsics.json").resolve()
    hole_type = {
        "grasp_candidate": "pose_se3",
        "principal_axis": "axis_3d",
        "part_center": "point_3d",
        "part_axis": "axis_3d",
    }[resolver]
    _write_json(graph_path, {
        "task": "insert_tubes",
        "stages": [{
            "index": 0,
            "name": "test_stage",
            "stage_objects": {"manipulated": "tube_left", "target": "rack"},
            "holes": [{
                "name": hole_name,
                "type": hole_type,
                "frame": "robot_base",
                "solver_hint": "test perception hole",
                "resolver": resolver,
                "anchor": {
                    key: value for key, value in anchor.items() if value is not None
                },
            }],
        }],
    })
    _write_json(objects_path, [
        {
            "id": "tube_left",
            "category": "test tube",
            "distinguishers": "the left tube",
        },
        {
            "id": "rack",
            "category": "three-hole rack",
            "distinguishers": "the rack on the right",
        },
    ])
    _write_json(intrinsics_path, {
        "head": {
            "resolution": [width, height],
            "fx": 100.0,
            "fy": 100.0,
            "cx": 4.5,
            "cy": 3.5,
            "baseline": 0.05,
        }
    })
    plan_record(
        graph_path=graph_path,
        objects_path=objects_path,
        stage_index=0,
        record_dir=root,
        intrinsics_path=intrinsics_path,
        hole_name=hole_name,
        pipeline_url="http://127.0.0.1:8000",
        graspnet_url="http://127.0.0.1:8092",
        qwen_url="https://qwen.example/v1/chat/completions",
        qwen_model="qwen-vl",
        sam3_url="https://sam3.example/segment",
        camera_socket="/tmp/fake-camera.sock",
        timeout_s=2.0,
        max_grasps=4,
        min_object_points=3,
    )

    sensor = root / "sensor"
    calibration_dir = root / "calibration"
    sensor.mkdir()
    calibration_dir.mkdir()

    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 0] = np.arange(width, dtype=np.uint8)
    image[:, :, 1] = np.arange(height, dtype=np.uint8)[:, None]
    depth = np.ones((height, width), dtype=np.float32)
    mask = np.zeros((height, width), dtype=np.bool_)
    mask[1:7, 4:6] = True

    image_path = (sensor / "head_left_bgr.npy").resolve()
    depth_path = (sensor / "head_depth_m.npy").resolve()
    calibration_path = (calibration_dir / "bundle.json").resolve()
    proprio_path = (root / "proprioception.json").resolve()
    np.save(image_path, image, allow_pickle=False)
    np.save(depth_path, depth, allow_pickle=False)
    _write_json(proprio_path, {"frozen": True})
    _write_json(calibration_path, {
        "schema": "demo_graph_lab.head_intrinsics.v1",
        "camera": "head",
        "frame": "camera_head_optical",
        "intrinsics": {
            "width": width,
            "height": height,
            "fx": 100.0,
            "fy": 100.0,
            "cx": 4.5,
            "cy": 3.5,
            "baseline": 0.05,
        },
    })
    _write_json(root / "observation.json", {
        "observation_id": "head-17-12500000",
        "captured_at_s": 12.5,
        "frame": "camera_head_optical",
        "calibration_ref": str(calibration_path),
        "sensor_refs": [
            str(image_path),
            str(depth_path),
            str(calibration_path),
            str(proprio_path),
        ],
        "robot_state": {
            "joint_positions": [0.0] * 14,
            "gripper_positions": [],
            "end_effector_frame": "robot_base",
            "end_effector_poses": {},
            "evidence_ref": str(proprio_path),
        },
        "objects": [],
    })
    manifest = json.loads((root / "manifest.json").read_text())
    manifest.update({
        "status": "OBSERVATION_RECORDED",
        "observation_id": "head-17-12500000",
        "updated_at": "2026-08-04T00:00:00+00:00",
        "last_error": None,
    })
    manifest["artifacts"].update({
        "observation": "observation.json",
        "head_left_bgr": "sensor/head_left_bgr.npy",
        "head_depth_m": "sensor/head_depth_m.npy",
        "calibration": "calibration/bundle.json",
    })
    _write_json(root / "manifest.json", manifest)
    return root, mask


class FakeSources:
    references = []
    mask = None
    raw_object_id = -1
    calls = []

    @classmethod
    def reset(cls, mask) -> None:
        cls.references = [{
            "rank": 1,
            "bbox_1000": [300.0, 100.0, 700.0, 900.0],
            "bbox_pixel": [3, 0, 7, 8],
        }]
        cls.mask = mask
        cls.raw_object_id = -1
        cls.calls = []

    class QwenGroundingClient:
        def __init__(self, endpoint, *, token, model, timeout_s):
            FakeSources.calls.append(
                ("qwen_init", endpoint, token, model, timeout_s)
            )

        def ground(
            self,
            jpeg_bytes,
            *,
            prompt,
            image_width,
            image_height,
            top_k,
        ):
            assert jpeg_bytes.startswith(b"\xff\xd8")
            assert jpeg_bytes.endswith(b"\xff\xd9")
            FakeSources.calls.append(
                ("ground", prompt, image_width, image_height, top_k)
            )
            return {
                "references": [dict(item) for item in FakeSources.references],
                "raw_response": {
                    "id": "qwen-request-1",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                },
            }

    class Sam3SegmentationClient:
        def __init__(self, endpoint, *, token, timeout_s):
            FakeSources.calls.append(("sam3_init", endpoint, token, timeout_s))

        def segment(
            self,
            jpeg_bytes,
            *,
            bbox_pixel,
            image_width,
            image_height,
        ):
            assert jpeg_bytes.startswith(b"\xff\xd8")
            FakeSources.calls.append(
                ("segment", bbox_pixel, image_width, image_height)
            )
            pixels = np.asarray(FakeSources.mask)
            if pixels.dtype == np.dtype(np.bool_):
                pixels = pixels.astype(np.uint8) * 255
            ok, encoded = cv2.imencode(".png", pixels)
            assert ok
            return {
                "mask_bytes": encoded.tobytes(),
                "mask": {
                    "encoding": "png",
                    "semantic": "binary_mask",
                    "width": image_width,
                    "height": image_height,
                },
                "detection_metadata": {"score": 0.91},
                "raw_response": {"success": True, "request_id": "sam3-1"},
            }

    class GraspNetReadClient:
        def __init__(self, base_url, *, timeout_s):
            FakeSources.calls.append(("graspnet_init", base_url, timeout_s))

        def health(self):
            FakeSources.calls.append(("graspnet_health",))
            return {"ok": True, "backend": "graspnet_baseline"}

        def predict(self, request):
            FakeSources.calls.append(("graspnet_predict", request))
            object_id = FakeSources.raw_object_id
            rotation = [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
            translation = [0.0, 0.0, 1.0]
            raw = [
                0.9,
                0.04,
                0.02,
                0.03,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                *translation,
                object_id,
            ]
            return {
                "ok": True,
                "schema": "kw_independent.graspnet.raw_response.v1",
                "backend": "graspnet_baseline",
                "coordinate_frame": request["coordinate_frame"],
                "grasps": [{
                    "raw_index": 0,
                    "score": 0.9,
                    "width": 0.04,
                    "height": 0.02,
                    "depth": 0.03,
                    "rotation_matrix": rotation,
                    "translation": translation,
                    "object_id": object_id,
                    "coordinate_frame": request["coordinate_frame"],
                    "raw_grasp_array": raw,
                }],
                "input_reference": dict(request),
            }


def _through_project(tmp_path: Path, **record_kwargs) -> tuple[Path, np.ndarray]:
    root, mask = _record(tmp_path, **record_kwargs)
    FakeSources.reset(mask)
    ground_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )
    segment_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
    )
    project_record(root)
    return root, mask


def test_permissions_fail_before_loading_any_transport(tmp_path) -> None:
    class Poison:
        def __getattr__(self, name):
            raise AssertionError(f"transport accessed: {name}")

    for function, keyword in (
        (ground_record, "allow-model-read"),
        (segment_record, "allow-model-read"),
        (predict_record, "allow-live-read"),
    ):
        with pytest.raises(PermissionError, match=keyword):
            function(tmp_path / "missing", source_module=Poison())


@pytest.mark.parametrize("count", [0, 2])
def test_grounding_fails_closed_and_preserves_evidence(tmp_path, count) -> None:
    root, mask = _record(tmp_path)
    FakeSources.reset(mask)
    FakeSources.references = [
        {
            "rank": index + 1,
            "bbox_1000": [100.0, 100.0, 500.0, 500.0],
            "bbox_pixel": [1, 1, 5, 4],
        }
        for index in range(count)
    ]

    with pytest.raises(ValueError, match="exactly one reference"):
        ground_record(
            root,
            allow_model_read=True,
            source_module=FakeSources,
            qwen_token="test-secret",
        )

    manifest = json.loads((root / "manifest.json").read_text())
    result = json.loads((root / "grounding/result.json").read_text())
    request_text = (root / "grounding/request.json").read_text()
    assert manifest["status"] == "OBSERVATION_RECORDED"
    assert manifest["last_error"]["step"] == "ground"
    assert result["status"] == "REJECTED"
    assert result["reference_count"] == count
    assert result["selected_reference"] is None
    assert (root / "grounding/input.jpg").read_bytes().startswith(b"\xff\xd8")
    assert (root / "grounding/raw.json").is_file()
    assert json.loads((root / "grounding/call.json").read_text())["status"] == "error"
    assert "test-secret" not in request_text


def test_grounding_rejects_bbox_mapping_before_success_status(tmp_path) -> None:
    root, mask = _record(tmp_path)
    FakeSources.reset(mask)
    FakeSources.references[0]["bbox_pixel"] = [2, 0, 7, 8]

    with pytest.raises(ValueError, match="does not match normalized"):
        ground_record(
            root,
            allow_model_read=True,
            source_module=FakeSources,
            qwen_token="test-secret",
        )

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "OBSERVATION_RECORDED"
    assert manifest["last_error"]["step"] == "ground"
    assert (root / "grounding/raw.json").is_file()
    assert not (root / "grounding/result.json").exists()


def test_grounding_revalidates_plan_against_graph_before_model_call(tmp_path) -> None:
    root, mask = _record(tmp_path)
    plan = json.loads((root / "plan.json").read_text())
    plan["perception_request"]["anchor"]["object_id"] = "rack"
    _write_json(root / "plan.json", plan)
    FakeSources.reset(mask)

    with pytest.raises(ValueError, match="no longer matches"):
        ground_record(
            root,
            allow_model_read=True,
            source_module=FakeSources,
            qwen_token="test-secret",
        )

    assert FakeSources.calls == []


def test_project_revalidates_grounding_before_assignment(tmp_path) -> None:
    root, mask = _record(tmp_path)
    FakeSources.reset(mask)
    ground_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )
    segment_record(root, allow_model_read=True, source_module=FakeSources)
    grounding = json.loads((root / "grounding/result.json").read_text())
    grounding["anchor"]["object_id"] = "rack"
    _write_json(root / "grounding/result.json", grounding)
    calls_before = list(FakeSources.calls)

    with pytest.raises(ValueError, match="grounding anchor does not match plan"):
        project_record(root)

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "MASK_RECORDED"
    assert manifest["last_error"]["step"] == "project"
    assert FakeSources.calls == calls_before
    assert not (root / "object/assignment.json").exists()
    assert not (root / "object/pointcloud.npz").exists()


def test_project_rejects_replaced_grounding_jpeg(tmp_path) -> None:
    root, mask = _record(tmp_path)
    FakeSources.reset(mask)
    ground_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )
    segment_record(root, allow_model_read=True, source_module=FakeSources)
    replacement = np.full((*mask.shape, 3), 255, dtype=np.uint8)
    ok, encoded = cv2.imencode(
        ".jpg", replacement, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    assert ok
    (root / "grounding/input.jpg").write_bytes(encoded.tobytes())

    with pytest.raises(ValueError, match="JPEG no longer matches"):
        project_record(root)

    assert not (root / "object/assignment.json").exists()


def test_segmentation_rejects_non_binary_png_and_keeps_raw_artifacts(tmp_path) -> None:
    root, mask = _record(tmp_path)
    FakeSources.reset(mask)
    ground_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )
    malformed = np.zeros(mask.shape, dtype=np.uint8)
    malformed[1:4, 1:4] = 127
    malformed[4:7, 4:7] = 255
    FakeSources.mask = malformed

    with pytest.raises(ValueError, match="strict binary"):
        segment_record(
            root,
            allow_model_read=True,
            source_module=FakeSources,
        )

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "GROUNDING_RECORDED"
    assert manifest["last_error"]["step"] == "segment"
    assert (root / "segmentation/raw.json").is_file()
    assert (root / "segmentation/mask.png").is_file()
    assert not (root / "segmentation/mask.npy").exists()
    assert json.loads((root / "segmentation/call.json").read_text())["status"] == "error"


def test_full_object_record_chain_stops_at_validated_raw_grasps(tmp_path) -> None:
    root, mask = _record(tmp_path)
    FakeSources.reset(mask)

    manifest = ground_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )
    assert manifest["status"] == "GROUNDING_RECORDED"
    grounding = json.loads((root / "grounding/result.json").read_text())
    assert grounding["selected_reference"]["rank"] == 1

    manifest = segment_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
    )
    assert manifest["status"] == "MASK_RECORDED"
    frozen_mask = np.load(root / "segmentation/mask.npy", allow_pickle=False)
    mask_record = json.loads((root / "segmentation/mask_record.json").read_text())
    assert frozen_mask.dtype == np.dtype(np.bool_)
    assert np.array_equal(frozen_mask, mask)
    assert mask_record["foreground_pixels"] == int(mask.sum())

    manifest = project_record(root)
    assert manifest["status"] == "OBJECT_CLOUD_RECORDED"
    assignment = json.loads((root / "object/assignment.json").read_text())
    full = json.loads((root / "object/cloud_manifest.json").read_text())
    compact = json.loads((root / "object/pointcloud_manifest.json").read_text())
    object_observation = json.loads((root / "object/observation.json").read_text())
    projection_result = json.loads((root / "object/result.json").read_text())
    points = np.load(root / "object/pointcloud.npz")["points"]
    pixels = np.load(root / "object/pixels_rc.npy", allow_pickle=False)
    assert assignment["graph_object"] == _ANCHOR
    assert assignment["identity_status"] == "MODEL_PROPOSED"
    assert full["graph_object"] == _ANCHOR
    assert full["identity_status"] == "MODEL_PROPOSED"
    assert full["point_count"] == int(mask.sum())
    assert points.shape == (int(mask.sum()), 3)
    assert np.array_equal(pixels, np.argwhere(mask))
    assert set(compact) == {
        "artifact_ref",
        "unit",
        "frame",
        "calibration_ref",
        "evidence_ref",
    }
    assert compact["evidence_ref"] == str(
        (root / "object/cloud_manifest.json").resolve()
    )
    assert object_observation["objects"] == []
    assert len(projection_result["principal_axis"]) == 3
    assert projection_result["extent"]["min"][2] == pytest.approx(1.0)

    manifest = predict_record(
        root,
        allow_live_read=True,
        source_module=FakeSources,
    )
    assert manifest["status"] == "OBJECT_RAW_GRASPNET_RECORDED"
    raw = json.loads((root / "object_graspnet/raw_response.json").read_text())
    result = json.loads((root / "object_graspnet/result.json").read_text())
    request = json.loads((root / "object_graspnet/request.json").read_text())
    assert raw["grasps"][0]["object_id"] == -1
    assert raw["input_reference"] == request
    assert request["point_cloud_path"] == str(
        (root / "object/pointcloud.npz").resolve()
    )
    assert result["candidate_artifact_created"] is False
    assert result["summary"]["object_ids"] == [-1]
    assert result["identity_status"] == "MODEL_PROPOSED"
    assert result["graph_object"] == _ANCHOR
    assert result["assignment_ref"] == str(
        (root / "object/assignment.json").resolve()
    )
    assert result["object_cloud_manifest_ref"] == str(
        (root / "object/cloud_manifest.json").resolve()
    )
    assert not (root / "candidates.json").exists()


def test_predict_preserves_positive_detector_id_as_raw_evidence(tmp_path) -> None:
    root, _ = _through_project(tmp_path)
    FakeSources.raw_object_id = 7

    manifest = predict_record(
        root,
        allow_live_read=True,
        source_module=FakeSources,
    )

    raw = json.loads((root / "object_graspnet/raw_response.json").read_text())
    result = json.loads((root / "object_graspnet/result.json").read_text())
    call = json.loads((root / "object_graspnet/call.json").read_text())
    assert manifest["status"] == "OBJECT_RAW_GRASPNET_RECORDED"
    assert raw["grasps"][0]["object_id"] == 7
    assert result["summary"]["object_ids"] == [7]
    assert call["status"] == "ok"
    assert not (root / "candidates.json").exists()


@pytest.mark.parametrize("artifact", ["points", "pixels"])
def test_predict_rejects_tampered_cloud_lineage_before_transport(
    tmp_path, artifact
) -> None:
    root, _ = _through_project(tmp_path)
    if artifact == "points":
        points = np.load(root / "object/pointcloud.npz")["points"]
        np.savez_compressed(
            root / "object/pointcloud.npz",
            points=points + np.float32(0.01),
        )
    else:
        pixels = np.load(root / "object/pixels_rc.npy", allow_pickle=False)
        pixels[0, 0] += 1
        np.save(root / "object/pixels_rc.npy", pixels, allow_pickle=False)
    calls_before = list(FakeSources.calls)

    with pytest.raises(ValueError, match="frozen RGB-D lineage"):
        predict_record(
            root,
            allow_live_read=True,
            source_module=FakeSources,
        )

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "OBJECT_CLOUD_RECORDED"
    assert manifest["last_error"]["step"] == "predict_object"
    assert FakeSources.calls == calls_before
    assert not (root / "object_graspnet/raw_response.json").exists()


def test_predict_rejects_replaced_grounding_jpeg_before_transport(tmp_path) -> None:
    root, mask = _through_project(tmp_path)
    replacement = np.full((*mask.shape, 3), 255, dtype=np.uint8)
    ok, encoded = cv2.imencode(
        ".jpg", replacement, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    assert ok
    (root / "grounding/input.jpg").write_bytes(encoded.tobytes())
    calls_before = list(FakeSources.calls)

    with pytest.raises(ValueError, match="JPEG no longer matches"):
        predict_record(
            root,
            allow_live_read=True,
            source_module=FakeSources,
        )

    assert FakeSources.calls == calls_before
    assert not (root / "object_graspnet/raw_response.json").exists()


def test_predict_rejects_non_grasp_resolver_before_transport(tmp_path) -> None:
    root, _ = _through_project(
        tmp_path,
        resolver="principal_axis",
        hole_name="tube_axis",
    )
    calls_before = list(FakeSources.calls)

    with pytest.raises(ValueError, match="resolver='grasp_candidate'"):
        predict_record(
            root,
            allow_live_read=True,
            source_module=FakeSources,
        )

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "OBJECT_CLOUD_RECORDED"
    assert manifest["last_error"]["step"] == "predict_object"
    assert FakeSources.calls == calls_before


def test_part_center_records_conservative_hole_geometry(tmp_path) -> None:
    root, mask = _record(
        tmp_path,
        resolver="part_center",
        hole_name="rack_left_hole_center",
        anchor={
            "object_id": "rack",
            "part": "hole",
            "instance": "left",
            "selection": None,
        },
    )
    FakeSources.reset(mask)

    ground_record(
        root,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )
    segment_record(root, allow_model_read=True, source_module=FakeSources)
    manifest = project_record(root)

    geometry = json.loads((root / "object/hole_geometry.json").read_text())
    result = json.loads((root / "object/result.json").read_text())
    object_observation = json.loads((root / "object/observation.json").read_text())
    assert manifest["status"] == "OBJECT_CLOUD_RECORDED"
    assert manifest["artifacts"]["rack_hole_geometry"] == "object/hole_geometry.json"
    assert geometry["status"] == "UNKNOWN"
    assert geometry["reason"] == "insufficient_depth_contrast"
    assert geometry["center"] is None
    assert geometry["axis"] is None
    assert result["hole_geometry_status"] == "UNKNOWN"
    assert object_observation["objects"] == []
    assert not (root / "candidates.json").exists()


def test_object_perception_chain_has_no_control_or_oracle_imports() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src/demo_graph_lab"
    source = "\n".join(
        (source_root / relative).read_text()
        for relative in (
            "execution/object_record.py",
            "perception/object_pipeline.py",
            "perception/semantic_sources.py",
        )
    )
    for forbidden in (
        "oracle_runtime",
        "robot_api",
        "PipelineClient",
        ".ctrl(",
        "'/state'",
        '"/state"',
        ".reset(",
    ):
        assert forbidden not in source
