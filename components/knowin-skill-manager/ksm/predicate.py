from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

from .config import ManagerConfig
from .io import read_yaml, write_json

KW_PREDICATE_FALLBACK_NAMES = {
    "aligned",
    "all_of",
    "any_of",
    "arranged",
    "count",
    "goal_not_already_satisfied",
    "gripper_open",
    "inside",
    "inserted",
    "joint_above_ratio",
    "joint_below_ratio",
    "joint_closed",
    "joint_open",
    "lift",
    "near",
    "not",
    "not_on",
    "on",
    "orientation",
    "pose_close",
    "robot_home",
    "separate",
    "settled",
    "stacked",
    "upright",
}


@dataclass(frozen=True)
class PredicateEvaluation:
    schema: str
    status: str
    predicate_success: bool | None
    predicates: list[Any]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_task_predicates(
    *,
    config: ManagerConfig,
    task_path: str | Path | None,
    predicates: list[Any] | None,
    output_dir: str | Path,
) -> PredicateEvaluation:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    normalized = normalize_predicates(predicates)
    capabilities = kw_predicate_capabilities(config)
    task_payload: dict[str, Any] = {}
    if not normalized and task_path:
        try:
            loaded = read_yaml(task_path)
            task_payload = loaded if isinstance(loaded, dict) else {}
            normalized = predicates_from_task_payload(task_payload)
        except Exception as exc:
            result = PredicateEvaluation(
                schema="ksm.aspire_kw.predicate_evaluation.v1",
                status="error",
                predicate_success=False,
                predicates=[],
                evidence={"error": f"failed to read task payload: {exc!r}", "task_path": str(task_path)},
            )
            write_json(output / "predicate_report.json", result.to_dict())
            return result

    if not normalized:
        result = PredicateEvaluation(
            schema="ksm.aspire_kw.predicate_evaluation.v1",
            status="skipped",
            predicate_success=None,
            predicates=[],
            evidence={
                "reason": "no task predicates were provided",
                "kw_predicate_capabilities": capabilities,
            },
        )
        write_json(output / "predicate_report.json", result.to_dict())
        return result

    try:
        assets_payload = fetch_scene_assets(config)
    except Exception as exc:
        result = PredicateEvaluation(
            schema="ksm.aspire_kw.predicate_evaluation.v1",
            status="error",
            predicate_success=False,
            predicates=normalized,
            evidence={"error": f"failed to fetch WebUI scene assets: {exc!r}", "webui_base_url": webui_base_url(config)},
        )
        write_json(output / "predicate_report.json", result.to_dict())
        return result

    assets = assets_by_id(assets_payload)
    checks = [evaluate_predicate(predicate, assets=assets, config=config) for predicate in normalized]
    supported_checks = [check for check in checks if check.get("supported")]
    unsupported_checks = [check for check in checks if not check.get("supported")]
    any_supported_failed = any(not bool(check.get("success")) for check in supported_checks)
    if any_supported_failed:
        predicate_success: bool | None = False
        status = "failed"
    elif supported_checks and not unsupported_checks:
        predicate_success = True
        status = "ok"
    elif supported_checks:
        predicate_success = None
        status = "partial"
    else:
        predicate_success = None
        status = "unsupported"
    result = PredicateEvaluation(
        schema="ksm.aspire_kw.predicate_evaluation.v1",
        status=status,
        predicate_success=predicate_success,
        predicates=checks,
        evidence={
            "method": "kw_native_predicates_with_webui_scene_adapter",
            "fallback_method": "predicate_specific_webui_asset_geometry_fallbacks",
            "kw_predicate_capabilities": capabilities,
            "webui_base_url": webui_base_url(config),
            "asset_count": len(assets),
            "task_path": str(task_path) if task_path else None,
        },
    )
    write_json(output / "predicate_report.json", result.to_dict())
    return result


def normalize_predicates(predicates: Any) -> list[Any]:
    if not isinstance(predicates, list):
        return []
    normalized: list[Any] = []
    for item in predicates:
        if isinstance(item, dict):
            normalized.append(dict(item))
        elif isinstance(item, str) and item.strip():
            normalized.append(item.strip())
    return normalized


def predicates_from_task_payload(task_payload: dict[str, Any]) -> list[Any]:
    direct = normalize_predicates(task_payload.get("predicates") if isinstance(task_payload, dict) else None)
    if direct:
        return direct
    success = task_payload.get("success") if isinstance(task_payload, dict) else None
    if isinstance(success, list):
        return normalize_predicates(success)
    if isinstance(success, dict):
        for key in ("all_of", "predicates"):
            normalized = normalize_predicates(success.get(key))
            if normalized:
                return normalized
    robodojo = task_payload.get("robodojo") if isinstance(task_payload.get("robodojo"), dict) else {}
    robodojo_success = robodojo.get("success")
    if isinstance(robodojo_success, list):
        return normalize_predicates(robodojo_success)
    if isinstance(robodojo_success, dict):
        for key in ("all_of", "predicates"):
            normalized = normalize_predicates(robodojo_success.get(key))
            if normalized:
                return normalized
    return []


