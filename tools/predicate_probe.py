"""Oracle-state predicate probe.

Places tubes into the rack in a visually correct "inserted upright" state, then
evaluates the task's success predicates with the real KW evaluator. Purpose: find
out whether the predicates actually recognise a correct goal state before we ask
any policy to reach it.

Usage:
  python predicate_probe.py --repo <kw repo> --url http://127.0.0.1:5150 [--place]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

# Rotation that maps the asset's local +x onto world +z (tube long axis vertical).
QUAT_LOCAL_X_UP = (0.70710678, 0.0, -0.70710678, 0.0)
# Rotation that maps local +y onto world +z, for comparison.
QUAT_LOCAL_Y_UP = (0.70710678, 0.70710678, 0.0, 0.0)


def get_json(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def post_json(url: str, payload: dict, timeout: float = 10.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class SceneEntity:
    def __init__(self, asset: dict) -> None:
        self._asset = asset

    def get_AABB(self):  # noqa: N802 - mirrors Genesis API
        bb = self._asset.get("aabb") or {}
        return np.array([bb.get("min"), bb.get("max")], dtype=float)

    def get_pos(self):
        return np.array((self._asset.get("current_pose") or {}).get("position"), dtype=float)

    def get_quat(self):
        return np.array((self._asset.get("current_pose") or {}).get("orientation_wxyz"), dtype=float)

    def get_vel(self):
        return np.zeros(3)


class SceneAdapter:
    def __init__(self, assets: dict[str, dict]) -> None:
        self._assets = assets

    def get_entity(self, entity_id: str) -> SceneEntity:
        if entity_id not in self._assets:
            raise KeyError(f"unknown entity {entity_id!r}")
        return SceneEntity(self._assets[entity_id])


def fetch_assets(base_url: str) -> dict[str, dict]:
    payload = get_json(f"{base_url}/api/list_scene_assets")
    return {a["id"]: a for a in payload.get("assets", []) if a.get("id")}


def describe(assets: dict[str, dict], ids: list[str]) -> None:
    for name in ids:
        a = assets.get(name)
        if not a:
            print(f"  {name}: MISSING")
            continue
        bb = a.get("aabb") or {}
        mn, mx = bb.get("min"), bb.get("max")
        pose = a.get("current_pose") or {}
        spans = [mx[i] - mn[i] for i in range(3)] if mn and mx else None
        print(
            f"  {name:12s} pos={[round(v, 3) for v in pose.get('position', [])]} "
            f"span=[{spans[0]:.3f},{spans[1]:.3f},{spans[2]:.3f}]" if spans else f"  {name}: no aabb"
        )


def axis_angle_to_world_z(quat, local_axis: str) -> float:
    w, x, y, z = quat
    R = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )
    idx = {"x": 0, "y": 1, "z": 2}[local_axis]
    v = R[:, idx]
    return math.degrees(math.acos(max(-1.0, min(1.0, float(v[2])))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:5150")
    ap.add_argument("--place", action="store_true", help="teleport tubes into rack slots first")
    ap.add_argument("--quat", choices=["x_up", "y_up", "keep"], default="x_up")
    ap.add_argument("--z", type=float, default=0.80, help="tube center height when placed")
    args = ap.parse_args()

    sys.path.insert(0, args.repo)
    from sim.eval.evaluator import check_predicates
    from sim.eval.task_spec import parse_predicate_spec

    assets = fetch_assets(args.url)
    rack = assets["slot_prop"]
    rack_bb = rack["aabb"]
    rack_pos = rack["current_pose"]["position"]

    print("== initial state ==")
    describe(assets, ["tube0_prop", "tube1_prop", "tube2_prop", "slot_prop"])
    for t in ["tube0_prop"]:
        q = assets[t]["current_pose"]["orientation_wxyz"]
        print(
            f"  {t} angle_to_world_z: local_x={axis_angle_to_world_z(q, 'x'):.1f} "
            f"local_y={axis_angle_to_world_z(q, 'y'):.1f} local_z={axis_angle_to_world_z(q, 'z'):.1f}"
        )

    if args.place:
        quat = {"x_up": QUAT_LOCAL_X_UP, "y_up": QUAT_LOCAL_Y_UP}.get(args.quat)
        # Distribute the three tubes across the rack's y footprint (slots run along y).
        y_lo, y_hi = rack_bb["min"][1], rack_bb["max"][1]
        ys = [y_lo + (y_hi - y_lo) * f for f in (0.25, 0.5, 0.75)]
        for tube, y in zip(["tube0_prop", "tube1_prop", "tube2_prop"], ys):
            payload = {
                "id": tube,
                "position": [rack_pos[0], y, args.z],
                "orientation_wxyz": list(quat) if quat else None,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            out = post_json(f"{args.url}/api/set_asset_pose", payload)
            print(f"  placed {tube} -> {out.get('ok')}")
        time.sleep(2.0)
        assets = fetch_assets(args.url)
        print("== after placement ==")
        describe(assets, ["tube0_prop", "tube1_prop", "tube2_prop"])
        q = assets["tube0_prop"]["current_pose"]["orientation_wxyz"]
        print(
            f"  tube0 angle_to_world_z: local_x={axis_angle_to_world_z(q, 'x'):.1f} "
            f"local_y={axis_angle_to_world_z(q, 'y'):.1f} local_z={axis_angle_to_world_z(q, 'z'):.1f}"
        )

    scene = SceneAdapter(assets)
    checks = [
        {"type": "inserted", "object": "tube0_prop", "container": "slot_prop"},
        {"type": "upright", "object": "tube0_prop"},
        {"type": "orientation", "object": "tube0_prop", "axis": "+x", "target_axis": "+z"},
        {"type": "orientation", "object": "tube0_prop", "axis": "+y", "target_axis": "+z"},
    ]
    print("== predicate evaluation ==")
    for raw in checks:
        try:
            spec = parse_predicate_spec(raw, "probe")
            report = check_predicates([spec], scene)
            label, detail = next(iter(report.details.items()))
            print(f"  {label:52s} passed={report.success} detail={detail}")
        except Exception as exc:  # noqa: BLE001 - probe should report, not crash
            print(f"  {raw} -> ERROR {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
