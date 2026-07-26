from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .candidate import load_candidate_package
from .io import read_yaml, resolve_path


@dataclass(frozen=True)
class SuiteTask:
    task_id: str
    task_path: str
    description: str
    skill_args: dict[str, Any]
    predicates: list[Any]
    reset_layout: bool
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuiteCandidateRef:
    candidate_id: str
    package_dir: str
    skill_path: str
    manifest_path: str
    manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuiteSpec:
    suite_id: str
    description: str
    manifest_path: str
    output_root: str
    publish_subdir: str
    success_threshold: float
    tasks: list[SuiteTask]
    candidates: list[SuiteCandidateRef]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_suite_spec(path: str | Path) -> SuiteSpec:
    suite_path = Path(path).expanduser().resolve()
    payload = read_yaml(suite_path)
    if not isinstance(payload, dict):
        raise ValueError(f"suite spec must be a mapping: {suite_path}")
    base_dir = suite_path.parent
    tasks = [
        _load_task(base_dir=base_dir, item=item)
        for item in payload.get("tasks", []) or []
        if isinstance(item, dict)
    ]
    candidates = [
        _load_candidate(base_dir=base_dir, value=value)
        for value in payload.get("candidate_packages", []) or []
    ]
    if not tasks:
        raise ValueError(f"suite must contain at least one task: {suite_path}")
    if not candidates:
        raise ValueError(f"suite must contain at least one candidate package: {suite_path}")
    return SuiteSpec(
        suite_id=str(payload.get("suite_id") or suite_path.stem),
        description=str(payload.get("description") or ""),
        manifest_path=str(suite_path),
        output_root=str(resolve_path(base_dir, payload.get("output_root") or "suite_runs")),
        publish_subdir=str(payload.get("publish_subdir") or ""),
        success_threshold=float(payload.get("success_threshold", 1.0)),
        tasks=tasks,
        candidates=candidates,
    )


def suite_summary(suite: SuiteSpec) -> dict[str, Any]:
    return {
        "suite_id": suite.suite_id,
        "description": suite.description,
        "manifest_path": suite.manifest_path,
        "output_root": suite.output_root,
        "publish_subdir": suite.publish_subdir,
        "success_threshold": suite.success_threshold,
        "tasks": [task.to_dict() for task in suite.tasks],
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "package_dir": candidate.package_dir,
                "skill_path": candidate.skill_path,
            }
            for candidate in suite.candidates
        ],
    }


def _load_task(*, base_dir: Path, item: dict[str, Any]) -> SuiteTask:
    task_path = resolve_path(base_dir, item.get("task_path") or "")
    payload = read_yaml(task_path)
    data = payload if isinstance(payload, dict) else {}
    args = item.get("skill_args")
    if not isinstance(args, dict):
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
    predicates = item.get("predicates")
    if not isinstance(predicates, list):
        predicates = data.get("predicates") if isinstance(data.get("predicates"), list) else []
    normalized_predicates: list[Any] = []
    for predicate in predicates:
        if isinstance(predicate, dict):
            normalized_predicates.append(dict(predicate))
        elif isinstance(predicate, str) and predicate.strip():
            normalized_predicates.append(predicate.strip())
    return SuiteTask(
        task_id=str(item.get("task_id") or data.get("task_id") or task_path.stem),
        task_path=str(task_path),
        description=str(item.get("description") or data.get("description") or ""),
        skill_args=dict(args),
        predicates=normalized_predicates,
        reset_layout=bool(item.get("reset_layout", data.get("reset_layout", True))),
        metadata={"raw": data},
    )


def _load_candidate(*, base_dir: Path, value: Any) -> SuiteCandidateRef:
    package_dir = resolve_path(base_dir, str(value))
    loaded = load_candidate_package(package_dir)
    return SuiteCandidateRef(
        candidate_id=str(loaded.get("candidate_id") or package_dir.name),
        package_dir=str(loaded.get("package_dir") or package_dir),
        skill_path=str(loaded.get("skill_path") or package_dir / "skill.yaml"),
        manifest_path=str(loaded.get("manifest_path") or package_dir / "candidate_manifest.json"),
        manifest=dict(loaded.get("manifest") or {}),
    )
