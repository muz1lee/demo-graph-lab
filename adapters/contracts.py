"""Evidence contracts for values crossing the Method Broker boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ._json import content_digest, require_jsonable


_PRIVILEGED_OUTPUT_KEYS = frozenset(
    {
        "asset",
        "asset_id",
        "entities",
        "entity_id",
        "execution_success",
        "oracle",
        "predicates",
        "probes",
        "run_success",
        "scene",
        "scene_id",
        "state",
        "target_binding",
        "task_success",
    }
)


METHOD_VISIBLE_SOURCES = frozenset(
    {
        "demo_video",
        "task_instruction",
        "runtime_perception",
        "robot_state",
        "generic_prior",
        "derived",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Opaque reference to non-privileged evidence visible to a policy."""

    evidence_id: str
    source: str
    digest: str
    observation_revision: str | None = None
    derived_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence_id must be non-empty")
        if self.source not in METHOD_VISIBLE_SOURCES:
            raise ValueError(f"provenance source is not method-visible: {self.source!r}")
        if not self.digest.startswith("sha256:") or len(self.digest) != 71:
            raise ValueError("digest must be a sha256:<64 hex chars> identity")
        try:
            int(self.digest[7:], 16)
        except ValueError as exc:
            raise ValueError("digest must contain lowercase/uppercase hexadecimal characters") from exc
        if self.source == "derived" and not self.derived_from:
            raise ValueError("derived evidence must name at least one upstream evidence id")
        if self.source != "derived" and self.derived_from:
            raise ValueError("derived_from is only valid for source='derived'")

    @classmethod
    def from_value(
        cls,
        *,
        evidence_id: str,
        source: str,
        value: Any,
        observation_revision: str | None = None,
        derived_from: tuple[str, ...] = (),
    ) -> "EvidenceRef":
        return cls(
            evidence_id=evidence_id,
            source=source,
            digest=content_digest(value),
            observation_revision=observation_revision,
            derived_from=derived_from,
        )


@dataclass(frozen=True, slots=True)
class MethodResult:
    """A broker result that cannot silently discard its evidence lineage."""

    value: Any
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        require_jsonable(self.value, name="MethodResult.value")
        assert_method_payload_safe(self.value)
        if not self.evidence:
            raise ValueError("MethodResult must include at least one evidence reference")
        identities = [item.evidence_id for item in self.evidence]
        if len(identities) != len(set(identities)):
            raise ValueError("MethodResult evidence ids must be unique")


def assert_method_payload_safe(value: Any, *, path: str = "$") -> None:
    """Reject obvious EvalServer/oracle structures at the broker boundary."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _PRIVILEGED_OUTPUT_KEYS:
                raise ValueError(f"{path}.{key} is privileged and not method-visible")
            assert_method_payload_safe(item, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            assert_method_payload_safe(item, path=f"{path}[{index}]")
    elif isinstance(value, str) and value.lower() == "privileged_oracle":
        raise ValueError(f"{path} is labeled privileged_oracle")