def evaluate_predicate(
    predicate: Any,
    *,
    assets: dict[str, dict[str, Any]],
    config: ManagerConfig,
) -> dict[str, Any]:
    predicate_type = predicate_type_name(predicate)
    capabilities = kw_predicate_capabilities(config)
    kw_registered = predicate_type in set(capabilities.get("names") or [])
    raw = dict(predicate) if isinstance(predicate, dict) else predicate
    native = evaluate_with_kw_native(predicate=predicate, assets=assets, config=config)
    if native.get("available"):
        return {
            "type": predicate_type,
            "supported": True,
            "success": bool(native.get("success")),
            "method": native.get("method"),
            "reason": "KW native predicate passed" if native.get("success") else "KW native predicate failed",
            "kw_registered": kw_registered,
            "native": native,
            "raw": raw,
        }

    if predicate_type != "inside":
        return {
            "type": predicate_type or "unknown",
            "supported": False,
            "success": False,
            "reason": (
                "KW predicate is registered but current KSM WebUI scene adapter cannot evaluate it"
                if kw_registered
                else "predicate type is not registered in known KW eval predicates"
            ),
            "kw_registered": kw_registered,
            "kw_registry_source": capabilities.get("source"),
            "native": native,
            "raw": raw,
        }
    predicate_dict = predicate_to_dict(predicate)
    source_id = str(predicate_dict.get("object") or predicate_dict.get("source") or predicate_dict.get("target") or "").strip()
    container_id = str(predicate_dict.get("container") or "").strip()
    source = assets.get(source_id)
    container = assets.get(container_id)
    if not source or not container:
        return {
            "type": "inside",
            "supported": True,
            "success": False,
            "object": source_id,
            "container": container_id,
            "reason": "missing source or container asset in WebUI scene assets",
            "kw_registered": kw_registered,
            "source_found": bool(source),
            "container_found": bool(container),
            "raw": raw,
        }

    inside = source_center_inside_container_aabb(source=source, container=container)
    return {
        "type": "inside",
        "supported": True,
        "success": bool(inside["success"]),
        "object": source_id,
        "container": container_id,
        "method": inside["method"],
        "reason": inside["reason"],
        "kw_registered": kw_registered,
        "source_center": inside["source_center"],
        "source_aabb": source.get("aabb"),
        "container_aabb": container.get("aabb"),
        "source_pose": source.get("current_pose"),
        "container_pose": container.get("current_pose"),
        "thresholds": inside["thresholds"],
        "native": native,
        "raw": raw,
    }


def evaluate_with_kw_native(
    *,
    predicate: Any,
    assets: dict[str, dict[str, Any]],
    config: ManagerConfig,
) -> dict[str, Any]:
    try:
        if str(config.kw_repo) not in sys.path:
            sys.path.insert(0, str(config.kw_repo))
        from sim.eval.evaluator import check_predicates
        from sim.eval.task_spec import parse_predicate_spec

        spec = parse_predicate_spec(predicate, "ksm.predicate")
        scene = WebUISceneManager(assets)
        report = check_predicates([spec], scene)
        label, detail = next(iter(report.details.items()))
        return {
            "available": True,
            "method": f"sim.eval.predicates.{predicate_type_name(predicate)}",
            "success": bool(report.success),
            "label": label,
            "detail": detail,
        }
    except Exception as exc:
        return {
            "available": False,
            "method": f"sim.eval.predicates.{predicate_type_name(predicate)}",
            "error": repr(exc),
        }


def evaluate_inside_with_kw_native(
    *,
    predicate: Any,
    assets: dict[str, dict[str, Any]],
    config: ManagerConfig,
) -> dict[str, Any]:
    return evaluate_with_kw_native(predicate=predicate, assets=assets, config=config)


def kw_predicate_capabilities(config: ManagerConfig) -> dict[str, Any]:
    try:
        if str(config.kw_repo) not in sys.path:
            sys.path.insert(0, str(config.kw_repo))
        from sim.eval.registry import PREDICATE_SCHEMAS

        return {
            "available": True,
            "source": "sim.eval.registry.PREDICATE_SCHEMAS",
            "names": sorted(str(name) for name in PREDICATE_SCHEMAS.keys()),
        }
    except Exception as exc:
        return {
            "available": False,
            "source": "ksm_fallback_kw_predicate_snapshot",
            "names": sorted(KW_PREDICATE_FALLBACK_NAMES),
            "error": repr(exc),
        }


def predicate_type_name(predicate: Any) -> str:
    if isinstance(predicate, dict):
        raw = predicate.get("type")
        if raw is None and len(predicate) == 1:
            raw = next(iter(predicate.keys()))
        return str(raw or "").strip()
    if isinstance(predicate, str):
        text = predicate.strip()
        return text.split("(", 1)[0].strip() if "(" in text else text
    return ""


