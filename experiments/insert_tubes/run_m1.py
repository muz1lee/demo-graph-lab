"""Minimal non-privileged M1 runner for the live Knowin World pipeline.

The useful path today is ``--mode grasp``.  ``--mode full`` runs a read-only
preflight first and refuses to move when either the observed tube axis or the
observed empty-opening pose is unavailable.  It never reads scene assets,
simulator poses, evaluator state, fixed slot coordinates, or task predicates.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters import BrokerPolicyBindings, EvidenceRef, MethodBroker, MethodResult
from adapters.method_broker import MethodSpec
from method.demo_graph import (
    ConstraintGraph,
    ControllerResult,
    Node,
    Observation,
)


class PipelineError(RuntimeError):
    pass


def _wire_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(stripped)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            pass
    return value


def _vector(value: Any) -> list[float]:
    value = _wire_value(value)
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    if isinstance(value, str):
        cleaned = value.replace("[", " ").replace("]", " ").replace(",", " ")
        return [float(item) for item in cleaned.split()]
    raise PipelineError(f"expected numeric vector, got {type(value).__name__}")


def _distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _axis_vertical(axis: list[float], tolerance_deg: float) -> bool:
    norm = math.sqrt(sum(float(item) ** 2 for item in axis))
    if norm <= 1e-9:
        return False
    cosine = abs(float(axis[2])) / norm
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    return tilt <= tolerance_deg


class PipelineClient:
    """Tiny client for the already-running pipeline ``/run`` endpoint."""

    def __init__(self, base_url: str, timeout_s: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def call(self, action: str, name: str, kwargs: Mapping[str, Any]) -> Any:
        query = urllib.parse.urlencode(
            {"action": action, "name": name, "kwargs": json.dumps(dict(kwargs))}
        )
        request = urllib.request.Request(f"{self.base_url}/run?{query}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read())
        except Exception as exc:
            raise PipelineError(f"{action}:{name} transport failed: {exc}") from exc
        if payload.get("ok") is not True:
            raise PipelineError(f"{action}:{name} failed: {payload.get('error')}")
        return _wire_value(payload.get("result"))

    def reasoning(self, name: str, **kwargs: Any) -> Mapping[str, Any]:
        value = self.call("reasoning", name, kwargs)
        if not isinstance(value, Mapping):
            raise PipelineError(f"reasoning:{name} returned {type(value).__name__}")
        return value

    def info(self, name: str, **kwargs: Any) -> Any:
        return self.call("info", name, kwargs)

    def ctrl(self, name: str, **kwargs: Any) -> None:
        if self.call("ctrl", name, kwargs) is not True:
            raise PipelineError(f"ctrl:{name} was not accepted")


class M1Runtime:
    """Task-thin use of existing qwen perception and robot primitives."""

    def __init__(
        self,
        pipeline: PipelineClient,
        *,
        arm_id: int,
        pick_prompt: str,
        place_prompt: str,
        lift_m: float = 0.08,
        lift_evidence_m: float = 0.03,
        max_track_xy_m: float = 0.05,
        upright_tolerance_deg: float = 15.0,
        settle_poll_s: float = 0.25,
        settle_samples: int = 3,
    ) -> None:
        self.pipeline = pipeline
        self.arm_id = int(arm_id)
        self.pick_prompt = pick_prompt
        self.place_prompt = place_prompt
        self.lift_m = float(lift_m)
        self.lift_evidence_m = float(lift_evidence_m)
        self.max_track_xy_m = float(max_track_xy_m)
        self.upright_tolerance_deg = float(upright_tolerance_deg)
        self.settle_poll_s = float(settle_poll_s)
        self.settle_samples = int(settle_samples)
        self.initial_pick_xyz: list[float] | None = None
        self.pick: dict[str, Any] | None = None
        self.place: dict[str, Any] | None = None
        self.revision = 0

    def _qwen_pick(self) -> dict[str, Any] | None:
        raw = self.pipeline.reasoning(
            "qwen_dof_xquat",
            text=[self.pick_prompt],
            offsets=[0.0, 0.0, 0.0],
            arm_id=self.arm_id,
            pick_per_arm_topk=3,
        )
        arms = raw.get("xquats")
        if not isinstance(arms, (list, tuple)) or len(arms) <= self.arm_id:
            return None
        rows = arms[self.arm_id]
        if not isinstance(rows, (list, tuple)) or not rows or rows[0] is None:
            return None
        pose = _vector(rows[0])[:7]
        if len(pose) != 7:
            return None
        angles = raw.get("grasp_angles")
        try:
            grasp_angle = float(angles[self.arm_id][0])
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        run_id = ""
        results = raw.get("results")
        if isinstance(results, list) and results and isinstance(results[0], Mapping):
            run_id = str(results[0].get("run_id") or "")
        axis = None
        for key in ("object_axis_world", "long_axis_world", "tube_axis_world"):
            candidate = raw.get(key)
            if isinstance(candidate, (list, tuple)) and len(candidate) == 3:
                axis = _vector(candidate)
                break
        self.pick = {
            "pose": pose,
            "grasp_angle": grasp_angle,
            "run_id": run_id,
            "axis": axis,
        }
        if self.initial_pick_xyz is None:
            self.initial_pick_xyz = pose[:3]
        return self.pick

    def _qwen_place(self) -> dict[str, Any] | None:
        if self.pick is None or not self.pick["run_id"]:
            return None
        raw = self.pipeline.reasoning(
            "qwen_dof_xquat_place",
            text=[self.place_prompt],
            data=self.pick["pose"],
            offsets=[0.0, 0.0, 0.0],
            arm_id=self.arm_id,
            run_id=self.pick["run_id"],
        )
        arms = raw.get("xquats")
        if not isinstance(arms, (list, tuple)) or len(arms) <= self.arm_id:
            return None
        rows = arms[self.arm_id]
        if not isinstance(rows, (list, tuple)) or not rows or rows[0] is None:
            return None
        pose = _vector(rows[0])[:7]
        if len(pose) != 7:
            return None
        self.place = {"pose": pose}
        return self.place

    def probe(self) -> dict[str, Any]:
        pick = self._qwen_pick()
        place = self._qwen_place() if pick is not None else None
        holes = []
        if pick is None:
            holes.append("grasp_pose")
        elif pick["axis"] is None:
            holes.append("tube_axis")
        if place is None:
            holes.append("holder_pose")
        return {
            "grasp_candidate_found": pick is not None,
            "tube_axis_found": bool(pick and pick["axis"] is not None),
            "holder_pose_found": place is not None,
            "perceptual_holes": holes,
        }

    def observe(self, node_id: str) -> dict[str, Any]:
        self.revision += 1
        pick = self._qwen_pick()
        payload: dict[str, Any] = {
            "revision": f"m1-{self.revision}",
            "tube_attached": False,
            "tube_upright": False,
            "tube_aligned": False,
            "tube_inserted": False,
            "task_verified": False,
            "perceptual_holes": [],
        }
        if pick is None:
            payload["perceptual_holes"] = ["grasp_pose"]
            return payload
        payload["pick_pose"] = pick["pose"]
        if self.initial_pick_xyz is not None:
            delta = [
                pick["pose"][index] - self.initial_pick_xyz[index] for index in range(3)
            ]
            payload["observed_pick_delta"] = delta
            payload["tube_attached"] = (
                delta[2] >= self.lift_evidence_m
                and math.hypot(delta[0], delta[1]) <= self.max_track_xy_m
            )
        axis = pick["axis"]
        if axis is None:
            payload["perceptual_holes"].append("tube_axis")
        else:
            payload["tube_axis"] = axis
            payload["tube_upright"] = _axis_vertical(
                axis, self.upright_tolerance_deg
            )
        if node_id in {"align", "insert", "verify"}:
            place = self._qwen_place()
            if place is None:
                payload["perceptual_holes"].append("holder_pose")
            else:
                payload["holder_pose"] = place["pose"]
        return payload

    def _wait_arm(self, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_s
        previous: list[float] | None = None
        stable = 0
        time.sleep(self.settle_poll_s)
        while time.monotonic() < deadline:
            current = _vector(self.pipeline.info("get_qpos", arm_id=self.arm_id))
            if previous is not None and _distance(current, previous) < 1.5e-3:
                stable += 1
                if stable >= self.settle_samples:
                    return
            else:
                stable = 0
            previous = current
            time.sleep(self.settle_poll_s)
        raise PipelineError("robot did not settle before timeout")

    def pick_controller(self, node: Node, observation: Observation) -> ControllerResult:
        del observation
        if self.pick is None:
            return ControllerResult(
                ok=False,
                reason="perceptual hole unresolved: qwen_dof_xquat returned no grasp pose",
                constraint_id="grasp_pose",
            )
        pose = self.pick["pose"]
        grasp_angle = float(self.pick["grasp_angle"])
        open_angle = min(85.0, grasp_angle + 15.0)
        try:
            self.pipeline.ctrl(
                "set_gripper", arm_id=self.arm_id, angle=open_angle, check=True
            )
            self.pipeline.ctrl(
                "xquat_move",
                arm_id=self.arm_id,
                target_xyz=pose[:3],
                target_quat=pose[3:7],
                interpolation="z_arc",
                gpos=[open_angle],
            )
            self._wait_arm()
            self.pipeline.ctrl(
                "set_gripper", arm_id=self.arm_id, angle=0.0, check=True
            )
            self.pipeline.ctrl(
                "delta_move",
                arm_id=self.arm_id,
                delta_xyz=[0.0, 0.0, self.lift_m],
                gpos=[0.0],
            )
            self._wait_arm()
        except PipelineError as exc:
            return ControllerResult(
                ok=False,
                reason=str(exc),
                constraint_id=node.constraints[0].constraint_id,
            )
        return ControllerResult(ok=True)

    @staticmethod
    def unresolved_controller(
        node: Node, observation: Observation
    ) -> ControllerResult:
        holes = observation.payload.get("perceptual_holes", ())
        hole = str(holes[0]) if holes else node.constraints[0].constraint_id
        return ControllerResult(
            ok=False,
            reason=f"perceptual hole unresolved: {hole}; no ground-truth fallback",
            constraint_id=hole if hole in node.attributable_ids else node.constraints[0].constraint_id,
        )


def _evidence(value: Mapping[str, Any], revision: str) -> EvidenceRef:
    return EvidenceRef.from_value(
        evidence_id=f"m1:{revision}",
        source="runtime_perception",
        value=value,
        observation_revision=revision,
    )


def build_policy(runtime: M1Runtime, graph: ConstraintGraph):
    latest: dict[str, Mapping[str, Any]] = {}

    def observe_handler(params: Mapping[str, Any]) -> MethodResult:
        payload = runtime.observe(str(params["node_id"]))
        revision = str(payload.pop("revision"))
        latest[revision] = payload
        value = {"revision": revision, "payload": payload}
        return MethodResult(value=value, evidence=(_evidence(payload, revision),))

    def goal_handler(params: Mapping[str, Any]) -> MethodResult:
        revision = str(params["observation_revision"])
        value = latest[revision]
        goal = str(params["goal"])
        result = {"satisfied": value.get(goal) is True}
        return MethodResult(value=result, evidence=(_evidence(value, revision),))

    broker = MethodBroker(
        [
            MethodSpec("perception.observe", observe_handler),
            MethodSpec("verification.goal_satisfied", goal_handler),
        ]
    )
    bindings = BrokerPolicyBindings(broker)
    controllers = {
        "trusted.pick": runtime.pick_controller,
        "trusted.reorient": runtime.unresolved_controller,
        "trusted.align": runtime.unresolved_controller,
        "trusted.insert": runtime.unresolved_controller,
        "trusted.verify": runtime.unresolved_controller,
    }
    return bindings.build_policy(graph, controllers), broker


def _grasp_only_graph(graph: ConstraintGraph) -> ConstraintGraph:
    pick = graph.node("pick")
    pick = type(pick)(
        node_id=pick.node_id,
        action=pick.action,
        goal=pick.goal,
        controller_ref=pick.controller_ref,
        constraints=pick.constraints,
        provenance=pick.provenance,
        holes=pick.holes,
        max_attempts=pick.max_attempts,
        next_node=None,
    )
    return ConstraintGraph(
        graph_id=f"{graph.graph_id}_grasp_only",
        entry_node="pick",
        nodes=(pick,),
        provenance=graph.provenance,
        schema_version=graph.schema_version,
    )


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("probe", "grasp", "full"), default="probe")
    parser.add_argument("--pipeline-url", default="http://127.0.0.1:8000")
    parser.add_argument("--arm-id", type=int, default=0)
    parser.add_argument(
        "--pick-prompt", default="a test tube lying on the table:dof"
    )
    parser.add_argument(
        "--place-prompt", default="an empty circular hole in the test tube rack"
    )
    parser.add_argument("--lift-m", type=float, default=0.08)
    args = parser.parse_args(argv)

    graph = ConstraintGraph.load_json(Path(__file__).with_name("m1_graph.json"))
    runtime = M1Runtime(
        PipelineClient(args.pipeline_url),
        arm_id=args.arm_id,
        pick_prompt=args.pick_prompt,
        place_prompt=args.place_prompt,
        lift_m=args.lift_m,
    )
    if args.mode == "probe":
        result = runtime.probe()
        _json_print(result)
        return 0 if not result["perceptual_holes"] else 2
    if args.mode == "full":
        preflight = runtime.probe()
        if preflight["perceptual_holes"]:
            _json_print(
                {
                    "succeeded": False,
                    "reason": "full M1 preflight failed closed",
                    **preflight,
                }
            )
            return 2
    if args.mode == "grasp":
        graph = _grasp_only_graph(graph)
    policy, broker = build_policy(runtime, graph)
    result = policy.run()
    _json_print(
        {
            "result": asdict(result),
            "method_calls": [asdict(record) for record in broker.audit_records],
        }
    )
    return 0 if result.succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
