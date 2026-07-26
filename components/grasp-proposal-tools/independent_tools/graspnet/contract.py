from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json


GRASPNET_REQUEST_SCHEMA = "kw_independent.graspnet.request.v1"
GRASPNET_PROPOSALS_SCHEMA = "kw_independent.graspnet.proposals.v1"


@dataclass
class GraspNetConfig:
    """Configuration for an external GraspNet/AnyGrasp-style service.

    The config describes transport and normalization only. It does not decide
    which grasp a downstream agent should execute.
    """

    service_url: str | None = None
    endpoint_path: str = "/predict"
    timeout_s: float = 30.0
    request_format: str = "json_paths"
    coordinate_frame: str | None = None
    max_candidates: int | None = 20
    preserve_raw: bool = True
    preserve_order: bool = True
    field_map: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "GraspNetConfig":
        payload = payload if isinstance(payload, dict) else {}
        known = {name for name in cls.__dataclass_fields__}
        values = {key: payload[key] for key in known if key in payload}
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolved_url(self) -> str:
        if not self.service_url:
            raise ValueError("GraspNetConfig.service_url is required for HTTP calls")
        base = self.service_url.rstrip("/")
        path = self.endpoint_path if self.endpoint_path.startswith("/") else f"/{self.endpoint_path}"
        return f"{base}{path}"


def load_config(path: str | Path | None = None) -> GraspNetConfig:
    if path is None:
        return GraspNetConfig()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GraspNet config must be a JSON object")
    return GraspNetConfig.from_dict(data)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def build_request(
    *,
    image_path: str | None = None,
    depth_path: str | None = None,
    point_cloud_path: str | None = None,
    mask_path: str | None = None,
    object_hint: str | None = None,
    camera_intrinsics: dict[str, Any] | None = None,
    frame_id: str | None = None,
    coordinate_frame: str | None = None,
    evidence_source: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a serializable request for an external grasp proposal service."""

    inputs: dict[str, Any] = {}
    if image_path:
        inputs["image_path"] = image_path
    if depth_path:
        inputs["depth_path"] = depth_path
    if point_cloud_path:
        inputs["point_cloud_path"] = point_cloud_path
    if mask_path:
        inputs["mask_path"] = mask_path
    if object_hint:
        inputs["object_hint"] = object_hint
    if camera_intrinsics:
        inputs["camera_intrinsics"] = camera_intrinsics
    if frame_id:
        inputs["frame_id"] = frame_id
    if coordinate_frame:
        inputs["coordinate_frame"] = coordinate_frame
    if extra:
        inputs["extra"] = extra

    return {
        "schema": GRASPNET_REQUEST_SCHEMA,
        "inputs": inputs,
        "evidence_source": evidence_source or {},
        "notes": [
            "This request is an evidence/proposal query, not a route decision.",
            "Coordinate transforms are not implied by this request.",
        ],
    }
