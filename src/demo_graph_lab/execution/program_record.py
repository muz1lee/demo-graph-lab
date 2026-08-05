"""Execute one published PerceptionProgram over a frozen record observation.

This is the first runtime consumer of ``perception_program.json``.  The compiled
document only says which closed-set operator chain publishes which graph hole;
every query string is rendered here from the hole's own graph anchor by the same
trusted renderer the single-anchor recorder uses, so the backend model still
writes no text, no parameters and no numbers.

The structure is the one ``docs/API.md`` predicted: one ``capture`` is the parent
observation and every program is an anchor-scoped child task under
``programs/p<stage>_<index>/``.  Published values keep the frame they were
actually measured in — the head optical frame.  No transform to ``robot_base``
exists yet, so an honest frame is what makes downstream binding validation refuse
these values instead of silently accepting mislabelled geometry.

Failure is all-or-nothing per program: any refused step publishes ``UNKNOWN`` for
every hole that program provides, with a machine-readable reason and the evidence
produced so far.  Partial success would let a caller believe a hole was filled.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any

from ..perception.fake_runtime import qualified_hole
from ..perception.object_pipeline import (
    GeometryStatus,
    MASK_SCHEMA,
    build_object_point_cloud,
    estimate_planar_opening_geometry,
    make_object_assignment_record,
    validate_mask_record,
)
from ..perception.operators import fit_principal_axis
from ..perception.program import (
    OPERATORS,
    program_id,
    validate_perception_program,
)
from .object_record import (
    _complete,
    _config_positive_integer,
    _config_string,
    _config_timeout,
    _decode_binary_png,
    _fail_stage,
    _frozen_observation,
    _image_array,
    _jpeg_bytes,
    _mask_outside_box,
    _validated_grounding_reference,
)
from .planning_record import (
    _load_manifest,
    _one_stage,
    _perception_request,
    _read_json,
    _registry_objects,
    _required_string,
    _revalidate_record_plan,
    _utc_now,
    _write_json,
)


_RESULTS_SCHEMA = "demo_graph_lab.perception_program_results.v1"
_GROUND_REQUEST_SCHEMA = "demo_graph_lab.program_grounding_request.v1"
_GROUND_RESULT_SCHEMA = "demo_graph_lab.program_grounding_record.v1"
_SEGMENT_REQUEST_SCHEMA = "demo_graph_lab.program_segmentation_request.v1"
_SEGMENT_RESULT_SCHEMA = "demo_graph_lab.program_segmentation_record.v1"
_GEOMETRY_REQUEST_SCHEMA = "demo_graph_lab.program_geometry_request.v1"
_GEOMETRY_RESULT_SCHEMA = "demo_graph_lab.program_geometry_record.v1"
_PRINCIPAL_AXIS_SCHEMA = "demo_graph_lab.program_principal_axis.v1"

_IDENTITY_STATUS = "MODEL_PROPOSED"
_PASS = "PASS"
_UNKNOWN = "UNKNOWN"
_TOP_K = 2

_PROGRAM_ARTIFACTS = {
    "program_results": "program_results.json",
    "program_input_image": "programs/observation_input.jpg",
    "programs_call": "programs/call.json",
}

# 算子 → 可信实现的绑定表。``localize``/``segment`` 是传输,由 ``source_module``
# 注入;这里只放本地几何实现,默认值就是 ``perception/program.py`` 注释里声明的那
# 三个函数。``crop_points`` 走 ``build_object_point_cloud``:它内部正是
# ``project_masked_depth``,外加 record 侧需要的 MODEL_PROPOSED assignment 与
# manifest,所以复用它比再写一份 provenance 组装更诚实。
GEOMETRY_IMPLEMENTATIONS = {
    "crop_points": build_object_point_cloud,
    "fit_opening": estimate_planar_opening_geometry,
    "fit_axis": fit_principal_axis,
}
TRANSPORT_OPERATORS = frozenset({"localize", "segment"})


class _ProgramFailure(Exception):
    """One chain step refused; the whole program must publish ``UNKNOWN``.

    ``reason`` is a stable snake_case code that enters the hole envelope.
    ``detail`` keeps the original message for the per-program record so the
    envelope stays machine-readable instead of carrying free text.
    """

    def __init__(self, step: str, reason: str, detail: str = "") -> None:
        super().__init__(f"{step}:{reason}")
        self.step = step
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class _Scene:
    """Everything one frozen capture contributes to every program in the run."""

    root: Path
    observation: Any
    image_ref: str
    depth_ref: str
    jpeg: bytes
    image_shape: tuple[int, int]
    depth: Any
    rgb: Any
    intrinsics: Mapping[str, Any]
    config: Mapping[str, Any]
    clients: Any
    impls: Mapping[str, Any]
    qwen_token: str
    sam3_token: str | None


def _frozen_scene(root: Path, config: Mapping[str, Any], clients, impls,
                  qwen_token: str, sam3_token: str | None,
                  image_path: Path) -> _Scene:
    """Freeze the shared parent observation once for all anchor sub-tasks."""

    import numpy as np

    try:
        import cv2
    except ImportError as error:  # pragma: no cover - optional runtime install
        raise RuntimeError("program recording requires opencv-python") from error

    _, observation = _frozen_observation(root)
    image = _image_array(root, observation)
    height, width = image.shape[:2]
    jpeg = _jpeg_bytes(image)
    image_path.write_bytes(jpeg)

    depth_path = (root / "sensor/head_depth_m.npy").resolve()
    calibration_path = (root / "calibration/bundle.json").resolve()
    for path in (depth_path, calibration_path):
        if str(path) not in observation.sensor_refs or not path.is_file():
            raise ValueError(f"frozen artifact does not belong to observation: {path}")
    if observation.calibration_ref != str(calibration_path):
        raise ValueError("observation calibration_ref does not name this record")
    depth = np.load(depth_path, allow_pickle=False)
    if depth.shape != (height, width):
        raise ValueError("frozen depth shape does not match the frozen head image")
    calibration = _read_json(calibration_path)
    if not isinstance(calibration, Mapping):
        raise ValueError("calibration record is invalid")

    # fit_opening 的 rgb_ref 必须是 mask_record 里那张图,所以它读的就是刚写下的
    # 共享 JPEG,而不是 .npy 原图——两者经过 JPEG 编码后并不逐像素相同。
    rgb = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if rgb is None or rgb.shape != (height, width, 3):
        raise ValueError("frozen program JPEG cannot be decoded at RGB-D shape")
    return _Scene(
        root=root,
        observation=observation,
        image_ref=str(image_path),
        depth_ref=str(depth_path),
        jpeg=jpeg,
        image_shape=(height, width),
        depth=depth,
        rgb=rgb,
        intrinsics=calibration.get("intrinsics"),
        config=config,
        clients=clients,
        impls=impls,
        qwen_token=qwen_token,
        sam3_token=sam3_token,
    )


def _localize(scene: _Scene, program_dir: Path, identity: str,
              request: Mapping[str, Any], evidence: list[str]) -> dict[str, Any]:
    """Ask the grounding client for exactly one box for this program's anchor."""

    directory = program_dir / "grounding"
    directory.mkdir(parents=True)
    height, width = scene.image_shape
    endpoint = _config_string(scene.config, "qwen_url")
    model = _config_string(scene.config, "qwen_model")
    logical_request = {
        "schema": _GROUND_REQUEST_SCHEMA,
        "program": identity,
        "observation_id": scene.observation.observation_id,
        "image_ref": scene.image_ref,
        "image_shape": [height, width],
        "hole_name": request["hole_name"],
        "resolver": request["resolver"],
        "anchor": request["anchor"],
        "prompt": request["prompt"],
        "endpoint": endpoint,
        "model": model,
        "top_k": _TOP_K,
    }
    _write_json(directory / "request.json", logical_request)
    evidence.append(str((directory / "request.json").resolve()))

    try:
        client = scene.clients.QwenGroundingClient(
            endpoint,
            token=scene.qwen_token,
            model=model,
            timeout_s=_config_timeout(scene.config),
        )
        response = client.ground(
            scene.jpeg,
            prompt=request["prompt"],
            image_width=width,
            image_height=height,
            top_k=_TOP_K,
        )
    except Exception as error:
        raise _ProgramFailure(
            "localize", "grounding_client_error", str(error)
        ) from error
    if not isinstance(response, Mapping):
        raise _ProgramFailure(
            "localize", "grounding_response_malformed", "result must be an object"
        )
    raw = response.get("raw_response")
    if isinstance(raw, Mapping):
        _write_json(directory / "raw.json", raw)
        evidence.append(str((directory / "raw.json").resolve()))
    references = response.get("references")
    if not isinstance(references, list):
        raise _ProgramFailure(
            "localize", "grounding_response_malformed", "references must be a list"
        )

    reference: dict[str, Any] | None = None
    failure: _ProgramFailure | None = None
    if len(references) != 1:
        failure = _ProgramFailure(
            "localize",
            "grounding_reference_count_not_one",
            f"received {len(references)} references",
        )
    else:
        try:
            reference = _validated_grounding_reference(
                references[0], width=width, height=height
            )
        except (TypeError, ValueError) as error:
            failure = _ProgramFailure(
                "localize", "grounding_bbox_invalid", str(error)
            )
    if reference is not None:
        box = reference["bbox_pixel"]
        if not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
            reference, failure = None, _ProgramFailure(
                "localize",
                "grounding_bbox_outside_image",
                f"bbox {box} leaves the frozen image",
            )
    result = {
        "schema": _GROUND_RESULT_SCHEMA,
        "status": "ACCEPTED" if reference is not None else "REJECTED",
        "program": identity,
        "observation_id": scene.observation.observation_id,
        "hole_name": request["hole_name"],
        "resolver": request["resolver"],
        "anchor": request["anchor"],
        "image_ref": scene.image_ref,
        "prompt": request["prompt"],
        "reference_count": len(references),
        "selected_reference": reference,
        "reason": None if failure is None else failure.reason,
    }
    _write_json(directory / "result.json", result)
    evidence.append(str((directory / "result.json").resolve()))
    if failure is not None:
        raise failure
    return reference


