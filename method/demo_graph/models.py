"""Lightweight graph artifact used by the first M1 vertical slice."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._json import JsonValue, content_digest, freeze_json, to_primitive
from .provenance import Provenance, ProvenanceSource, assert_method_safe


def _required(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class TypedHole:
    hole_id: str
    value_type: str
    solver: str
    search_domain: Mapping[str, JsonValue]
    provenance: Provenance
    shape: tuple[int, ...] = ()
    unit: str = "unspecified"
    frame: str = "unspecified"
    required_inputs: tuple[str, ...] = ()
    runtime_verification: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.hole_id, "hole_id")
        _required(self.value_type, "value_type")
        _required(self.solver, "solver")
        _required(self.unit, "unit")
        _required(self.frame, "frame")
        object.__setattr__(self, "shape", tuple(int(item) for item in self.shape))
        object.__setattr__(self, "required_inputs", tuple(self.required_inputs))
        object.__setattr__(
            self,
            "runtime_verification",
            tuple(self.runtime_verification),
        )
        if any(item < 1 for item in self.shape):
            raise ValueError("typed-hole shape dimensions must be >= 1")
        object.__setattr__(
            self,
            "search_domain",
            freeze_json(self.search_domain, path=f"hole[{self.hole_id}]"),
        )


@dataclass(frozen=True, slots=True)
class Constraint:
    constraint_id: str
    description: str
    provenance: Provenance
    hole_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.constraint_id, "constraint_id")
        _required(self.description, "constraint description")
        object.__setattr__(self, "hole_ids", tuple(self.hole_ids))


@dataclass(frozen=True, slots=True)
class Node:
    node_id: str
    action: str
    goal: str
    controller_ref: str
    constraints: tuple[Constraint, ...]
    provenance: Provenance
    holes: tuple[TypedHole, ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    budget: Mapping[str, JsonValue] = field(default_factory=dict)
    max_attempts: int = 1
    next_node: str | None = None
    on_recoverable: str | None = None
    on_failed: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.node_id, "node_id"),
            (self.action, "action"),
            (self.goal, "goal"),
            (self.controller_ref, "controller_ref"),
        ):
            _required(value, name)
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "holes", tuple(self.holes))
        object.__setattr__(self, "preconditions", tuple(self.preconditions))
        object.__setattr__(self, "postconditions", tuple(self.postconditions))
        object.__setattr__(self, "invariants", tuple(self.invariants))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(
            self,
            "budget",
            freeze_json(self.budget, path=f"node[{self.node_id}].budget"),
        )
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if not self.constraints:
            raise ValueError(f"node {self.node_id!r} must declare a constraint")
        constraint_ids = [item.constraint_id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError(f"node {self.node_id!r} has duplicate constraints")
        hole_ids = [item.hole_id for item in self.holes]
        if len(hole_ids) != len(set(hole_ids)):
            raise ValueError(f"node {self.node_id!r} has duplicate holes")
        known_holes = set(hole_ids)
        for constraint in self.constraints:
            unknown = set(constraint.hole_ids) - known_holes
            if unknown:
                raise ValueError(
                    f"constraint {constraint.constraint_id!r} references unknown "
                    f"holes: {sorted(unknown)}"
                )

    @property
    def attributable_ids(self) -> frozenset[str]:
        return frozenset(
            [
                *(constraint.constraint_id for constraint in self.constraints),
                *(hole.hole_id for hole in self.holes),
            ]
        )


@dataclass(frozen=True, slots=True)
class ConstraintGraph:
    graph_id: str
    entry_node: str
    nodes: tuple[Node, ...]
    provenance: Provenance
    schema_version: str = "0.2"

    def __post_init__(self) -> None:
        _required(self.graph_id, "graph_id")
        _required(self.entry_node, "entry_node")
        _required(self.schema_version, "schema_version")
        object.__setattr__(self, "nodes", tuple(self.nodes))
        if not self.nodes:
            raise ValueError("graph must contain at least one node")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("graph contains duplicate node ids")
        known = set(node_ids)
        if self.entry_node not in known:
            raise ValueError(f"entry node {self.entry_node!r} is missing")
        for node in self.nodes:
            if node.next_node is not None and node.next_node not in known:
                raise ValueError(
                    f"node {node.node_id!r} targets unknown {node.next_node!r}"
                )
        # The graph cannot exist as a main-method artifact if oracle appears at
        # any depth, including below a derived provenance chain.
        assert_method_safe(self)

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)

    def assert_action_sequence(self, expected: Sequence[str]) -> None:
        actual: list[str] = []
        current: str | None = self.entry_node
        visited: set[str] = set()
        while current is not None:
            if current in visited:
                raise ValueError("graph action sequence contains a cycle")
            visited.add(current)
            node = self.node(current)
            actual.append(node.action)
            current = node.next_node
        if tuple(actual) != tuple(expected):
            raise ValueError(f"expected action sequence {tuple(expected)}, got {tuple(actual)}")

    @property
    def digest(self) -> str:
        return content_digest(self)

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=indent,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ConstraintGraph":
        def parse_provenance(value: Mapping[str, Any]) -> Provenance:
            return Provenance(
                source=ProvenanceSource(value["source"]),
                reference=value["reference"],
                confidence=value.get("confidence", 1.0),
                derived_from=tuple(
                    parse_provenance(parent)
                    for parent in value.get("derived_from", ())
                ),
            )

        def parse_hole(value: Mapping[str, Any]) -> TypedHole:
            return TypedHole(
                hole_id=value["hole_id"],
                value_type=value["value_type"],
                solver=value["solver"],
                search_domain=value.get("search_domain", {}),
                provenance=parse_provenance(value["provenance"]),
                shape=tuple(value.get("shape", ())),
                unit=value.get("unit", "unspecified"),
                frame=value.get("frame", "unspecified"),
                required_inputs=tuple(value.get("required_inputs", ())),
                runtime_verification=tuple(
                    value.get("runtime_verification", ())
                ),
            )

        def parse_constraint(value: Mapping[str, Any]) -> Constraint:
            return Constraint(
                constraint_id=value["constraint_id"],
                description=value["description"],
                provenance=parse_provenance(value["provenance"]),
                hole_ids=tuple(value.get("hole_ids", ())),
            )

        def parse_node(value: Mapping[str, Any]) -> Node:
            return Node(
                node_id=value["node_id"],
                action=value["action"],
                goal=value["goal"],
                controller_ref=value["controller_ref"],
                constraints=tuple(
                    parse_constraint(item) for item in value["constraints"]
                ),
                provenance=parse_provenance(value["provenance"]),
                holes=tuple(parse_hole(item) for item in value.get("holes", ())),
                preconditions=tuple(value.get("preconditions", ())),
                postconditions=tuple(value.get("postconditions", ())),
                invariants=tuple(value.get("invariants", ())),
                evidence_refs=tuple(value.get("evidence_refs", ())),
                budget=value.get("budget", {}),
                max_attempts=value.get("max_attempts", 1),
                next_node=value.get("next_node"),
                on_recoverable=value.get("on_recoverable"),
                on_failed=value.get("on_failed"),
            )

        return cls(
            graph_id=raw["graph_id"],
            entry_node=raw["entry_node"],
            nodes=tuple(parse_node(item) for item in raw["nodes"]),
            provenance=parse_provenance(raw["provenance"]),
            schema_version=raw.get("schema_version", "0.2"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "ConstraintGraph":
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise TypeError("graph JSON root must be an object")
        return cls.from_dict(raw)

    @classmethod
    def load_json(cls, path: str | Path) -> "ConstraintGraph":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))
