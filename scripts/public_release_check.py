#!/usr/bin/env python3
"""Fail closed when release-candidate files violate the public repository boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_FILE_BYTES = 10 * 1024 * 1024
SOURCE_MANIFEST_PATH = Path("components/SOURCE_MANIFEST.json")
FORBIDDEN_PARTS = {
    ".codex_backups",
    ".venv",
    "artifacts",
    "audit",
    "backups",
    "cache",
    "checkpoints",
    "demo_evidence",
    "knowin-world",
    "knowin-world-data",
    "logs",
    "models",
    "oracle",
    "outputs",
    "repos",
    "runs",
    "vendor",
    "venv",
    "weights",
}
FORBIDDEN_NAMES = {
    ".env",
    ".openaikey",
    ".qwenkey",
    "id_ed25519",
    "id_rsa",
    "secrets.env",
}
FORBIDDEN_SUFFIXES = {
    ".ckpt",
    ".key",
    ".mp4",
    ".npy",
    ".npz",
    ".onnx",
    ".p12",
    ".pem",
    ".pfx",
    ".pth",
    ".pt",
    ".safetensors",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    "internal service endpoint": re.compile(
        r"\b(?:"
        r"10(?:\.[0-9]{1,3}){3}|"
        r"192\.168(?:\.[0-9]{1,3}){2}|"
        r"172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2}|"
        r"101\.132\.143\.105"
        r")(?::[0-9]{1,5})?\b"
    ),
}


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str


def release_candidate_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [
        root / entry.decode("utf-8")
        for entry in result.stdout.split(b"\0")
        if entry
    ]


def _forbidden_name(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in FORBIDDEN_NAMES
        or (name.startswith(".env.") and name != ".env.example")
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
    )


def scan(paths: list[Path], root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        relative = path.relative_to(root)
        parts = set(relative.parts)
        if parts & FORBIDDEN_PARTS or any(part.startswith(".venv") for part in parts):
            violations.append(Violation(str(relative), "forbidden path"))
            continue
        if _forbidden_name(path):
            violations.append(Violation(str(relative), "forbidden filename or file type"))
            continue
        if not path.exists():
            # 工作区已删除、尚未从 index 移除的路径：跳过，由 git status 处理。
            continue
        if path.is_symlink():
            violations.append(Violation(str(relative), "symbolic links are not allowed"))
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            violations.append(Violation(str(relative), "tracked path is not a regular file"))
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            violations.append(
                Violation(str(relative), f"file is larger than {MAX_FILE_BYTES} bytes")
            )
            continue
        data = path.read_bytes()
        if b"\0" in data:
            violations.append(Violation(str(relative), "binary file is not allowlisted"))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            violations.append(Violation(str(relative), "file is not valid UTF-8 text"))
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                violations.append(Violation(str(relative), f"possible {label}"))
    return violations


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_source_manifest(root: Path, candidates: list[Path]) -> list[Violation]:
    manifest_path = root / SOURCE_MANIFEST_PATH
    violation_path = str(SOURCE_MANIFEST_PATH)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [Violation(violation_path, f"cannot read source manifest: {exc}")]

    if manifest.get("schema_version") != "1.0":
        return [Violation(violation_path, "unsupported source manifest schema")]

    components = manifest.get("components")
    if not isinstance(components, dict) or not components:
        return [Violation(violation_path, "source manifest has no components")]

    violations: list[Violation] = []
    component_digests: list[tuple[str, str]] = []
    total_files = 0
    candidate_set = {path.resolve() for path in candidates}

    for name, component in components.items():
        if not isinstance(component, dict):
            violations.append(Violation(violation_path, f"invalid component entry: {name}"))
            continue
        target_value = component.get("target")
        if not isinstance(target_value, str):
            violations.append(Violation(violation_path, f"component {name} has no target"))
            continue
        target = (root / target_value).resolve()
        try:
            target.relative_to((root / "components").resolve())
        except ValueError:
            violations.append(
                Violation(violation_path, f"component {name} target escapes components/")
            )
            continue

        entries = component.get("files")
        if not isinstance(entries, list):
            violations.append(Violation(violation_path, f"component {name} has no file list"))
            continue

        expected_paths: set[Path] = set()
        aggregate_rows: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                violations.append(
                    Violation(violation_path, f"component {name} has invalid file entry")
                )
                continue
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                violations.append(
                    Violation(violation_path, f"component {name} has unsafe path: {entry['path']}")
                )
                continue
            source_file = target / relative
            expected_paths.add(source_file.resolve())
            if source_file.is_symlink() or not source_file.is_file():
                violations.append(
                    Violation(
                        violation_path,
                        f"manifest file is missing or unsafe: {name}/{entry['path']}",
                    )
                )
                continue
            digest = _sha256(source_file)
            size = source_file.stat().st_size
            if digest != entry.get("sha256") or size != entry.get("size_bytes"):
                violations.append(
                    Violation(
                        violation_path,
                        f"manifest hash/size mismatch: {name}/{entry['path']}",
                    )
                )
            aggregate_rows.append(f"{entry['path']}\t{digest}\n")

        if len(expected_paths) != len(entries):
            violations.append(
                Violation(violation_path, f"duplicate file path in component {name}")
            )
        public_component_files = {
            path
            for path in candidate_set
            if path != manifest_path.resolve() and (path == target or target in path.parents)
        }
        if public_component_files != expected_paths:
            violations.append(
                Violation(violation_path, f"manifest file set mismatch for component {name}")
            )

        aggregate = hashlib.sha256("".join(aggregate_rows).encode("utf-8")).hexdigest()
        if aggregate != component.get("aggregate_sha256"):
            violations.append(
                Violation(violation_path, f"aggregate digest mismatch for component {name}")
            )
        if len(entries) != component.get("file_count"):
            violations.append(
                Violation(violation_path, f"file count mismatch for component {name}")
            )
        component_digests.append((name, aggregate))
        total_files += len(entries)

    global_rows = "".join(
        f"{name}\t{digest}\n" for name, digest in sorted(component_digests)
    )
    global_digest = hashlib.sha256(global_rows.encode("utf-8")).hexdigest()
    if global_digest != manifest.get("global_aggregate_sha256"):
        violations.append(Violation(violation_path, "global aggregate digest mismatch"))
    if total_files != manifest.get("file_count"):
        violations.append(Violation(violation_path, "global file count mismatch"))
    if len(components) != manifest.get("component_count"):
        violations.append(Violation(violation_path, "component count mismatch"))

    for sanitization in manifest.get("public_sanitizations", []):
        if not isinstance(sanitization, dict):
            violations.append(Violation(violation_path, "invalid public sanitization entry"))
            continue
        component = components.get(sanitization.get("component"), {})
        entries = component.get("files", []) if isinstance(component, dict) else []
        matched = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("path") == sanitization.get("path")
            ),
            None,
        )
        provenance = matched.get("provenance", {}) if matched else {}
        if (
            not matched
            or matched.get("sha256") != sanitization.get("sanitized_sha256")
            or provenance.get("upstream_sha256") != sanitization.get("upstream_sha256")
            or provenance.get("patch_reason") != sanitization.get("patch_reason")
        ):
            violations.append(Violation(violation_path, "public sanitization provenance mismatch"))

    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    candidates = release_candidate_paths(root)
    violations = scan(candidates, root)
    violations.extend(validate_source_manifest(root, candidates))
    if violations:
        for violation in violations:
            print(f"{violation.path}: {violation.reason}", file=sys.stderr)
        return 1
    print("public release check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