def _segment(scene: _Scene, program_dir: Path, identity: str,
             reference: Mapping[str, Any], evidence: list[str]):
    """Segment the one accepted box and freeze a validated boolean mask."""

    import numpy as np

    directory = program_dir / "segmentation"
    directory.mkdir(parents=True)
    height, width = scene.image_shape
    box = list(reference["bbox_pixel"])
    grounding_ref = str((program_dir / "grounding/result.json").resolve())
    endpoint = _config_string(scene.config, "sam3_url")
    request = {
        "schema": _SEGMENT_REQUEST_SCHEMA,
        "program": identity,
        "observation_id": scene.observation.observation_id,
        "image_ref": scene.image_ref,
        "grounding_ref": grounding_ref,
        "proposal_id": f"qwen_reference_{reference['rank']}",
        "bbox_pixel": box,
        "endpoint": endpoint,
    }
    _write_json(directory / "request.json", request)
    evidence.append(str((directory / "request.json").resolve()))

    try:
        client = scene.clients.Sam3SegmentationClient(
            endpoint,
            token=scene.sam3_token,
            timeout_s=_config_timeout(scene.config),
        )
        response = client.segment(
            scene.jpeg,
            bbox_pixel=box,
            image_width=width,
            image_height=height,
        )
    except Exception as error:
        raise _ProgramFailure(
            "segment", "segmentation_client_error", str(error)
        ) from error
    if not isinstance(response, Mapping):
        raise _ProgramFailure(
            "segment", "segmentation_response_malformed", "result must be an object"
        )
    raw = response.get("raw_response")
    if isinstance(raw, Mapping):
        _write_json(directory / "raw.json", raw)
        evidence.append(str((directory / "raw.json").resolve()))
    mask_bytes = response.get("mask_bytes")
    if not isinstance(mask_bytes, bytes):
        raise _ProgramFailure(
            "segment", "segmentation_response_malformed", "mask bytes are missing"
        )
    png_path = (directory / "mask.png").resolve()
    png_path.write_bytes(mask_bytes)
    evidence.append(str(png_path))
    try:
        mask = _decode_binary_png(mask_bytes, shape=(height, width))
    except (TypeError, ValueError, RuntimeError) as error:
        raise _ProgramFailure(
            "segment", "segmentation_mask_invalid", str(error)
        ) from error
    # 与单 anchor 链共用同一个越框守卫(含 1px 量化容差);两条链对同一张 mask
    # 的判定必须一致,否则 segment 收下的记录会在另一条链上被判 UNKNOWN。
    if _mask_outside_box(mask, box):
        raise _ProgramFailure(
            "segment",
            "segmentation_mask_outside_box",
            "mask contains foreground outside the accepted box",
        )

    mask_path = (directory / "mask.npy").resolve()
    mask_record_path = (directory / "mask_record.json").resolve()
    mask_record = {
        "schema": MASK_SCHEMA,
        "observation_id": scene.observation.observation_id,
        "image_ref": scene.image_ref,
        "grounding_ref": grounding_ref,
        "proposal_id": request["proposal_id"],
        "mask_ref": str(mask_path),
        "shape": [height, width],
        "encoding": "bool",
        "foreground_pixels": int(np.count_nonzero(mask)),
    }
    try:
        mask_record = validate_mask_record(
            mask_record,
            mask,
            expected_observation_id=scene.observation.observation_id,
        )
    except (TypeError, ValueError) as error:
        raise _ProgramFailure(
            "segment", "segmentation_mask_invalid", str(error)
        ) from error
    np.save(mask_path, mask, allow_pickle=False)
    _write_json(mask_record_path, mask_record)
    result = {
        "schema": _SEGMENT_RESULT_SCHEMA,
        "status": "ACCEPTED",
        "program": identity,
        "observation_id": scene.observation.observation_id,
        "image_ref": scene.image_ref,
        "grounding_ref": grounding_ref,
        "bbox_pixel": box,
        "mask_png_ref": str(png_path),
        "mask_ref": str(mask_path),
        "mask_record_ref": str(mask_record_path),
        "mask_metadata": response.get("mask"),
        "detection_metadata": response.get("detection_metadata", {}),
    }
    _write_json(directory / "result.json", result)
    evidence.extend([str(mask_path), str(mask_record_path),
                     str((directory / "result.json").resolve())])
    return mask, mask_record


