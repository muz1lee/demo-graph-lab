from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .io import assert_child_path, read_json, safe_id, write_json
from .policy import PolicyResult, check_skill
from .registry import ToolRegistry
from .skill_ir import SkillIR, load_skill_ir


@dataclass(frozen=True)
class CandidatePackage:
    candidate_id: str
    package_dir: str
    skill_path: str
    code_path: str
    manifest_path: str
    static_report_path: str
    policy_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def package_skill_candidate(
    *,
    candidate_id: str,
    skill_yaml: str | Path,
    output_root: str | Path,
    registry: ToolRegistry,
    hypothesis: str,
    change_summary: str = "",
    expected_failure_modes: list[str] | None = None,
    skill_args: dict[str, Any] | None = None,
    parent_id: str | None = None,
    overwrite: bool = False,
    metadata: dict[str, Any] | None = None,
) -> CandidatePackage:
    cleaned_id = safe_id(candidate_id)
    source = Path(skill_yaml).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(str(source))
    package_dir = Path(output_root).expanduser().resolve() / cleaned_id
    if package_dir.exists():
        if not overwrite:
            raise FileExistsError(f"candidate package already exists: {package_dir}")
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    skill_dst = package_dir / "skill.yaml"
    shutil.copy2(source, skill_dst)
    ir = load_skill_ir(skill_dst)
    policy = check_skill(skill_dst, registry)
    merged_args = dict(ir.args)
    if skill_args:
        merged_args.update(skill_args)

    code_path = package_dir / "code.py"
    manifest_path = package_dir / "candidate_manifest.json"
    static_report_path = package_dir / "static_report.json"
    code_path.write_text(
        render_facade_code(
            candidate_id=cleaned_id,
            hypothesis=hypothesis,
            change_summary=change_summary,
            expected_failure_modes=expected_failure_modes or [],
            skill_args=merged_args,
        ),
        encoding="utf-8",
    )
    static_report = build_static_report(
        candidate_id=cleaned_id,
        ir=ir,
        policy=policy,
        skill_args=merged_args,
    )
    write_json(static_report_path, static_report)
    manifest = {
        "candidate_id": cleaned_id,
        "artifact_type": "knowin_yaml_skill",
        "runtime": "knowin_world_pipeline",
        "code_path": "code.py",
        "skill_path": "skill.yaml",
        "parent_id": parent_id,
        "hypothesis": hypothesis,
        "change_summary": change_summary,
        "expected_failure_modes": expected_failure_modes or [],
        "skill_args": merged_args,
        "metadata": metadata or {},
        "capabilities": ir.capabilities(),
        "policy": policy.to_dict(),
        "bridge_contract": {
            "facade_is_not_robot_runtime": True,
            "robot_runtime_entrypoint": "skill.yaml",
            "methodology": "generate_validate_publish_execute_evaluate_reflect",
            "kw_runtime": "yaml_workflow",
        },
    }
    write_json(manifest_path, manifest)
    (package_dir / "README.md").write_text(render_candidate_readme(cleaned_id, manifest), encoding="utf-8")
    return CandidatePackage(
        candidate_id=cleaned_id,
        package_dir=str(package_dir),
        skill_path=str(skill_dst),
        code_path=str(code_path),
        manifest_path=str(manifest_path),
        static_report_path=str(static_report_path),
        policy_ok=bool(policy.ok),
    )


def load_candidate_package(package_dir: str | Path) -> dict[str, Any]:
    root = Path(package_dir).expanduser().resolve()
    manifest_path = root / "candidate_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing candidate manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("candidate_manifest.json must decode to an object")
    skill_path = assert_child_path(root, str(manifest.get("skill_path") or "skill.yaml"))
    code_path = assert_child_path(root, str(manifest.get("code_path") or "code.py"))
    if not skill_path.exists():
        raise FileNotFoundError(f"missing candidate skill: {skill_path}")
    return {
        "candidate_id": str(manifest.get("candidate_id") or root.name),
        "package_dir": str(root),
        "manifest_path": str(manifest_path),
        "skill_path": str(skill_path),
        "code_path": str(code_path),
        "manifest": manifest,
    }


def build_static_report(
    *,
    candidate_id: str,
    ir: SkillIR,
    policy: PolicyResult,
    skill_args: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "source_path": ir.source_path,
        "description": ir.description,
        "skill_args": skill_args,
        "ir": ir.to_dict(),
        "policy": policy.to_dict(),
        "assessment": {
            "candidate_surface": "ASPIRE-style code.py facade",
            "robot_runtime_surface": "KW YAML skill interpreted by SkillPipeline",
            "first_stage_scope": "single continuous YAML skill, no multi-skill planner",
        },
    }


def render_facade_code(
    *,
    candidate_id: str,
    hypothesis: str,
    change_summary: str,
    expected_failure_modes: list[str],
    skill_args: dict[str, Any],
) -> str:
    expected = "; ".join(expected_failure_modes) if expected_failure_modes else "KW pipeline or predicate failure."
    args_json = json.dumps(skill_args, indent=4, ensure_ascii=False)
    return f'''"""
Hypothesis: {hypothesis}
Diff from parent: {change_summary or "Initial KW YAML skill candidate."}
Expected failure mode if wrong: {expected}

This is an ASPIRE-style facade for a knowin-world YAML skill.
The robot runtime entrypoint is skill.yaml.
"""

CANDIDATE_ID = {candidate_id!r}
CANDIDATE_KIND = "knowin_yaml_skill"
CANDIDATE_SKILL_PATH = "skill.yaml"
CANDIDATE_ARGS = {args_json}


def run_kw_skill(executor):
    """Execute through an object exposing run_skill(path, kwargs)."""
    return executor.run_skill(CANDIDATE_SKILL_PATH, CANDIDATE_ARGS)


def describe_candidate():
    return {{
        "candidate_id": CANDIDATE_ID,
        "kind": CANDIDATE_KIND,
        "skill_path": CANDIDATE_SKILL_PATH,
        "args": CANDIDATE_ARGS,
    }}
'''


def render_candidate_readme(candidate_id: str, manifest: dict[str, Any]) -> str:
    policy = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}
    return f"""# {candidate_id}

This is a KSM ASPIRE-style candidate package.

- `skill.yaml`: KW SkillPipeline runtime entrypoint.
- `code.py`: facade for ASPIRE-style history, audits, and candidate identity.
- `candidate_manifest.json`: candidate metadata and policy result.
- `static_report.json`: extracted workflow and capabilities.

Policy ok: `{policy.get("ok")}`
"""
