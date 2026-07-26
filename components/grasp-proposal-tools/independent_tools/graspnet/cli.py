from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json

from .client import call_grasp_service
from .contract import build_request, load_config, load_json, write_json
from .normalizer import normalize_grasp_response
from .pointcloud import mask_pointcloud, rgbd_to_pointcloud, run_real_frame_probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone GraspNet proposal tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build-request", help="Build a serializable grasp proposal request")
    build.add_argument("--image")
    build.add_argument("--depth")
    build.add_argument("--point-cloud")
    build.add_argument("--mask")
    build.add_argument("--object-hint")
    build.add_argument("--frame-id")
    build.add_argument("--coordinate-frame")
    build.add_argument("--output", required=True)

    norm = sub.add_parser("normalize", help="Normalize a raw GraspNet/AnyGrasp JSON response")
    norm.add_argument("--raw-response", required=True)
    norm.add_argument("--config")
    norm.add_argument("--input-reference")
    norm.add_argument("--output", required=True)

    call = sub.add_parser("call", help="Call a configured grasp service and normalize the response")
    call.add_argument("--request", required=True)
    call.add_argument("--config", required=True)
    call.add_argument("--output-dir", required=True)

    rgbd = sub.add_parser("rgbd-to-pointcloud", help="Project RGB-D/depth evidence into a point cloud")
    rgbd.add_argument("--depth", required=True)
    rgbd.add_argument("--intrinsics", required=True)
    rgbd.add_argument("--output", required=True)
    rgbd.add_argument("--image")
    rgbd.add_argument("--mask")
    rgbd.add_argument("--depth-scale", type=float, default=0.001)
    rgbd.add_argument("--min-depth-m", type=float)
    rgbd.add_argument("--max-depth-m", type=float)
    rgbd.add_argument("--coordinate-frame")
    rgbd.add_argument("--max-points", type=int)
    rgbd.add_argument("--sample-seed", type=int, default=0)
    rgbd.add_argument("--manifest")

    mask_pc = sub.add_parser("mask-pointcloud", help="Apply a 2D mask to a point cloud with pixel_xy provenance")
    mask_pc.add_argument("--point-cloud", required=True)
    mask_pc.add_argument("--mask", required=True)
    mask_pc.add_argument("--output", required=True)
    mask_pc.add_argument("--manifest")

    probe = sub.add_parser("real-frame-probe", help="Convert one RGB-D/depth frame and query a grasp service")
    probe.add_argument("--depth", required=True)
    probe.add_argument("--intrinsics", required=True)
    probe.add_argument("--output-dir", required=True)
    probe.add_argument("--config")
    probe.add_argument("--service-url")
    probe.add_argument("--endpoint-path", default="/predict")
    probe.add_argument("--timeout-s", type=float, default=30.0)
    probe.add_argument("--image")
    probe.add_argument("--mask")
    probe.add_argument("--object-hint")
    probe.add_argument("--frame-id")
    probe.add_argument("--coordinate-frame")
    probe.add_argument("--depth-scale", type=float, default=0.001)
    probe.add_argument("--min-depth-m", type=float)
    probe.add_argument("--max-depth-m", type=float)
    probe.add_argument("--max-points", type=int, default=20000)
    probe.add_argument("--sample-seed", type=int, default=0)
    probe.add_argument("--max-grasps", type=int)

    args = parser.parse_args(argv)
    if args.cmd == "build-request":
        payload = build_request(
            image_path=args.image,
            depth_path=args.depth,
            point_cloud_path=args.point_cloud,
            mask_path=args.mask,
            object_hint=args.object_hint,
            frame_id=args.frame_id,
            coordinate_frame=args.coordinate_frame,
            evidence_source={"created_by": "independent_tools.graspnet.cli"},
        )
        write_json(args.output, payload)
        print(json.dumps({"ok": True, "output": str(Path(args.output))}, ensure_ascii=False))
        return 0

    if args.cmd == "normalize":
        cfg = load_config(args.config)
        raw = load_json(args.raw_response)
        input_reference: dict[str, Any] = {}
        if args.input_reference:
            loaded = load_json(args.input_reference)
            input_reference = loaded if isinstance(loaded, dict) else {"value": loaded}
        payload = normalize_grasp_response(
            raw,
            config=cfg,
            input_reference=input_reference,
            raw_response_path=str(Path(args.raw_response)),
            source={"normalizer": "independent_tools.graspnet.cli"},
        )
        write_json(args.output, payload)
        print(json.dumps({"ok": True, "output": str(Path(args.output)), "num_proposals": payload["num_proposals"]}, ensure_ascii=False))
        return 0

    if args.cmd == "call":
        cfg = load_config(args.config)
        payload = call_grasp_service(
            request_payload=load_json(args.request),
            config=cfg,
            output_dir=args.output_dir,
        )
        write_json(Path(args.output_dir) / "call_result.json", payload)
        print(json.dumps({"ok": bool(payload.get("ok")), "output_dir": str(Path(args.output_dir))}, ensure_ascii=False))
        return 0 if payload.get("ok") else 2

    if args.cmd == "rgbd-to-pointcloud":
        manifest = rgbd_to_pointcloud(
            depth_path=args.depth,
            intrinsics=args.intrinsics,
            output_path=args.output,
            image_path=args.image,
            mask_path=args.mask,
            depth_scale=args.depth_scale,
            min_depth_m=args.min_depth_m,
            max_depth_m=args.max_depth_m,
            coordinate_frame=args.coordinate_frame,
            max_points=args.max_points,
            sample_seed=args.sample_seed,
            manifest_path=args.manifest,
            evidence_source={"created_by": "independent_tools.graspnet.cli"},
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(Path(args.output)),
                    "num_output_points": manifest["stats"]["num_output_points"],
                    "manifest": args.manifest,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.cmd == "mask-pointcloud":
        manifest = mask_pointcloud(
            point_cloud_path=args.point_cloud,
            mask_path=args.mask,
            output_path=args.output,
            manifest_path=args.manifest,
            evidence_source={"created_by": "independent_tools.graspnet.cli"},
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(Path(args.output)),
                    "num_output_points": manifest["stats"]["num_output_points"],
                    "manifest": args.manifest,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.cmd == "real-frame-probe":
        cfg = load_config(args.config) if args.config else load_config(None)
        if args.service_url:
            cfg.service_url = args.service_url
        cfg.endpoint_path = args.endpoint_path
        cfg.timeout_s = args.timeout_s
        payload = run_real_frame_probe(
            depth_path=args.depth,
            intrinsics=args.intrinsics,
            service_config=cfg,
            output_dir=args.output_dir,
            image_path=args.image,
            mask_path=args.mask,
            depth_scale=args.depth_scale,
            min_depth_m=args.min_depth_m,
            max_depth_m=args.max_depth_m,
            coordinate_frame=args.coordinate_frame,
            max_points=args.max_points,
            sample_seed=args.sample_seed,
            object_hint=args.object_hint,
            frame_id=args.frame_id,
            max_grasps=args.max_grasps,
        )
        normalized = payload.get("call_result", {}).get("normalized", {})
        print(
            json.dumps(
                {
                    "ok": bool(payload.get("ok")),
                    "output_dir": str(Path(args.output_dir)),
                    "num_proposals": normalized.get("num_proposals"),
                    "probe_result": str(Path(args.output_dir) / "probe_result.json"),
                },
                ensure_ascii=False,
            )
        )
        return 0 if payload.get("ok") else 2

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