def _crop_points(scene: _Scene, directory: Path, anchor: Mapping[str, Any],
                 mask, mask_record: Mapping[str, Any], evidence: list[str]):
    """Project the masked depth into an object-only cloud with full lineage."""

    import numpy as np

    cloud_path = (directory / "pointcloud.npz").resolve()
    pixels_path = (directory / "pixels_rc.npy").resolve()
    assignment_path = (directory / "assignment.json").resolve()
    manifest_path = (directory / "cloud_manifest.json").resolve()
    assignment = make_object_assignment_record(
        observation_id=scene.observation.observation_id,
        object_id=anchor["object_id"],
        part=anchor["part"],
        instance=anchor["instance"],
        selection=anchor["selection"],
        grounding_ref=mask_record["grounding_ref"],
        mask_ref=mask_record["mask_ref"],
        cloud_ref=str(cloud_path),
        cloud_manifest_ref=str(manifest_path),
        frame=scene.observation.frame,
        calibration_ref=scene.observation.calibration_ref,
    )
    try:
        cloud = scene.impls["crop_points"](
            scene.depth,
            mask,
            scene.intrinsics,
            mask_record=mask_record,
            assignment_record=assignment,
            assignment_ref=str(assignment_path),
            depth_ref=scene.depth_ref,
            pixel_lineage_ref=str(pixels_path),
            expected_graph_object=anchor,
            min_points=_config_positive_integer(scene.config, "min_object_points"),
        )
    except (TypeError, ValueError, RuntimeError) as error:
        raise _ProgramFailure(
            "crop_points", "crop_points_failed", str(error)
        ) from error
    np.savez_compressed(cloud_path, points=cloud.points)
    np.save(pixels_path, cloud.pixels_rc, allow_pickle=False)
    _write_json(assignment_path, assignment)
    _write_json(manifest_path, cloud.manifest_record())
    evidence.extend([str(cloud_path), str(pixels_path),
                     str(assignment_path), str(manifest_path)])
    return cloud.points