def predicate_to_dict(predicate: Any) -> dict[str, Any]:
    if isinstance(predicate, dict):
        return dict(predicate)
    if not isinstance(predicate, str):
        return {}
    text = predicate.strip()
    if "(" not in text or not text.endswith(")"):
        return {"type": text}
    name, rest = text.split("(", 1)
    args = [item.strip() for item in rest[:-1].split(",") if item.strip()]
    payload: dict[str, Any] = {"type": name.strip()}
    if payload["type"] == "inside" and len(args) >= 2:
        payload["object"] = args[0]
        payload["container"] = args[1]
    elif payload["type"] in {"on", "near", "separate", "inserted", "stacked"} and len(args) >= 2:
        payload["object"] = args[0]
        payload["target"] = args[1]
    elif args:
        payload["object"] = args[0]
        if len(args) > 1:
            payload["args"] = args
    return payload


def source_center_inside_container_aabb(*, source: dict[str, Any], container: dict[str, Any]) -> dict[str, Any]:
    source_aabb = source.get("aabb") if isinstance(source.get("aabb"), dict) else {}
    container_aabb = container.get("aabb") if isinstance(container.get("aabb"), dict) else {}
    source_min = _vec3(source_aabb.get("min"))
    source_max = _vec3(source_aabb.get("max"))
    container_min = _vec3(container_aabb.get("min"))
    container_max = _vec3(container_aabb.get("max"))
    if None in (source_min, source_max, container_min, container_max):
        return {
            "success": False,
            "method": "aabb_center",
            "reason": "missing source or container AABB",
            "source_center": None,
            "thresholds": {},
        }
    center = [(source_min[i] + source_max[i]) / 2.0 for i in range(3)]
    xy_margin = 0.02
    z_tolerance = 0.03
    inside_x = container_min[0] + xy_margin <= center[0] <= container_max[0] - xy_margin
    inside_y = container_min[1] + xy_margin <= center[1] <= container_max[1] - xy_margin
    inside_z = container_min[2] <= center[2] <= container_max[2] + z_tolerance
    success = bool(inside_x and inside_y and inside_z)
    return {
        "success": success,
        "method": "source_aabb_center_within_container_aabb",
        "reason": "source center is inside container AABB" if success else "source center is outside container AABB",
        "source_center": center,
        "thresholds": {
            "xy_margin": xy_margin,
            "z_tolerance": z_tolerance,
            "inside_x": inside_x,
            "inside_y": inside_y,
            "inside_z": inside_z,
        },
    }


def fetch_scene_assets(config: ManagerConfig) -> dict[str, Any]:
    url = f"{webui_base_url(config)}/api/list_scene_assets"
    with urlopen(url, timeout=8.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise ValueError(f"unexpected scene assets payload from {url}: {payload!r}")
    return payload


def assets_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("id"):
            result[str(asset["id"])] = asset
    return result


class WebUIEntity:
    def __init__(self, asset: dict[str, Any]) -> None:
        self.asset = asset

    def get_AABB(self) -> list[list[float]]:  # noqa: N802 - mirrors Genesis API
        aabb = self.asset.get("aabb") if isinstance(self.asset.get("aabb"), dict) else {}
        lower = _vec3(aabb.get("min"))
        upper = _vec3(aabb.get("max"))
        if lower is None or upper is None:
            raise ValueError(f"asset {self.asset.get('id')!r} has no valid AABB")
        return [lower, upper]

    def get_pos(self) -> list[float]:
        pose = self.asset.get("current_pose") if isinstance(self.asset.get("current_pose"), dict) else {}
        pos = _vec3(pose.get("position"))
        if pos is not None:
            return pos
        aabb = self.get_AABB()
        return [(aabb[0][i] + aabb[1][i]) / 2.0 for i in range(3)]

    def get_quat(self) -> list[float]:
        pose = self.asset.get("current_pose") if isinstance(self.asset.get("current_pose"), dict) else {}
        quat = pose.get("orientation_wxyz")
        if isinstance(quat, list | tuple) and len(quat) >= 4:
            return [float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3])]
        return [1.0, 0.0, 0.0, 0.0]

    def get_vel(self) -> list[float]:
        return [0.0, 0.0, 0.0]


class WebUISceneManager:
    def __init__(self, assets: dict[str, dict[str, Any]]) -> None:
        self.assets = assets

    def get_entity(self, entity_id: str) -> WebUIEntity:
        try:
            return WebUIEntity(self.assets[entity_id])
        except KeyError as exc:
            known = ", ".join(sorted(self.assets)) or "<none>"
            raise KeyError(f"unknown WebUI asset {entity_id!r}; known assets: [{known}]") from exc


def webui_base_url(config: ManagerConfig) -> str:
    explicit = os.environ.get("KSM_WEBUI_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    parsed = urlparse(config.pipeline.base_url)
    netloc = parsed.netloc
    if ":" in netloc:
        host = netloc.rsplit(":", 1)[0]
        netloc = f"{host}:8080"
    else:
        netloc = f"{netloc}:8080"
    return urlunparse((parsed.scheme or "http", netloc, "", "", "", "")).rstrip("/")


def _vec3(value: Any) -> list[float] | None:
    if not isinstance(value, list | tuple) or len(value) < 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None
