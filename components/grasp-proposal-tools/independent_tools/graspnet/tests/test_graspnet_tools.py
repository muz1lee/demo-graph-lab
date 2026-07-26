from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from independent_tools.graspnet import GraspNetConfig, build_request, normalize_grasp_response, rgbd_to_pointcloud
from independent_tools.graspnet.client import _service_payload
from independent_tools.graspnet.pointcloud import mask_pointcloud


class GraspNetToolTests(unittest.TestCase):
    def test_build_request_is_serializable_and_non_decisional(self) -> None:
        payload = build_request(
            image_path="frames/keyframe.png",
            depth_path="frames/keyframe_depth.png",
            mask_path="masks/object.png",
            object_hint="target object",
            frame_id="demo_frame_003",
            coordinate_frame="camera_left",
        )
        encoded = json.dumps(payload)
        self.assertIn("kw_independent.graspnet.request.v1", encoded)
        self.assertEqual(payload["inputs"]["coordinate_frame"], "camera_left")
        self.assertNotIn("route_decision", encoded)

    def test_normalizes_array_style_graspnet_response(self) -> None:
        raw = {
            "scores": [0.93, 0.81],
            "translations": [[0.12, -0.04, 0.35], [0.11, -0.05, 0.34]],
            "rotation_matrices": [
                [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
                [[0, 1, 0], [-1, 0, 0], [0, 0, 1]],
            ],
            "widths": [0.045, 0.052],
            "object_ids": ["obj", "obj"],
        }
        result = normalize_grasp_response(raw, config=GraspNetConfig(coordinate_frame="camera_frame"))
        self.assertEqual(result["schema"], "kw_independent.graspnet.proposals.v1")
        self.assertEqual(result["num_proposals"], 2)
        self.assertEqual(result["proposals"][0]["score"], 0.93)
        self.assertEqual(result["proposals"][0]["pose"]["coordinate_frame"], "camera_frame")
        self.assertEqual(result["proposals"][0]["raw_index"], 0)
        self.assertEqual(result["source_policy"], "external_grasp_proposals_no_route_decision")

    def test_normalizes_list_style_anygrasp_response_without_guessing_frame(self) -> None:
        raw = [
            {
                "score": 0.88,
                "translation": [0.2, 0.1, 0.42],
                "rotation_matrix": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
                "width": 0.04,
            }
        ]
        result = normalize_grasp_response(raw, config=GraspNetConfig(coordinate_frame=None))
        proposal = result["proposals"][0]
        self.assertEqual(proposal["pose"]["coordinate_frame"], "unknown")
        self.assertIn("coordinate frame is unknown", result["warnings"][0])
        self.assertIn("raw", proposal)

    def test_cli_normalize_writes_output(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "proposals.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "independent_tools.graspnet.cli",
                    "normalize",
                    "--raw-response",
                    str(root / "examples" / "sample_graspnet_response.json"),
                    "--config",
                    str(root / "examples" / "config.json"),
                    "--output",
                    str(out),
                ],
                cwd=str(root.parents[1]),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["num_proposals"], 2)
            self.assertEqual(payload["proposals"][0]["pose"]["coordinate_frame"], "camera_frame")

    def test_rgbd_to_pointcloud_projects_depth_and_preserves_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            depth = np.array([[1000, 0], [2000, 3000]], dtype=np.uint16)
            mask = np.array([[1, 0], [0, 1]], dtype=np.uint8)
            depth_path = root / "depth.npy"
            mask_path = root / "mask.npy"
            out = root / "cloud.npz"
            manifest_path = root / "manifest.json"
            np.save(depth_path, depth)
            np.save(mask_path, mask)

            manifest = rgbd_to_pointcloud(
                depth_path=depth_path,
                intrinsics={"fx": 100.0, "fy": 100.0, "cx": 0.0, "cy": 0.0},
                output_path=out,
                mask_path=mask_path,
                depth_scale=0.001,
                coordinate_frame="camera_left",
                manifest_path=manifest_path,
            )

            cloud = np.load(out)
            self.assertEqual(manifest["stats"]["num_output_points"], 2)
            self.assertEqual(cloud["points"].shape, (2, 3))
            self.assertEqual(cloud["pixel_xy"].tolist(), [[0, 0], [1, 1]])
            self.assertAlmostEqual(float(cloud["points"][0, 2]), 1.0)
            self.assertAlmostEqual(float(cloud["points"][1, 0]), 0.03)
            self.assertEqual(manifest["coordinate_frame"], "camera_left")
            self.assertTrue(manifest_path.exists())

    def test_mask_pointcloud_filters_using_pixel_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cloud_path = root / "cloud.npz"
            mask_path = root / "mask.npy"
            out = root / "masked.npz"
            np.savez_compressed(
                cloud_path,
                points=np.array([[0, 0, 1], [1, 1, 1], [2, 2, 1]], dtype=np.float32),
                pixel_xy=np.array([[0, 0], [1, 1], [2, 2]], dtype=np.int32),
            )
            mask = np.zeros((3, 3), dtype=np.uint8)
            mask[1, 1] = 1
            np.save(mask_path, mask)

            manifest = mask_pointcloud(point_cloud_path=cloud_path, mask_path=mask_path, output_path=out)
            masked = np.load(out)
            self.assertEqual(manifest["stats"]["num_output_points"], 1)
            self.assertEqual(masked["points"].tolist(), [[1.0, 1.0, 1.0]])
            self.assertEqual(masked["pixel_xy"].tolist(), [[1, 1]])

    def test_service_payload_unwraps_serializable_request(self) -> None:
        request = build_request(
            depth_path="/tmp/depth.npy",
            point_cloud_path="/tmp/cloud.npz",
            coordinate_frame="camera_left",
        )
        payload = _service_payload(request)
        self.assertEqual(payload["point_cloud_path"], "/tmp/cloud.npz")
        self.assertEqual(payload["coordinate_frame"], "camera_left")
        self.assertNotIn("inputs", payload)


if __name__ == "__main__":
    unittest.main()