def _fit_opening(scene: _Scene, directory: Path, mask,
                 mask_record: Mapping[str, Any],
                 evidence: list[str]) -> dict[str, list[float]]:
    """Recompute one opening center/axis from local RGB-D support evidence."""

    try:
        geometry = scene.impls["fit_opening"](
            scene.rgb,
            scene.depth,
            mask,
            scene.intrinsics,
            observation_id=scene.observation.observation_id,
            frame=scene.observation.frame,
            calibration_ref=scene.observation.calibration_ref,
            rgb_ref=scene.image_ref,
            depth_ref=scene.depth_ref,
            roi_record=mask_record,
        )
        record = geometry.to_record()
    except (TypeError, ValueError, KeyError, RuntimeError) as error:
        raise _ProgramFailure(
            "fit_opening", "fit_opening_failed", str(error)
        ) from error
    geometry_path = (directory / "opening_geometry.json").resolve()
    _write_json(geometry_path, record)
    evidence.append(str(geometry_path))
    # UNKNOWN 是估计器的正常输出,不是异常;但对本程序来说链没有走完,所以照样
    # 触发 all-or-nothing,并把估计器自己的 reason 原样带进 envelope。
    if record["status"] != GeometryStatus.PASS.value:
        raise _ProgramFailure("fit_opening", record["reason"], "opening geometry is UNKNOWN")
    return {"center": list(record["center"]), "axis": list(record["axis"])}


