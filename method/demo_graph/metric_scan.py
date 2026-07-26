"""Static scanner and freeze gate for scene-specific metric literals."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_SCANNED_SUFFIXES = frozenset({".py", ".yaml", ".yml", ".json"})
_SPATIAL_MARKERS = frozenset(
    {
        "coordinate",
        "hole",
        "location",
        "offset",
        "place",
        "pose",
        "position",
        "seed",
        "slot",
        "target",
        "waypoint",
        "world",
        "xquat",
        "xyz",
    }
)
_NUMBER = re.compile(
    r"(?<![\w.])[-+]?(?:\d+\.\d+|\d+\.?)(?:[eE][-+]?\d+)?(?![\w.])"
)
_YAML_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s*)?(?P<key>[A-Za-z_][\w.-]*):")


class MetricLiteralViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MetricFinding:
    path: str
    line: int
    column: int
    literal: str
    context: str
    rule: str = "scene_specific_metric_literal"


@dataclass(frozen=True, slots=True)
class MetricScanReport:
    files_scanned: tuple[str, ...]
    findings: tuple[MetricFinding, ...]

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "demo_graph.metric_scan.v1",
            "clean": self.clean,
            "file_count": len(self.files_scanned),
            "finding_count": len(self.findings),
            "files_scanned": list(self.files_scanned),
            "findings": [asdict(item) for item in self.findings],
        }


@dataclass(frozen=True, slots=True)
class FrozenPolicy:
    path: str
    code_digest: str
    scan_report: MetricScanReport


def _tokens(name: str) -> frozenset[str]:
    return frozenset(
        token
        for token in re.split(r"[^a-z0-9]+", name.lower())
        if token
    )


def _is_spatial_context(name: str) -> bool:
    tokens = _tokens(name)
    return bool(tokens & _SPATIAL_MARKERS) or any(
        marker in name.lower()
        for marker in ("xquat", "xyz", "world_", "per_seed")
    )


def _literal_text(source: str, node: ast.AST, value: float | int) -> str:
    segment = ast.get_source_segment(source, node)
    return segment.strip() if segment else repr(value)


def _numeric_value(node: ast.AST) -> float | int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    ):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        return sign * node.operand.value
    return None


def _name_from_expr(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_from_expr(node.value)}.{node.attr}".strip(".")
    if isinstance(node, (ast.Tuple, ast.List)):
        return ".".join(_name_from_expr(item) for item in node.elts)
    return ""


def _python_context(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
) -> str:
    current = node
    for _ in range(8):
        parent = parents.get(current)
        if parent is None:
            return ""
        if isinstance(parent, ast.keyword):
            return parent.arg or ""
        if isinstance(parent, ast.Assign):
            return ".".join(_name_from_expr(target) for target in parent.targets)
        if isinstance(parent, ast.AnnAssign):
            return _name_from_expr(parent.target)
        if isinstance(parent, ast.NamedExpr):
            return _name_from_expr(parent.target)
        if isinstance(parent, ast.Dict):
            for key, value in zip(parent.keys, parent.values):
                if value is current and isinstance(key, ast.Constant):
                    return str(key.value)
        if isinstance(parent, ast.Call):
            return _name_from_expr(parent.func)
        current = parent
    return ""


def _python_findings(path: Path, source: str) -> list[MetricFinding]:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise MetricLiteralViolation(f"cannot parse policy {path}: {exc}") from exc
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    findings: list[MetricFinding] = []
    skip_constants: set[ast.AST] = set()
    for node in ast.walk(tree):
        if node in skip_constants:
            continue
        value = _numeric_value(node)
        if value is None or abs(float(value)) <= 1e-12:
            continue
        if isinstance(node, ast.UnaryOp):
            skip_constants.add(node.operand)
        parent = parents.get(node)
        if isinstance(parent, ast.Subscript) and parent.slice is node:
            continue
        context = _python_context(node, parents)
        if not _is_spatial_context(context):
            continue
        findings.append(
            MetricFinding(
                path=str(path),
                line=int(getattr(node, "lineno", 0)),
                column=int(getattr(node, "col_offset", 0)) + 1,
                literal=_literal_text(source, node, value),
                context=context,
            )
        )
    return findings


def _yaml_findings(path: Path, source: str) -> list[MetricFinding]:
    stack: list[tuple[int, str]] = []
    findings: list[MetricFinding] = []
    for line_number, original in enumerate(source.splitlines(), start=1):
        content = original.split("#", 1)[0]
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        key_match = _YAML_KEY.match(content)
        if key_match:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, key_match.group("key")))
        context = ".".join(item[1] for item in stack)
        if not _is_spatial_context(context):
            continue
        for match in _NUMBER.finditer(content):
            value = float(match.group())
            if abs(value) <= 1e-12:
                continue
            findings.append(
                MetricFinding(
                    path=str(path),
                    line=line_number,
                    column=match.start() + 1,
                    literal=match.group(),
                    context=context,
                )
            )
    return findings


def _json_findings(path: Path, source: str) -> list[MetricFinding]:
    try:
        raw = json.loads(source)
    except json.JSONDecodeError as exc:
        raise MetricLiteralViolation(f"cannot parse JSON policy {path}: {exc}") from exc
    findings: list[MetricFinding] = []

    def walk(value: Any, keys: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, (*keys, str(key)))
            return
        if isinstance(value, list):
            for item in value:
                walk(item, keys)
            return
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and abs(float(value)) > 1e-12
        ):
            context = ".".join(keys)
            if _is_spatial_context(context):
                findings.append(
                    MetricFinding(
                        path=str(path),
                        line=0,
                        column=0,
                        literal=repr(value),
                        context=context,
                    )
                )

    walk(raw, ())
    return findings


def _iter_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(
                item
                for item in sorted(path.rglob("*"))
                if item.is_file() and item.suffix.lower() in _SCANNED_SUFFIXES
            )
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(path)
    unique = {str(item.resolve()): item for item in files}
    return tuple(unique[key] for key in sorted(unique))


def scan_paths(paths: Iterable[str | Path]) -> MetricScanReport:
    files = _iter_paths(paths)
    findings: list[MetricFinding] = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".py":
            findings.extend(_python_findings(path, source))
        elif suffix == ".json":
            findings.extend(_json_findings(path, source))
        else:
            findings.extend(_yaml_findings(path, source))
    return MetricScanReport(
        files_scanned=tuple(str(path) for path in files),
        findings=tuple(findings),
    )


def _file_digest(path: str | Path) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return f"sha256:{digest}"


def freeze_policy(path: str | Path) -> FrozenPolicy:
    policy_path = Path(path)
    report = scan_paths((policy_path,))
    if not report.clean:
        raise MetricLiteralViolation(
            f"policy contains {len(report.findings)} scene-specific metric literal(s)"
        )
    return FrozenPolicy(
        path=str(policy_path),
        code_digest=_file_digest(policy_path),
        scan_report=report,
    )


def assert_frozen_policy_unchanged(frozen: FrozenPolicy) -> None:
    actual = _file_digest(frozen.path)
    if actual != frozen.code_digest:
        raise MetricLiteralViolation(
            f"frozen policy changed: expected {frozen.code_digest}, got {actual}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan generated policy code for scene-specific metric literals."
    )
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--output")
    parser.add_argument(
        "--expect",
        choices=("clean", "findings", "any"),
        default="clean",
    )
    args = parser.parse_args(argv)
    report = scan_paths(args.paths)
    payload = json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    if args.expect == "clean" and not report.clean:
        return 1
    if args.expect == "findings" and report.clean:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
