"""Provenance contracts and the main-method ground-truth firewall."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Mapping


class ProvenanceError(ValueError):
    """Raised when a main-method artifact depends on privileged information."""


class ProvenanceSource(str, Enum):
    DEMO_VIDEO = "demo_video"
    TASK_INSTRUCTION = "task_instruction"
    RUNTIME_PERCEPTION = "runtime_perception"
    ROBOT_STATE = "robot_state"
    GENERIC_PRIOR = "generic_prior"
    DERIVED = "derived"
    PRIVILEGED_ORACLE = "privileged_oracle"


@dataclass(frozen=True, slots=True)
class Provenance:
    """A recursively traceable source declaration.

    ``privileged_oracle`` is representable so the trusted evaluator can label
    its own artifacts. A main-method graph rejects it through
    :func:`assert_method_safe`.
    """

    source: ProvenanceSource
    reference: str
    confidence: float = 1.0
    derived_from: tuple["Provenance", ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.source, str):
            try:
                object.__setattr__(self, "source", ProvenanceSource(self.source))
            except ValueError as error:
                raise ProvenanceError(f"unknown provenance source: {self.source}") from error
        object.__setattr__(self, "derived_from", tuple(self.derived_from))
        if not self.reference.strip():
            raise ProvenanceError("provenance reference must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ProvenanceError("provenance confidence must be in [0, 1]")
        if self.source is ProvenanceSource.DERIVED and not self.derived_from:
            raise ProvenanceError("derived provenance must declare derived_from")
        if self.source is not ProvenanceSource.DERIVED and self.derived_from:
            raise ProvenanceError("only derived provenance may declare derived_from")

    @classmethod
    def derived(
        cls,
        reference: str,
        *parents: "Provenance",
        confidence: float = 1.0,
    ) -> "Provenance":
        return cls(
            source=ProvenanceSource.DERIVED,
            reference=reference,
            confidence=confidence,
            derived_from=tuple(parents),
        )

    def assert_method_safe(self) -> None:
        assert_method_safe(self)


def assert_method_safe(value: Any) -> None:
    """Recursively reject any dependency on ``privileged_oracle``.

    This deliberately follows dataclasses, mappings and sequences so an oracle
    label cannot be hidden below a ``derived`` source or nested graph field.
    """

    visited: set[int] = set()

    def walk(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, bytes, int, float, bool, Enum)):
            return
        item_id = id(item)
        if item_id in visited:
            return
        visited.add(item_id)

        if isinstance(item, Provenance):
            if item.source is ProvenanceSource.PRIVILEGED_ORACLE:
                raise ProvenanceError(
                    f"{path} depends on privileged_oracle ({item.reference})"
                )
            for index, parent in enumerate(item.derived_from):
                walk(parent, f"{path}.derived_from[{index}]")
            return
        if is_dataclass(item) and not isinstance(item, type):
            for field in fields(item):
                walk(getattr(item, field.name), f"{path}.{field.name}")
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                walk(nested, f"{path}[{key!r}]")
            return
        if isinstance(item, (tuple, list, set, frozenset)):
            for index, nested in enumerate(item):
                walk(nested, f"{path}[{index}]")

    walk(value, "$")