def _fit_axis(scene: _Scene, directory: Path, identity: str, points,
              evidence: list[str]) -> dict[str, list[float]]:
    """Take the dominant PCA axis of the object-only cloud."""

    try:
        axis = scene.impls["fit_axis"](points)
    except (TypeError, ValueError, RuntimeError) as error:
        raise _ProgramFailure("fit_axis", "fit_axis_failed", str(error)) from error
    axis_path = (directory / "principal_axis.json").resolve()
    _write_json(axis_path, {
        "schema": _PRINCIPAL_AXIS_SCHEMA,
        "program": identity,
        "observation_id": scene.observation.observation_id,
        "frame": scene.observation.frame,
        "calibration_ref": scene.observation.calibration_ref,
        "axis_convention": "unit_vector",
        "method": "pca_dominant_axis",
        "point_count": int(len(points)),
        "axis": list(axis),
    })
    evidence.append(str(axis_path))
    return {"axis": list(axis)}


def _run_geometry(scene: _Scene, program_dir: Path, identity: str,
                  anchor: Mapping[str, Any], chain: list[str], mask,
                  mask_record: Mapping[str, Any],
                  evidence: list[str]) -> dict[str, list[float]]:
    """Walk the post-segmentation tail of the chain and publish its fields."""

    directory = program_dir / "geometry"
    directory.mkdir(parents=True)
    terminal = chain[-1]
    _write_json(directory / "request.json", {
        "schema": _GEOMETRY_REQUEST_SCHEMA,
        "program": identity,
        "observation_id": scene.observation.observation_id,
        "identity_status": _IDENTITY_STATUS,
        "graph_object": dict(anchor),
        "chain": list(chain),
        "terminal_operator": terminal,
        "frame": scene.observation.frame,
        "calibration_ref": scene.observation.calibration_ref,
        "rgb_ref": scene.image_ref,
        "depth_ref": scene.depth_ref,
        "mask_ref": mask_record["mask_ref"],
        "mask_record_ref": str(
            (program_dir / "segmentation/mask_record.json").resolve()
        ),
    })
    evidence.append(str((directory / "request.json").resolve()))

    fields: dict[str, list[float]] = {}
    failure: _ProgramFailure | None = None
    payload: Any = mask
    try:
        # 类型表让链的前两步只能是 localize→segment(只有 localize 产出 BBOX,只有
        # segment 消费它并产出 MASK),所以尾巴从 chain[2] 开始是校验器的推论。
        for operator in chain[2:]:
            if operator == "crop_points":
                payload = _crop_points(
                    scene, directory, anchor, mask, mask_record, evidence
                )
            elif operator == "fit_opening":
                payload = _fit_opening(scene, directory, mask, mask_record, evidence)
            elif operator == "fit_axis":
                payload = _fit_axis(scene, directory, identity, payload, evidence)
            else:
                # 契约的闭集变了而执行器没跟上时必须硬停,不能静默少跑一步。
                raise ValueError(f"executor has no implementation for operator {operator!r}")
        fields = payload
    except _ProgramFailure as error:
        failure = error

    _write_json(directory / "result.json", {
        "schema": _GEOMETRY_RESULT_SCHEMA,
        "program": identity,
        "status": _PASS if failure is None else _UNKNOWN,
        "reason": None if failure is None else failure.reason,
        "failed_step": None if failure is None else failure.step,
        "observation_id": scene.observation.observation_id,
        "identity_status": _IDENTITY_STATUS,
        "graph_object": dict(anchor),
        "terminal_operator": terminal,
        "frame": scene.observation.frame,
        "calibration_ref": scene.observation.calibration_ref,
        "fields": dict(fields),
        "evidence_refs": list(evidence),
    })
    evidence.append(str((directory / "result.json").resolve()))
    if failure is not None:
        raise failure
    return fields


