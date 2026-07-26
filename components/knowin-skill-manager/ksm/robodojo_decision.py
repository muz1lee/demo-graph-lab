from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import new_run_dir
from .config import ManagerConfig
from .io import read_yaml, safe_id, write_json
from .llm import GPTChatClient, StaticResponseChatClient, resolve_llm_config
from .llm_generator import extract_llm_json
from .registry import ToolRegistry, build_registry


SCHEMA = "ksm.robodojo.task_skill_decision.v1"
DECISIONS = {
    "reuse_existing_skill",
    "new_yaml_subskill_candidate",
    "blocked_by_missing_low_level_primitive",
}
DEFAULT_TASK_CLASSES = [
    "general_pickup",
    "put_bottles_into_dustbin",
    "stack_blocks",
    "pour_balls_into_vase",
    "deposit_coin",
    "insert_key",
    "plug_in_charger",
]


@dataclass(frozen=True)
class DecisionTaskSample:
    task_id: str
    task_class: str
    prompt: str
    tags: list[str]
    suite_path: str
    scene_path: str
    success: dict[str, Any]
    subtasks: list[dict[str, Any]]
    scene_summary: dict[str, Any]
    reference_decision: str
    reference_reason: str

    def to_dict(self, *, include_reference: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_reference:
            payload.pop("reference_decision", None)
            payload.pop("reference_reason", None)
        return payload


@dataclass(frozen=True)
class DecisionResult:
    run_dir: str
    report_path: str
    summary: dict[str, Any]
    decisions: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_robodojo_decision(
    *,
    config: ManagerConfig,
    output_root: str | Path | None = None,
    task_classes: list[str] | None = None,
    max_per_class: int = 1,
    candidate_prefix: str = "robodojo_decision",
    llm_overrides: dict[str, Any] | None = None,
    response_file: str | Path | None = None,
    client: Any | None = None,
) -> DecisionResult:
    registry = build_registry(config)
    root = Path(output_root).expanduser().resolve() if output_root else config.root_dir / "experiments" / "robodojo_decision"
    run_dir = new_run_dir(root / "runs", safe_id(candidate_prefix))
    samples = collect_decision_samples(
        config=config,
        task_classes=task_classes or DEFAULT_TASK_CLASSES,
        max_per_class=max_per_class,
    )
    settings = resolve_llm_config(config.llm, llm_overrides)
    chat_client = client or (
        StaticResponseChatClient.from_file(response_file)
        if response_file
        else GPTChatClient(settings)
    )
    decisions: list[dict[str, Any]] = []
    for sample in samples:
        prompt = build_task_decision_prompt(sample=sample, registry=registry)
        prompt_path = run_dir / "prompts" / f"{safe_id(sample.task_id)}.prompt.txt"
        response_path = run_dir / "responses" / f"{safe_id(sample.task_id)}.response.json"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        response = chat_client.complete([{"role": "user", "content": prompt}])
        parsed = normalize_decision_payload(
            extract_llm_json(response.text),
            sample=sample,
            prompt_path=prompt_path,
            response=response,
            settings=settings.safe_dict(),
        )
        write_json(response_path, {"text": response.text, "parsed": parsed, "raw": response.raw})
        decisions.append(parsed)
    summary = summarize_decisions(decisions)
    write_json(run_dir / "samples.json", [sample.to_dict() for sample in samples])
    report_path = write_json(
        run_dir / "decision_report.json",
        {
            "schema": "ksm.robodojo.task_skill_decision_report.v1",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_dir": str(run_dir),
            "summary": summary,
            "decisions": decisions,
        },
    )
    (run_dir / "README.md").write_text(render_decision_report(summary=summary, decisions=decisions), encoding="utf-8")
    return DecisionResult(run_dir=str(run_dir), report_path=str(report_path), summary=summary, decisions=decisions)


def collect_decision_samples(
    *,
    config: ManagerConfig,
    task_classes: list[str],
    max_per_class: int,
) -> list[DecisionTaskSample]:
    samples: list[DecisionTaskSample] = []
    for task_class in task_classes:
        suite_dir = config.kw_repo / "tasks" / "robodojo" / task_class
        if not suite_dir.exists():
            continue
        count = 0
        for suite_path in sorted(suite_dir.glob("*.suite.yaml")):
            sample = _sample_from_suite(config=config, task_class=task_class, suite_path=suite_path)
            if sample is None:
                continue
            samples.append(sample)
            count += 1
            if count >= max(1, int(max_per_class)):
                break
    return samples


def build_task_decision_prompt(*, sample: DecisionTaskSample, registry: ToolRegistry) -> str:
    registry_payload = {
        "ctrl": registry.ctrl,
        "info": registry.info,
        "reasoning": _public_reasoning_names(registry.reasoning),
        "capability_counts": registry.capability_counts,
        "skills": [
            {
                "path": skill.path,
                "description": skill.description,
                "args": skill.args,
                "actions": _public_action_names(skill.actions),
                "capabilities": skill.capabilities,
            }
            for skill in registry.skills[:80]
            if not _is_experiment_skill(skill.path, registry.test_skill_dir)
        ],
    }
    task_payload = sample.to_dict(include_reference=False)
    return f"""
You are the KSM task-level decision agent for RoboDojo skill generation.

Return exactly one JSON object, no Markdown.

Classify the task into exactly one decision:
- reuse_existing_skill: the task can primarily reuse stable existing KW skills; do not create a new skill candidate.
- new_yaml_subskill_candidate: KW has enough existing skills/control/reasoning to compose a reusable YAML subskill candidate, but no stable existing skill directly covers the behavior.
- blocked_by_missing_low_level_primitive: an essential low-level capability is missing, such as insert/plug/screw/fine alignment/pour/handover/dual-arm coordination; do not fake a YAML skill.

Required JSON fields:
- schema: exactly "{SCHEMA}"
- task_id: string
- decision: one of {sorted(DECISIONS)}
- confidence: number from 0 to 1
- selected_existing_skills: list of skill paths to reuse, empty if none
- proposed_candidate_name: string or null
- missing_capabilities: list of concrete missing capabilities
- rationale: short task-specific reason
- next_action: one of "run_reuse_baseline", "create_yaml_subskill_candidate", "write_gap_report"

Decision rules:
- Prefer reuse_existing_skill only when the complete task family is already covered by stable KW skills.
- Repeated pick/place can still be reuse_existing_skill if it is just repeated calls to existing pick/place skills.
- Use new_yaml_subskill_candidate only when the missing unit is a reusable YAML-level composition, not a new controller/reasoner.
- Use blocked_by_missing_low_level_primitive when the task requires precise insertion, plugging, screwing, pouring, fine alignment, or handoff that is not represented in the registry.
- Do not use scene asset ids as evidence of an executable skill.
- Do not output YAML.

RoboDojo task:
{json.dumps(task_payload, ensure_ascii=False, indent=2)}

KW registry:
{json.dumps(registry_payload, ensure_ascii=False, indent=2)}
""".strip()


def normalize_decision_payload(
    payload: dict[str, Any],
    *,
    sample: DecisionTaskSample,
    prompt_path: Path,
    response: Any,
    settings: dict[str, Any],
) -> dict[str, Any]:
    decision = str(payload.get("decision") or "").strip()
    if decision not in DECISIONS:
        decision = "blocked_by_missing_low_level_primitive"
    normalized = {
        "schema": SCHEMA,
        "task_id": sample.task_id,
        "task_class": sample.task_class,
        "prompt": sample.prompt,
        "decision": decision,
        "confidence": _float_between(payload.get("confidence"), default=0.0),
        "selected_existing_skills": _string_list(payload.get("selected_existing_skills")),
        "proposed_candidate_name": payload.get("proposed_candidate_name") if payload.get("proposed_candidate_name") else None,
        "missing_capabilities": _string_list(payload.get("missing_capabilities")),
        "rationale": str(payload.get("rationale") or ""),
        "next_action": _normalize_next_action(payload.get("next_action"), decision),
        "reference": {
            "decision": sample.reference_decision,
            "reason": sample.reference_reason,
            "matches": decision == sample.reference_decision,
        },
        "artifacts": {
            "prompt": str(prompt_path),
        },
        "llm": {
            "provider": getattr(response, "provider", ""),
            "model": getattr(response, "model", ""),
            "settings": settings,
        },
    }
    return normalized


def summarize_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    matches = 0
    mismatches: list[dict[str, Any]] = []
    for item in decisions:
        decision = str(item.get("decision") or "")
        counts[decision] = counts.get(decision, 0) + 1
        reference = item.get("reference") if isinstance(item.get("reference"), dict) else {}
        if reference.get("matches"):
            matches += 1
        else:
            mismatches.append(
                {
                    "task_id": item.get("task_id"),
                    "task_class": item.get("task_class"),
                    "decision": decision,
                    "reference_decision": reference.get("decision"),
                }
            )
    total = len(decisions)
    return {
        "total": total,
        "decision_counts": counts,
        "reference_match_rate": (matches / total) if total else 0.0,
        "mismatches": mismatches,
    }


def render_decision_report(*, summary: dict[str, Any], decisions: list[dict[str, Any]]) -> str:
    lines = [
        "# RoboDojo Task-Level Decision Report",
        "",
        f"- Total: `{summary.get('total')}`",
        f"- Reference match rate: `{summary.get('reference_match_rate')}`",
        f"- Decision counts: `{summary.get('decision_counts')}`",
        "",
        "| Task | Class | Decision | Reference | Next | Confidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in decisions:
        reference = item.get("reference") if isinstance(item.get("reference"), dict) else {}
        lines.append(
            f"| `{item.get('task_id')}` | `{item.get('task_class')}` | `{item.get('decision')}` | `{reference.get('decision')}` | `{item.get('next_action')}` | `{item.get('confidence')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _sample_from_suite(*, config: ManagerConfig, task_class: str, suite_path: Path) -> DecisionTaskSample | None:
    try:
        suite = read_yaml(suite_path)
        if not isinstance(suite, dict):
            return None
        tasks = suite.get("tasks")
        if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
            return None
        task = tasks[0]
        scene_path = (config.kw_repo / str(suite.get("scene") or "")).resolve()
        scene_summary = _scene_summary(scene_path)
        reference_decision, reference_reason = _reference_decision(task_class=task_class, task=task)
        return DecisionTaskSample(
            task_id=safe_id(str(task.get("task_id") or suite_path.stem)),
            task_class=task_class,
            prompt=str(task.get("prompt") or ""),
            tags=[str(tag) for tag in task.get("tags", [])] if isinstance(task.get("tags"), list) else [],
            suite_path=str(suite_path.resolve()),
            scene_path=str(scene_path),
            success=task.get("success") if isinstance(task.get("success"), dict) else {},
            subtasks=[dict(item) for item in task.get("subtasks", []) if isinstance(item, dict)] if isinstance(task.get("subtasks"), list) else [],
            scene_summary=scene_summary,
            reference_decision=reference_decision,
            reference_reason=reference_reason,
        )
    except Exception:
        return None


def _scene_summary(scene_path: Path) -> dict[str, Any]:
    try:
        scene = read_yaml(scene_path)
    except Exception:
        return {"available": False}
    if not isinstance(scene, dict):
        return {"available": False}
    refs = ((scene.get("metadata") or {}).get("robodojo_asset_refs") if isinstance(scene.get("metadata"), dict) else None)
    imports = scene.get("imports")
    asset_refs = [ref for ref in refs if isinstance(ref, dict)] if isinstance(refs, list) else []
    import_items = [item for item in imports if isinstance(item, dict)] if isinstance(imports, list) else []
    categories: dict[str, int] = {}
    qualified_false: list[str] = []
    collision_false: list[str] = []
    for ref in asset_refs:
        category = str(ref.get("category") or "unknown")
        categories[category] = categories.get(category, 0) + 1
        if not bool(ref.get("qualified")):
            qualified_false.append(str(ref.get("id") or ""))
        if not bool(ref.get("has_collision_prims")):
            collision_false.append(str(ref.get("id") or ""))
    return {
        "available": True,
        "asset_count": len(asset_refs),
        "import_count": len(import_items),
        "categories": categories,
        "qualified_false_refs": qualified_false[:12],
        "has_collision_prims_false_refs": collision_false[:12],
    }


def _reference_decision(*, task_class: str, task: dict[str, Any]) -> tuple[str, str]:
    text = " ".join([task_class, str(task.get("prompt") or ""), " ".join(str(tag) for tag in task.get("tags", []))]).lower()
    if any(token in text for token in ("insert", "plug", "deposit", "screw")):
        return "blocked_by_missing_low_level_primitive", "Requires insertion/plug/deposit precision beyond current stable KW YAML skills."
    if any(token in text for token in ("pour", "align")):
        return "blocked_by_missing_low_level_primitive", "Requires pouring/alignment behavior that is not covered by current stable KW skills."
    if "stack" in text:
        return "new_yaml_subskill_candidate", "Likely expressible as reusable pick/place-on-object composition, but not yet a stable family skill."
    if any(token in text for token in ("pickup", "pick up", "pickplace", "dustbin")):
        return "reuse_existing_skill", "Covered by existing semantic pick or semantic pickplace family."
    return "new_yaml_subskill_candidate", "No direct stable family skill identified, but task may be YAML-composable."


def _normalize_next_action(value: Any, decision: str) -> str:
    text = str(value or "").strip()
    allowed = {"run_reuse_baseline", "create_yaml_subskill_candidate", "write_gap_report"}
    if text in allowed:
        return text
    if decision == "reuse_existing_skill":
        return "run_reuse_baseline"
    if decision == "new_yaml_subskill_candidate":
        return "create_yaml_subskill_candidate"
    return "write_gap_report"


def _float_between(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return max(0.0, min(1.0, number))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _public_reasoning_names(names: list[str]) -> list[str]:
    return [name for name in names if "qwen" not in name.lower()]


def _public_action_names(actions: list[str]) -> list[str]:
    return [action for action in actions if "qwen" not in action.lower()]


def _is_experiment_skill(path: str, test_skill_dir: str) -> bool:
    normalized = str(path or "").strip("/")
    prefix = str(test_skill_dir or "").strip("/")
    if prefix.startswith("knowin_skills/"):
        prefix = prefix[len("knowin_skills/") :]
    return bool(prefix and (normalized == prefix or normalized.startswith(f"{prefix}/")))
