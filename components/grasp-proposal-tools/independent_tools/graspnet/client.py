from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib import error, request
import json

from .contract import GRASPNET_REQUEST_SCHEMA, GraspNetConfig, write_json
from .normalizer import normalize_grasp_response


def call_grasp_service(
    *,
    request_payload: dict[str, Any],
    config: GraspNetConfig | dict[str, Any],
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Call a configured external grasp service and normalize the JSON result."""

    cfg = config if isinstance(config, GraspNetConfig) else GraspNetConfig.from_dict(config)
    service_payload = _service_payload(request_payload)
    body = json.dumps(service_payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        cfg.resolved_url(),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    raw_response_path = None
    try:
        with request.urlopen(req, timeout=cfg.timeout_s) as resp:
            text = resp.read().decode("utf-8")
            status = getattr(resp, "status", None)
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {
            "schema": "kw_independent.graspnet.call_result.v1",
            "ok": False,
            "status": exc.code,
            "error": f"HTTPError: {exc.reason}",
            "body": text,
            "source_policy": "transport_error_not_grasp_quality_evidence",
        }
    except OSError as exc:
        return {
            "schema": "kw_independent.graspnet.call_result.v1",
            "ok": False,
            "status": None,
            "error": repr(exc),
            "source_policy": "transport_error_not_grasp_quality_evidence",
        }

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "schema": "kw_independent.graspnet.call_result.v1",
            "ok": False,
            "status": status,
            "error": f"non_json_response: {exc}",
            "body": text[:4000],
            "source_policy": "service_contract_error_not_grasp_quality_evidence",
        }

    if output_dir is not None:
        root = Path(output_dir)
        write_json(root / "request.json", request_payload)
        write_json(root / "service_request.json", service_payload)
        raw_response_path = str(write_json(root / "raw_response.json", raw))

    normalized = normalize_grasp_response(
        raw,
        config=cfg,
        input_reference=request_payload.get("inputs") if isinstance(request_payload, dict) else {},
        raw_response_path=raw_response_path,
        source={"service_url": cfg.service_url, "endpoint_path": cfg.endpoint_path, "http_status": status},
    )
    if output_dir is not None:
        write_json(Path(output_dir) / "normalized_proposals.json", normalized)
    return {
        "schema": "kw_independent.graspnet.call_result.v1",
        "ok": True,
        "status": status,
        "normalized": normalized,
    }


def _service_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    if (
        isinstance(request_payload, dict)
        and request_payload.get("schema") == GRASPNET_REQUEST_SCHEMA
        and isinstance(request_payload.get("inputs"), dict)
    ):
        return dict(request_payload["inputs"])
    return request_payload
