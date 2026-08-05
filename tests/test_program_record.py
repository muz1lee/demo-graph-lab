"""Offline tests for the PerceptionProgram executor over a frozen observation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from demo_graph_lab.execution.planning_record import plan_record
from demo_graph_lab.execution.program_record import (
    GEOMETRY_IMPLEMENTATIONS,
    TRANSPORT_OPERATORS,
    programs_record,
)
from demo_graph_lab.perception.program import (
    OPERATORS,
    coverage_by_stage,
)


_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "graphs"
_OPTICAL_FRAME = "camera_head_optical"
_HEIGHT, _WIDTH = 8, 10

_TUBE_ANCHOR = {"object_id": "tube_left", "part": "whole"}
_RACK_ANCHOR = {"object_id": "rack", "part": "hole", "instance": "left"}

_GRAPH = {
    "task": "insert_tubes",
    "stages": [
        {
            "index": 0,
            "name": "pick",
            "stage_objects": {"manipulated": "tube_left", "target": "rack"},
            "holes": [
                {
                    "name": "tube_grasp_pose",
                    "type": "pose_se3",
                    "frame": "robot_base",
                    "solver_hint": "grasp pose on the tube body",
                    "resolver": "grasp_candidate",
                    "anchor": dict(_TUBE_ANCHOR),
                },
                {
                    "name": "tube_long_axis",
                    "type": "axis_3d",
                    "frame": "robot_base",
                    "solver_hint": "long axis of the tube body",
                    "resolver": "principal_axis",
                    "anchor": dict(_TUBE_ANCHOR),
                },
            ],
        },
        {
            "index": 1,
            "name": "insertion",
            "stage_objects": {"manipulated": "tube_left", "target": "rack"},
            "holes": [
                {
                    "name": "rack_hole_center",
                    "type": "point_3d",
                    "frame": "robot_base",
                    "solver_hint": "center of the rack left-hole opening",
                    "resolver": "part_center",
                    "anchor": dict(_RACK_ANCHOR),
                },
                {
                    "name": "rack_hole_axis",
                    "type": "axis_3d",
                    "frame": "robot_base",
                    "solver_hint": "axis of the rack left-hole opening",
                    "resolver": "part_axis",
                    "anchor": dict(_RACK_ANCHOR),
                },
            ],
        },
    ],
}

_PROGRAM = {
    "schema": "demo_graph_lab.perception_program.v1",
    "task": "insert_tubes",
    "programs": [
        {
            "stage": 0,
            "chain": ["localize", "segment", "crop_points", "fit_axis"],
            "provides": [{"field": "axis", "hole": "tube_long_axis"}],
        },
        {
            "stage": 1,
            "chain": ["localize", "segment", "fit_opening"],
            "provides": [
                {"field": "center", "hole": "rack_hole_center"},
                {"field": "axis", "hole": "rack_hole_axis"},
            ],
        },
    ],
}

_OBJECTS = [
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
]

# 同一根管子被两个 stage 各问一次:合法的重复查询,两个程序本来就该命中同一个框。
_REPEAT_GRAPH = {
    "task": "insert_tubes",
    "stages": [
        {
            "index": index,
            "name": name,
            "stage_objects": {"manipulated": "tube_left", "target": "rack"},
            "holes": [
                {
                    "name": "tube_long_axis",
                    "type": "axis_3d",
                    "frame": "robot_base",
                    "solver_hint": "long axis of the tube body",
                    "resolver": "principal_axis",
                    "anchor": dict(_TUBE_ANCHOR),
                },
            ],
        }
        for index, name in enumerate(("pick", "insertion"))
    ],
}

_REPEAT_PROGRAM = {
    "schema": "demo_graph_lab.perception_program.v1",
    "task": "insert_tubes",
    "programs": [
        {
            "stage": index,
            "chain": ["localize", "segment", "crop_points", "fit_axis"],
            "provides": [{"field": "axis", "hole": "tube_long_axis"}],
        }
        for index in (0, 1)
    ],
}

_TUBES = ("tube_left", "tube_right", "tube_third")

# 今晨那次观测的形状:三根同类管子各占一个 stage,靠 distinguisher 区分。
# `tube_third` 的 distinguisher 是时序描述,单帧不可解析——这正是退化的起点。
_TUBE_OBJECTS = [
    {"id": "tube_left", "category": "test tube",
     "distinguishers": "the left tube"},
    {"id": "tube_right", "category": "test tube",
     "distinguishers": "the right tube"},
    {"id": "tube_third", "category": "test tube",
     "distinguishers": "the third tube that was inserted"},
    {"id": "rack", "category": "three-hole rack",
     "distinguishers": "the rack on the right"},
]

_TUBE_GRAPH = {
    "task": "insert_tubes",
    "stages": [
        {
            "index": index,
            "name": f"insert_{object_id}",
            "stage_objects": {"manipulated": object_id, "target": "rack"},
            "holes": [
                {
                    "name": f"{object_id}_long_axis",
                    "type": "axis_3d",
                    "frame": "robot_base",
                    "solver_hint": "long axis of the tube body",
                    "resolver": "principal_axis",
                    "anchor": {"object_id": object_id, "part": "whole"},
                },
            ],
        }
        for index, object_id in enumerate(_TUBES)
    ],
}

_TUBE_PROGRAM = {
    "schema": "demo_graph_lab.perception_program.v1",
    "task": "insert_tubes",
    "programs": [
        {
            "stage": index,
            "chain": ["localize", "segment", "crop_points", "fit_axis"],
            "provides": [{"field": "axis", "hole": f"{object_id}_long_axis"}],
        }
        for index, object_id in enumerate(_TUBES)
    ],
}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _scene_arrays(*, opening: bool, shape=(_HEIGHT, _WIDTH), mask_bounds=(1, 7, 4, 6)):
    """Synthetic RGB-D whose ROI either reads as an opening or as flat surface."""

    height, width = shape
    row0, row1, col0, col1 = mask_bounds
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 0] = (np.arange(width) % 256).astype(np.uint8)
    image[:, :, 1] = (np.arange(height) % 256).astype(np.uint8)[:, None]
    depth = np.ones((height, width), dtype=np.float32)
    mask = np.zeros((height, width), dtype=np.bool_)
    mask[row0:row1, col0:col1] = True
    if opening:
        # 让 ROI 在 RGB-D 上真的像一个开口:比周围支撑面更远、更亮,越过估计器的
        # 深度/亮度对比度门槛。手法与 tests/test_object_record.py 的 PASS 用例一致。
        depth[mask] += np.float32(0.05)
        image[mask] = 200
    return image, depth, mask


def _record(
    tmp_path: Path,
    *,
    graph: dict | None = None,
    objects: list | None = None,
    stage_index: int = 0,
    hole_name: str = "tube_grasp_pose",
    opening: bool = True,
    shape=(_HEIGHT, _WIDTH),
    mask_bounds=(1, 7, 4, 6),
) -> tuple[Path, np.ndarray, Path]:
    """Build one planned+captured record dir with a frozen synthetic capture."""

    height, width = shape
    principal = ((width - 1) / 2.0, (height - 1) / 2.0)
    root = (tmp_path / "record").resolve()
    graph_path = (tmp_path / "graph.json").resolve()
    objects_path = (tmp_path / "objects.json").resolve()
    intrinsics_path = (tmp_path / "intrinsics.json").resolve()
    program_path = (tmp_path / "perception_program.json").resolve()
    _write_json(graph_path, _GRAPH if graph is None else graph)
    _write_json(objects_path, _OBJECTS if objects is None else objects)
    _write_json(intrinsics_path, {
        "head": {
            "resolution": [width, height],
            "fx": 100.0,
            "fy": 100.0,
            "cx": principal[0],
            "cy": principal[1],
            "baseline": 0.05,
        }
    })
    plan_record(
        graph_path=graph_path,
        objects_path=objects_path,
        stage_index=stage_index,
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
    image, depth, mask = _scene_arrays(
        opening=opening, shape=shape, mask_bounds=mask_bounds
    )
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
        "frame": _OPTICAL_FRAME,
        "intrinsics": {
            "width": width,
            "height": height,
            "fx": 100.0,
            "fy": 100.0,
            "cx": principal[0],
            "cy": principal[1],
            "baseline": 0.05,
        },
    })
    _write_json(root / "observation.json", {
        "observation_id": "head-17-12500000",
        "captured_at_s": 12.5,
        "frame": _OPTICAL_FRAME,
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
    return root, mask, program_path


def _reference(box, *, width: int, height: int) -> dict:
    """One grounding reference whose 1000-scale box quantizes back to ``box``.

    契约侧按 min 边 `floor`、max 边 `ceil` 反算像素框,这里取半像素内缩,保证往返
    稳稳落回同一个框而不受浮点抖动影响。
    """

    x0, y0, x1, y1 = box
    return {
        "rank": 1,
        "bbox_1000": [
            (x0 + 0.5) * 1000.0 / width,
            (y0 + 0.5) * 1000.0 / height,
            (x1 - 0.5) * 1000.0 / width,
            (y1 - 0.5) * 1000.0 / height,
        ],
        "bbox_pixel": list(box),
    }


class FakeSources:
    """Fake Qwen/SAM3 transports; nothing here opens a socket."""

    mask = None
    calls: list = []
    reject_prompt_substring = None
    box_overrides: tuple = ()
    boxes: dict = {}

    @classmethod
    def reset(cls, mask, *, box_overrides=()) -> None:
        cls.mask = mask
        cls.calls = []
        cls.reject_prompt_substring = None
        # (查询子串, 像素框):命中的查询改用指定框,用来造真实里出现的同框情形。
        cls.box_overrides = tuple(box_overrides)
        cls.boxes = {}

    @classmethod
    def box_for(cls, prompt: str, *, width: int, height: int) -> list:
        """同一个查询固定同一个框;默认每个不同 anchor 拿到属于自己的那个框。"""

        for substring, box in cls.box_overrides:
            if substring in prompt:
                return list(box)
        if prompt not in cls.boxes:
            index = len(cls.boxes)
            # 默认框互不相同(否则不同 anchor 会假性同框),但都包住 fake mask
            # (否则会先撞上越框守卫)。
            cls.boxes[prompt] = [index % 4, 0, width - index // 4, height]
        return list(cls.boxes[prompt])

    class QwenGroundingClient:
        def __init__(self, endpoint, *, token, model, timeout_s):
            FakeSources.calls.append(("qwen_init", endpoint, token, model))

        def ground(self, jpeg_bytes, *, prompt, image_width, image_height, top_k):
            assert jpeg_bytes.startswith(b"\xff\xd8")
            assert jpeg_bytes.endswith(b"\xff\xd9")
            FakeSources.calls.append(("ground", prompt))
            reference = _reference(
                FakeSources.box_for(
                    prompt, width=image_width, height=image_height
                ),
                width=image_width,
                height=image_height,
            )
            reject = FakeSources.reject_prompt_substring
            references = [dict(reference)]
            if isinstance(reject, str) and reject in prompt:
                references = [dict(reference), {**reference, "rank": 2}]
            return {
                "references": references,
                "raw_response": {"id": "qwen-request-1"},
            }

    class Sam3SegmentationClient:
        def __init__(self, endpoint, *, token, timeout_s):
            FakeSources.calls.append(("sam3_init", endpoint, token))

        def segment(self, jpeg_bytes, *, bbox_pixel, image_width, image_height):
            assert jpeg_bytes.startswith(b"\xff\xd8")
            FakeSources.calls.append(("segment", tuple(bbox_pixel)))
            pixels = np.asarray(FakeSources.mask).astype(np.uint8) * 255
            ok, encoded = cv2.imencode(".png", pixels)
            assert ok
            return {
                "mask_bytes": encoded.tobytes(),
                "mask": {"encoding": "png", "semantic": "binary_mask"},
                "detection_metadata": {"score": 0.91},
                "raw_response": {"success": True, "request_id": "sam3-1"},
            }


def _run(tmp_path: Path, *, document=None, box_overrides=(), **record_kwargs):
    root, mask, program_path = _record(tmp_path, **record_kwargs)
    _write_json(program_path, _PROGRAM if document is None else document)
    FakeSources.reset(mask, box_overrides=box_overrides)
    return root, mask, program_path


def _results(root: Path) -> dict:
    return json.loads((root / "program_results.json").read_text())


def test_executor_implements_the_whole_operator_closed_set() -> None:
    # 契约的闭集是 perception/program.py;执行器少实现一个算子必须在这里就暴露,
    # 而不是等到某条链跑到一半才发现。
    assert TRANSPORT_OPERATORS | set(GEOMETRY_IMPLEMENTATIONS) == set(OPERATORS)
    assert not TRANSPORT_OPERATORS & set(GEOMETRY_IMPLEMENTATIONS)


def test_programs_refuses_without_model_read_before_loading_transport(tmp_path) -> None:
    class Poison:
        def __getattr__(self, name):
            raise AssertionError(f"transport accessed: {name}")

    with pytest.raises(PermissionError, match="allow-model-read"):
        programs_record(
            tmp_path / "missing",
            perception_program_path=tmp_path / "missing.json",
            source_module=Poison(),
        )
    assert not (tmp_path / "missing").exists()


def test_both_chain_shapes_publish_optical_frame_envelopes(tmp_path) -> None:
    root, mask, program_path = _run(tmp_path)

    manifest = programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    assert manifest["status"] == "PROGRAMS_RECORDED"
    assert manifest["artifacts"]["program_results"] == "program_results.json"
    results = _results(root)
    assert set(results["holes"]) == {
        "s0.tube_long_axis", "s1.rack_hole_center", "s1.rack_hole_axis"
    }
    assert [item["program"] for item in results["programs"]] == ["p0_0", "p1_1"]

    axis = results["holes"]["s0.tube_long_axis"]
    center = results["holes"]["s1.rack_hole_center"]
    opening_axis = results["holes"]["s1.rack_hole_axis"]
    for envelope in (axis, center, opening_axis):
        # frame 如实写测量所在的相机光学系。graph 请求的是 robot_base,标定链未建,
        # 所以下游 typed-hole 校验会因为 frame 不一致而拒绝——这是设计意图。
        assert envelope["frame"] == _OPTICAL_FRAME
        assert envelope["frame"] != "robot_base"
        assert envelope["status"] == "PASS"
        assert envelope["failed_step"] is None
        assert envelope["identity_status"] == "MODEL_PROPOSED"
        assert envelope["calibration_ref"] == str(
            (root / "calibration/bundle.json").resolve()
        )
        assert len(envelope["value"]) == 3
        assert all(Path(ref).is_file() for ref in envelope["evidence_refs"])
    assert axis["object_id"] == "tube_left"
    assert axis["reason"] == "pca_dominant_axis"
    assert center["object_id"] == "rack"
    assert opening_axis["object_id"] == "rack"
    assert center["program"] == opening_axis["program"] == "p1_1"

    # 两个洞由同一个程序发布,因此共享同一条证据链和同一次观测。
    assert center["evidence_refs"] == opening_axis["evidence_refs"]
    cloud = np.load(root / "programs/p0_0/geometry/pointcloud.npz")["points"]
    assignment = json.loads(
        (root / "programs/p0_0/geometry/assignment.json").read_text()
    )
    geometry = json.loads(
        (root / "programs/p1_1/geometry/opening_geometry.json").read_text()
    )
    assert cloud.shape == (int(mask.sum()), 3)
    assert assignment["identity_status"] == "MODEL_PROPOSED"
    assert assignment["graph_object"] == {
        "object_id": "tube_left", "part": "whole",
        "instance": None, "selection": None,
    }
    assert geometry["status"] == "PASS"
    assert not (root / "candidates.json").exists()


def test_localize_query_is_rendered_from_the_graph_anchor(tmp_path) -> None:
    root, _, program_path = _run(tmp_path)

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    prompts = [entry[1] for entry in FakeSources.calls if entry[0] == "ground"]
    assert len(prompts) == 2
    assert "the complete visible test tube instance" in prompts[0]
    assert "the left hole of the three-hole rack" in prompts[1]
    request = json.loads(
        (root / "programs/p1_1/grounding/request.json").read_text()
    )
    assert request["anchor"] == {
        "object_id": "rack", "part": "hole",
        "instance": "left", "selection": None,
    }
    assert request["prompt"] == prompts[1]
    assert "test-secret" not in (
        root / "programs/p1_1/grounding/request.json"
    ).read_text()


def test_one_refused_step_makes_the_whole_program_unknown(tmp_path) -> None:
    root, _, program_path = _run(tmp_path)
    FakeSources.reject_prompt_substring = "three-hole rack"

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    center = results["holes"]["s1.rack_hole_center"]
    opening_axis = results["holes"]["s1.rack_hole_axis"]
    for envelope in (center, opening_axis):
        assert envelope["status"] == "UNKNOWN"
        assert envelope["value"] is None
        assert envelope["reason"] == "grounding_reference_count_not_one"
        assert envelope["failed_step"] == "localize"
        assert envelope["frame"] == _OPTICAL_FRAME
        assert envelope["identity_status"] == "MODEL_PROPOSED"
        assert all(Path(ref).is_file() for ref in envelope["evidence_refs"])
    # all-or-nothing:链在 localize 就断了,后面的算子一步都没有跑。
    assert not (root / "programs/p1_1/segmentation").exists()
    assert not (root / "programs/p1_1/geometry").exists()
    grounding = json.loads(
        (root / "programs/p1_1/grounding/result.json").read_text()
    )
    call = json.loads((root / "programs/p1_1/call.json").read_text())
    assert grounding["status"] == "REJECTED"
    assert grounding["reference_count"] == 2
    assert call["status"] == "refused"
    # 同一次 capture 下的另一个程序不受影响。
    assert results["holes"]["s0.tube_long_axis"]["status"] == "PASS"


def test_geometry_unknown_propagates_to_every_hole_of_that_program(tmp_path) -> None:
    root, _, program_path = _run(tmp_path, opening=False)

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    geometry = json.loads(
        (root / "programs/p1_1/geometry/opening_geometry.json").read_text()
    )
    assert geometry["status"] == "UNKNOWN"
    assert geometry["reason"] == "insufficient_depth_contrast"
    for name in ("s1.rack_hole_center", "s1.rack_hole_axis"):
        envelope = results["holes"][name]
        assert envelope["status"] == "UNKNOWN"
        assert envelope["value"] is None
        # 估计器自己的 reason 原样进 envelope,不被压成一个笼统的失败码。
        assert envelope["reason"] == "insufficient_depth_contrast"
        assert envelope["failed_step"] == "fit_opening"
        assert str(
            (root / "programs/p1_1/geometry/opening_geometry.json").resolve()
        ) in envelope["evidence_refs"]
    # 几何 UNKNOWN 不阻断其它程序,也不把本程序降级成部分成功。
    assert results["holes"]["s0.tube_long_axis"]["status"] == "PASS"
    assert json.loads(
        (root / "programs/p1_1/geometry/result.json").read_text()
    )["fields"] == {}


def test_two_object_ids_on_one_box_demote_each_other(tmp_path) -> None:
    # 一个框不可能同时是两个物体:证据分不清身份,两边都不能发布。
    shared = [3, 0, 7, 8]
    root, _, program_path = _run(tmp_path, box_overrides=(
        ("test tube", shared), ("three-hole rack", shared),
    ))

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    assert [item["bbox_pixel"] for item in results["programs"]] == [shared, shared]
    assert [item["status"] for item in results["programs"]] == ["UNKNOWN", "UNKNOWN"]
    for name in ("s0.tube_long_axis", "s1.rack_hole_center", "s1.rack_hole_axis"):
        envelope = results["holes"][name]
        assert envelope["status"] == "UNKNOWN"
        assert envelope["value"] is None
        assert envelope["reason"] == "grounding_identity_collision"
        assert envelope["failed_step"] == "localize"
        assert envelope["identity_status"] == "MODEL_PROPOSED"
        assert all(Path(ref).is_file() for ref in envelope["evidence_refs"])
    # 互相点名:归因不必回头翻另一个程序的产物。
    assert results["holes"]["s0.tube_long_axis"]["collides_with"] == ["p1_1"]
    assert results["holes"]["s1.rack_hole_center"]["collides_with"] == ["p0_0"]
    assert results["holes"]["s1.rack_hole_axis"]["collides_with"] == ["p0_0"]
    assert [item["collides_with"] for item in results["programs"]] == [
        ["p1_1"], ["p0_0"]
    ]
    # 产物留在原地作证据:链确实跑完了,降级只发生在 envelope 上。
    assert (root / "programs/p0_0/geometry/pointcloud.npz").is_file()
    for program in ("p0_0", "p1_1"):
        assert json.loads(
            (root / f"programs/{program}/geometry/result.json").read_text()
        )["status"] == "PASS"
        assert json.loads(
            (root / f"programs/{program}/call.json").read_text()
        )["status"] == "ok"


def test_one_object_queried_twice_on_one_box_still_passes(tmp_path) -> None:
    # 今晨 p0_0/p1_1 的情形:同一个 object_id 被两个程序各查一次,命中同一个框是
    # 正常的,守卫不许误伤。
    root, _, program_path = _run(
        tmp_path,
        graph=_REPEAT_GRAPH,
        document=_REPEAT_PROGRAM,
        hole_name="tube_long_axis",
    )

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    boxes = [item["bbox_pixel"] for item in results["programs"]]
    assert [item["program"] for item in results["programs"]] == ["p0_0", "p1_1"]
    assert boxes[0] == boxes[1]
    assert set(results["holes"]) == {"s0.tube_long_axis", "s1.tube_long_axis"}
    for envelope in results["holes"].values():
        assert envelope["status"] == "PASS"
        assert envelope["collides_with"] == []
        assert envelope["object_id"] == "tube_left"
        assert len(envelope["value"]) == 3


def test_three_programs_demote_only_the_two_sharing_a_box(tmp_path) -> None:
    shared = [3, 0, 7, 8]
    root, _, program_path = _run(
        tmp_path,
        graph=_TUBE_GRAPH,
        document=_TUBE_PROGRAM,
        objects=_TUBE_OBJECTS,
        hole_name="tube_left_long_axis",
        box_overrides=(("the right tube", shared), ("the third tube", shared)),
    )

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    left = results["holes"]["s0.tube_left_long_axis"]
    right = results["holes"]["s1.tube_right_long_axis"]
    third = results["holes"]["s2.tube_third_long_axis"]
    # 没参与同框的第三个程序不受牵连。
    assert left["status"] == "PASS"
    assert left["collides_with"] == []
    assert len(left["value"]) == 3
    for envelope in (right, third):
        assert envelope["status"] == "UNKNOWN"
        assert envelope["value"] is None
        assert envelope["reason"] == "grounding_identity_collision"
    assert right["collides_with"] == ["p2_2"]
    assert third["collides_with"] == ["p1_1"]


def test_morning_shared_box_between_tube_right_and_tube_third(tmp_path) -> None:
    """2026-08-05 5090 实跑的回归:`tube_third` 的 distinguisher 是时序描述,单帧
    不可解析,Qwen 退化成「右边那根」,与 `tube_right` 返回同一个框
    `[730, 387, 811, 483]`,`tube_third_long_axis` 因此拿到一个来自错误物体、逐位
    等于 `tube_right` 的 PASS 值。守卫必须让这两个都记 UNKNOWN。
    """

    morning_box = [730, 387, 811, 483]
    root, _, program_path = _run(
        tmp_path,
        graph=_TUBE_GRAPH,
        document=_TUBE_PROGRAM,
        objects=_TUBE_OBJECTS,
        hole_name="tube_left_long_axis",
        # 框是实测值,画幅取一个能容纳它的头相机分辨率;mask 取细长条,PCA 才有
        # 明确主轴(近方形的点云会被 fit_axis 判为 ambiguous)。
        shape=(720, 1280),
        mask_bounds=(390, 480, 745, 765),
        box_overrides=(
            ("the right tube", morning_box), ("the third tube", morning_box),
        ),
    )

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    summaries = {item["program"]: item for item in results["programs"]}
    assert summaries["p1_1"]["bbox_pixel"] == morning_box
    assert summaries["p2_2"]["bbox_pixel"] == morning_box
    right = results["holes"]["s1.tube_right_long_axis"]
    third = results["holes"]["s2.tube_third_long_axis"]
    for envelope, peer in ((right, "p2_2"), (third, "p1_1")):
        assert envelope["status"] == "UNKNOWN"
        assert envelope["value"] is None
        assert envelope["reason"] == "grounding_identity_collision"
        assert envelope["failed_step"] == "localize"
        assert envelope["collides_with"] == [peer]
    assert results["holes"]["s0.tube_left_long_axis"]["status"] == "PASS"


def test_a_collided_program_keeps_its_own_earlier_failure_reason(tmp_path) -> None:
    # 冲突另一端已经因为自己链上的失败记了 UNKNOWN 时,保留那个更具体的 reason,
    # 冲突本身走 collides_with 记账。
    shared = [3, 0, 7, 8]
    root, _, program_path = _run(tmp_path, opening=False, box_overrides=(
        ("test tube", shared), ("three-hole rack", shared),
    ))

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    tube = results["holes"]["s0.tube_long_axis"]
    center = results["holes"]["s1.rack_hole_center"]
    assert tube["status"] == "UNKNOWN"
    assert tube["reason"] == "grounding_identity_collision"
    assert tube["failed_step"] == "localize"
    assert tube["collides_with"] == ["p1_1"]
    assert center["status"] == "UNKNOWN"
    assert center["value"] is None
    assert center["reason"] == "insufficient_depth_contrast"
    assert center["failed_step"] == "fit_opening"
    assert center["collides_with"] == ["p0_0"]


def test_programs_step_advances_the_manifest_and_refuses_a_rerun(tmp_path) -> None:
    root, _, program_path = _run(tmp_path)

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )
    # 状态门先于产物门:record 已经推进到 PROGRAMS_RECORDED,再跑一次就不是
    # 「已 capture」的记录了。
    with pytest.raises(ValueError, match="OBSERVATION_RECORDED"):
        programs_record(
            root,
            perception_program_path=program_path,
            allow_model_read=True,
            source_module=FakeSources,
            qwen_token="test-secret",
        )

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "PROGRAMS_RECORDED"
    assert manifest["last_error"] is None


def test_programs_refuses_to_overwrite_existing_program_artifacts(tmp_path) -> None:
    root, _, program_path = _run(tmp_path)
    (root / "programs").mkdir()

    with pytest.raises(FileExistsError, match="already exist"):
        programs_record(
            root,
            perception_program_path=program_path,
            allow_model_read=True,
            source_module=FakeSources,
            qwen_token="test-secret",
        )

    assert FakeSources.calls == []
    assert json.loads((root / "manifest.json").read_text())["status"] == (
        "OBSERVATION_RECORDED"
    )


def test_programs_requires_a_captured_observation(tmp_path) -> None:
    root, _, program_path = _run(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["status"] = "PLANNED"
    _write_json(root / "manifest.json", manifest)

    with pytest.raises(ValueError, match="OBSERVATION_RECORDED"):
        programs_record(
            root,
            perception_program_path=program_path,
            allow_model_read=True,
            source_module=FakeSources,
            qwen_token="test-secret",
        )
    assert FakeSources.calls == []


def test_programs_rejects_an_invalid_document_before_any_model_call(tmp_path) -> None:
    broken = {
        **_PROGRAM,
        "programs": [{
            "stage": 1,
            "chain": ["localize", "segment", "crop_points", "fit_axis"],
            # part_axis 的语义由 fit_opening.axis 定义;类型一致不能替代绑定。
            "provides": [{"field": "axis", "hole": "rack_hole_axis"}],
        }],
    }
    root, _, program_path = _run(tmp_path, document=broken)

    with pytest.raises(ValueError, match="PerceptionProgram validation failed"):
        programs_record(
            root,
            perception_program_path=program_path,
            allow_model_read=True,
            source_module=FakeSources,
            qwen_token="test-secret",
        )

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "OBSERVATION_RECORDED"
    assert manifest["last_error"]["step"] == "programs"
    assert FakeSources.calls == []
    assert not (root / "program_results.json").exists()


def test_insert_tubes_fixture_fills_exactly_the_covered_holes(tmp_path) -> None:
    graph = json.loads((_FIXTURE_ROOT / "insert_tubes.graph.json").read_text())
    objects = json.loads((_FIXTURE_ROOT / "insert_tubes.objects.json").read_text())
    document = json.loads(
        (_FIXTURE_ROOT / "insert_tubes.perception_program.json").read_text()
    )
    root, _, program_path = _run(
        tmp_path,
        document=document,
        graph=graph,
        objects=objects,
        stage_index=0,
        hole_name="tube_mid_grasp_pose",
    )

    programs_record(
        root,
        perception_program_path=program_path,
        allow_model_read=True,
        source_module=FakeSources,
        qwen_token="test-secret",
    )

    results = _results(root)
    coverage = coverage_by_stage(document, graph)
    covered = {
        f"s{entry['stage']}.{name}"
        for entry in coverage for name in entry["covered"]
    }
    uncovered = {
        f"s{entry['stage']}.{name}"
        for entry in coverage for name in entry["uncovered"]
    }
    filled = {
        name for name, envelope in results["holes"].items()
        if envelope["status"] == "PASS"
    }
    assert len(results["programs"]) == 9
    assert filled == covered
    assert set(results["holes"]) & uncovered == set()
    assert all(
        envelope["frame"] == _OPTICAL_FRAME
        and envelope["identity_status"] == "MODEL_PROPOSED"
        for envelope in results["holes"].values()
    )
    # 程序身份是文档索引,与 fake 干跑同规;执行顺序按 (stage, index)。
    assert [item["program"] for item in results["programs"]] == [
        "p0_0", "p1_1", "p1_2", "p2_3", "p3_4", "p3_5", "p4_6", "p4_7", "p5_8",
    ]
    assert all(
        (root / item["artifact_dir"] / "call.json").is_file()
        for item in results["programs"]
    )


def test_program_executor_has_no_control_or_oracle_imports() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src/demo_graph_lab/execution/program_record.py"
    ).read_text()
    for forbidden in (
        "oracle_runtime",
        "robot_api",
        "planning_runtime",
        "PipelineClient",
        ".ctrl(",
        "'/state'",
        '"/state"',
        "candidate",
    ):
        assert forbidden not in source