def _pass_reason(terminal: str) -> str:
    return {
        "fit_opening": "estimated_from_rgbd_roi_and_local_support_plane",
        "fit_axis": "pca_dominant_axis",
    }[terminal]


def _execute_program(scene: _Scene, directory: Path, offset: int,
                     program: Mapping[str, Any], stage: Mapping[str, Any],
                     registry: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Run one program end to end; never publish part of a broken chain."""

    stage_index = program["stage"]
    identity = program_id(stage_index, offset)
    program_dir = directory / identity
    program_dir.mkdir()
    chain = list(program["chain"])
    holes = [entry["hole"] for entry in program["provides"]]
    # 校验器已保证同一程序的洞共享逐字段相同的 anchor,所以查询只按第一个洞渲染;
    # 渲染器与单 anchor record 是同一个,model 依旧写不了任何查询文本。
    request = _perception_request(stage, registry, holes[0])
    anchor = request["anchor"]

    started = time.monotonic()
    call = {
        "step": "program",
        "program": identity,
        "stage": stage_index,
        "chain": chain,
        "started_at": _utc_now(),
        "allow_model_read": True,
        "status": "running",
    }
    evidence: list[str] = []
    fields: dict[str, list[float]] = {}
    failure: _ProgramFailure | None = None
    try:
        reference = _localize(scene, program_dir, identity, request, evidence)
        mask, mask_record = _segment(
            scene, program_dir, identity, reference, evidence
        )
        fields = _run_geometry(
            scene, program_dir, identity, anchor, chain, mask, mask_record, evidence
        )
    except _ProgramFailure as error:
        failure = error

    call.update({
        "status": "ok" if failure is None else "refused",
        "duration_s": time.monotonic() - started,
        "reason": None if failure is None else failure.reason,
        "failed_step": None if failure is None else failure.step,
        "detail": None if failure is None else failure.detail,
    })
    _write_json(program_dir / "call.json", call)
    evidence.append(str((program_dir / "call.json").resolve()))

    status = _PASS if failure is None else _UNKNOWN
    reason = _pass_reason(chain[-1]) if failure is None else failure.reason
    envelopes = {}
    for entry in program["provides"]:
        envelopes[qualified_hole(stage_index, entry["hole"])] = {
            # frame 如实写测量所在的相机光学系。标定链未建,所以下游 typed-hole
            # 校验会因为 frame 不等于 graph 请求的 robot_base 而拒绝——这正是
            # 设计意图,不是缺陷。
            "value": fields[entry["field"]] if failure is None else None,
            "frame": scene.observation.frame,
            "calibration_ref": scene.observation.calibration_ref,
            "object_id": anchor["object_id"],
            "identity_status": _IDENTITY_STATUS,
            "status": status,
            "reason": reason,
            "failed_step": None if failure is None else failure.step,
            "evidence_refs": list(evidence),
            "program": identity,
        }
    return {
        "summary": {
            "program": identity,
            "stage": stage_index,
            "chain": chain,
            "anchor": dict(anchor),
            "hole_name_rendered_from": holes[0],
            "provides": sorted(envelopes),
            "status": status,
            "reason": reason,
            "failed_step": None if failure is None else failure.step,
            "detail": None if failure is None else failure.detail,
            "artifact_dir": f"programs/{identity}",
        },
        "holes": envelopes,
    }


def programs_record(
    record_dir: str | Path,
    *,
    perception_program_path: str | Path,
    allow_model_read: bool = False,
    source_module=None,
    operator_impls: Mapping[str, Any] | None = None,
    qwen_token: str | None = None,
    sam3_token: str | None = None,
) -> dict[str, Any]:
    """Execute every program of one published PerceptionProgram document."""

    if allow_model_read is not True:
        raise PermissionError("programs requires --allow-model-read")
    root, manifest = _load_manifest(record_dir)
    if manifest.get("status") != "OBSERVATION_RECORDED":
        raise ValueError("programs requires manifest status OBSERVATION_RECORDED")
    directory = root / "programs"
    if directory.exists() or (root / "program_results.json").exists():
        raise FileExistsError("program artifacts already exist; use a new record dir")
    directory.mkdir()
    started = time.monotonic()
    call = {
        "step": "programs",
        "started_at": _utc_now(),
        "allow_model_read": True,
        "status": "running",
    }
    try:
        # 多程序执行不消费 plan 的那一个 ``perception_request``,但仍然要求 record
        # 目录与自己的 plan 一致:graph/objects 未被替换、仍然 live-ready、embedded
        # stage 未漂移。这是 record 目录的身份检查,不是本步骤的输入。
        plan, _, config = _revalidate_record_plan(root)
        graph_ref = _required_string(plan.get("graph_ref"), "plan.graph_ref")
        objects_ref = _required_string(plan.get("objects_ref"), "plan.objects_ref")
        graph = _read_json(graph_ref)
        registry = _registry_objects(_read_json(objects_ref))

        program_ref = Path(perception_program_path).resolve()
        if not program_ref.is_file():
            raise FileNotFoundError(f"perception program does not exist: {program_ref}")
        document = _read_json(program_ref)
        violations = validate_perception_program(document, graph)
        if violations:
            raise ValueError(
                f"PerceptionProgram validation failed: {violations[:3]}"
            )

        impls = dict(GEOMETRY_IMPLEMENTATIONS if operator_impls is None
                     else operator_impls)
        missing = sorted(set(OPERATORS) - TRANSPORT_OPERATORS - set(impls))
        if missing:
            raise ValueError(f"geometry implementations are missing: {missing}")
        token = (
            qwen_token
            or os.environ.get("QWEN_AUTH_TOKEN")
            or os.environ.get("QWEN_API_KEY")
        )
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Qwen token is required outside plan.json")
        clients = source_module
        if clients is None:
            from ..perception import semantic_sources as clients
        scene = _frozen_scene(
            root,
            config,
            clients,
            impls,
            token,
            sam3_token or os.environ.get("SAM3_API_KEY"),
            (directory / "observation_input.jpg").resolve(),
        )
        call["service_urls"] = {
            "qwen": _config_string(config, "qwen_url"),
            "sam3": _config_string(config, "sam3_url"),
        }

        summaries: list[dict[str, Any]] = []
        holes: dict[str, Any] = {}
        ordered = sorted(
            enumerate(document["programs"]),
            key=lambda item: (item[1]["stage"], item[0]),
        )
        for offset, program in ordered:
            stage = _one_stage(graph, program["stage"])
            outcome = _execute_program(
                scene, directory, offset, program, stage, registry
            )
            summaries.append(outcome["summary"])
            holes.update(outcome["holes"])

        _write_json(root / "program_results.json", {
            "schema": _RESULTS_SCHEMA,
            "observation_id": scene.observation.observation_id,
            "perception_program_ref": str(program_ref),
            "graph_ref": graph_ref,
            "objects_ref": objects_ref,
            "identity_status": _IDENTITY_STATUS,
            "frame": scene.observation.frame,
            "calibration_ref": scene.observation.calibration_ref,
            "image_ref": scene.image_ref,
            "programs": summaries,
            "holes": holes,
        })
        call.update({"status": "ok", "duration_s": time.monotonic() - started,
                     "program_count": len(summaries)})
        _write_json(directory / "call.json", call)
        return _complete(
            root,
            manifest,
            status="PROGRAMS_RECORDED",
            artifacts=_PROGRAM_ARTIFACTS,
        )
    except Exception as error:
        _fail_stage(
            root,
            manifest,
            step="programs",
            directory=directory,
            call=call,
            started=started,
            error=error,
            artifacts=_PROGRAM_ARTIFACTS,
        )
        raise
