from __future__ import annotations

import ast
import json
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .artifacts import new_run_dir
from .candidate import CandidatePackage, package_skill_candidate
from .capture import PeriodicKeyframeSampler, capture_keyframes
from .config import ManagerConfig
from .io import read_yaml, safe_id, write_json, write_yaml
from .llm_generator import generate_skill_from_task_llm
from .grounding import _finalize_preflight_status, choose_grounded_label, apply_stateful_plan_preflight
from .registry import ToolRegistry, build_registry
from .runner import PipelineDirectClient
from .skill_candidates import build_skill_candidate_artifacts, write_skill_candidate_artifacts
from .staged_experiment import StagedExperimentResult, run_staged_experiment
from .suite_runner import SuiteRunResult, run_suite


SUPPORTED_TASK_CLASSES = {"general_pickup", "put_bottles_into_dustbin", "stack_blocks"}
TASK_CLASS_REJECT_KEYWORDS = {
    "align",
    "deposit",
    "insert",
    "open",
    "plug",
    "precision",
    "screw",
}
HIGH_RISK_TARGET_CATEGORIES = {
    "card",
    "coin",
    "earbuds",
    "key",
    "paper",
    "scissors",
    "thread",
}
PREFERRED_TARGET_CATEGORIES = {
    "can": 30,
    "cup": 24,
    "bottle": 22,
    "box": 20,
    "bowl": 18,
    "block": 16,
    "dice": 12,
}

CATEGORY_LABELS = {
    "can": [
        "\u7ea2\u8272\u6613\u62c9\u7f50:dof",
        "\u6613\u62c9\u7f50:dof",
        "can:dof",
        "target can:dof",
    ],
    "cup": ["\u676f\u5b50:dof", "cup:dof", "target cup:dof"],
    "bottle": ["\u74f6\u5b50:dof", "bottle:dof", "target bottle:dof"],
    "box": ["\u76d2\u5b50:dof", "box:dof", "target box:dof"],
    "bowl": ["\u7897:dof", "bowl:dof", "target bowl:dof"],
    "block": ["\u79ef\u6728:dof", "block:dof", "target block:dof"],
    "dice": ["\u9ab0\u5b50:dof", "dice:dof", "target dice:dof"],
}

PLACE_LABELS = {
    "dustbin": ["\u5783\u573e\u6876", "dustbin", "trash bin"],
    "box": ["\u76d2\u5b50", "box"],
    "bowl": ["\u7897", "bowl"],
    "block": ["\u79ef\u6728:dof", "block:dof", "target block:dof"],
}

_ASSET_VISUAL_LABEL_CACHE: dict[str, list[str]] = {}


@dataclass(frozen=True)
class RobodojoPoolItem:
    task_id: str
    task_class: str
    prompt: str
    tags: list[str]
    suite_path: str
    scene_path: str
    target_asset: dict[str, Any]
    target_import: dict[str, Any]
    success: dict[str, Any]
    admission: dict[str, Any]
    binding: dict[str, Any]
    score: float
    tier: int = 3
    place_asset: dict[str, Any] = field(default_factory=dict)
    place_import: dict[str, Any] = field(default_factory=dict)
    subtask: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RobodojoAutoResult:
    run_dir: str
    selected_task: dict[str, Any]
    task_path: str
    generated: dict[str, Any] | None
    package: dict[str, Any] | None
    suite_path: str | None
    suite_run: dict[str, Any] | None
    skill_candidate_artifacts: dict[str, str] | None
    stage_state_artifacts: dict[str, Any] | None
    artifacts: dict[str, Any]
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RobodojoStagedAutoResult:
    run_dir: str
    selected_task: dict[str, Any]
    skill_candidate_artifacts: dict[str, str]
    staged_experiment: dict[str, Any]
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_robodojo_auto(
    *,
    config: ManagerConfig,
    output_root: str | Path | None = None,
    task_class: str | None = None,
    tier: int = 3,
    max_scenes: int = 240,
    candidate_prefix: str = "robodojo_auto",
    execute: bool = False,
    publish: bool = True,
    reset_before_execute: bool = True,
    llm_overrides: dict[str, Any] | None = None,
    llm_max_attempts: int = 2,
    capture_artifacts: bool = True,
    diagnostic_stages: bool = False,
    preferred_task_id: str | None = None,
    primary_pick_label: str | None = None,
    primary_place_label: str | None = None,
) -> RobodojoAutoResult:
    registry = build_registry(config)
    tier_value = int(tier)
    default_name = "robodojo_auto_tier4" if tier_value >= 4 else "robodojo_auto_tier123"
    tier_name = "tier4" if tier_value >= 4 else "tier123"
    root = Path(output_root).expanduser().resolve() if output_root else config.root_dir / "experiments" / default_name
    run_dir = new_run_dir(root / "runs", safe_id(f"{candidate_prefix}_{tier_name}"))
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    pool = discover_task_pool(
        config=config,
        registry=registry,
        task_class=task_class,
        tier=tier_value,
        max_scenes=max_scenes,
    )
    if not pool:
        raise RuntimeError("no admissible RoboDojo task found for current KSM registry and filters")
    selected = _select_pool_item(pool, preferred_task_id=preferred_task_id)
    selected = _with_binding_label_overrides(
        selected,
        primary_pick_label=primary_pick_label,
        primary_place_label=primary_place_label,
    )
    scene_alignment = _scene_alignment_report(config=config, selected=selected, enabled=execute)
    if scene_alignment.get("status") == "mismatch":
        raise RuntimeError(
            "WebUI scene does not match selected RoboDojo task: "
            f"expected={scene_alignment.get('expected_scene_path')} current={scene_alignment.get('current_scene_path')}"
        )
    preflight_layout_reset = _reset_webui_layout_for_preflight(config=config, enabled=execute)
    grounding_preflight = _grounding_preflight(config=config, selected=selected, enabled=execute)
    selected_pick_label = str(grounding_preflight.get("selected_pick_label") or "").strip()
    if selected_pick_label and selected_pick_label != str(selected.binding.get("primary_pick_label") or "").strip():
        selected = _with_binding_label_overrides(selected, primary_pick_label=selected_pick_label, primary_place_label=None)
    if grounding_preflight.get("status") == "failed":
        raise RuntimeError(f"grounding preflight failed for task {selected.task_id}: no candidate pick label grounded")
    write_json(
        run_dir / "selection_report.json",
        {
            "selected": selected.to_dict(),
            "selection_overrides": {
                "preferred_task_id": preferred_task_id,
                "primary_pick_label": primary_pick_label,
                "primary_place_label": primary_place_label,
            },
            "scene_alignment": scene_alignment,
            "preflight_layout_reset": preflight_layout_reset,
            "grounding_preflight": grounding_preflight,
            "pool": [item.to_dict() for item in pool[:50]],
        },
    )

    task_path = write_selected_task(run_dir / "task.yaml", selected)
    before_frames = capture_keyframes(config, artifacts_dir / "frames_before", enabled=capture_artifacts)

    candidate_id = safe_id(f"{candidate_prefix}_{selected.task_id}")
    generated = generate_skill_from_task_llm(
        task_path=task_path,
        candidate_id=candidate_id,
        output_dir=run_dir / "generated" / candidate_id,
        registry=registry,
        llm_config=config.llm,
        llm_overrides=llm_overrides,
        max_attempts=llm_max_attempts,
        prompt_override=build_robodojo_prompt(selected=selected, candidate_id=candidate_id, registry=registry),
    )
    candidate_arg_preflight = _candidate_arg_preflight(
        config=config,
        selected=selected,
        generated=generated,
        enabled=execute,
    )
    if isinstance(candidate_arg_preflight.get("skill_args"), dict):
        generated.metadata["skill_args"] = dict(candidate_arg_preflight["skill_args"])
        _sync_generated_skill_arg_defaults(generated=generated, skill_args=dict(candidate_arg_preflight["skill_args"]))
    write_json(run_dir / "candidate_arg_preflight.json", candidate_arg_preflight)
    package = package_skill_candidate(
        candidate_id=generated.candidate_id,
        skill_yaml=generated.local_path,
        output_root=run_dir / "packages",
        registry=registry,
        hypothesis=str(generated.metadata.get("hypothesis") or "RoboDojo auto-generated KW skill."),
        change_summary=str(generated.metadata.get("change_summary") or "Generated from RoboDojo metadata and KW registry."),
        expected_failure_modes=list(generated.metadata.get("expected_failure_modes") or []),
        skill_args=dict(generated.metadata.get("skill_args") or {}),
        metadata=_robodojo_candidate_metadata(
            generated=generated,
            selected=selected,
            artifacts_dir=artifacts_dir if capture_artifacts else None,
        ),
        overwrite=True,
    )
    candidate_manifest = _load_candidate_manifest(package)
    skill_candidate_payload: dict[str, Any] | None = None
    skill_candidate_paths: dict[str, str] | None = None
    if diagnostic_stages:
        skill_candidate_payload = build_skill_candidate_artifacts(
            selected_task=selected.to_dict(),
            registry=registry,
            generated_manifest=candidate_manifest,
        )
        skill_candidate_paths = write_skill_candidate_artifacts(run_dir / "skill_candidates", skill_candidate_payload)
    suite_path = write_suite(run_dir / "suite.yaml", selected=selected, task_path=task_path, package=package)
    sampler = PeriodicKeyframeSampler(config=config, output_dir=artifacts_dir / "frames_during", enabled=capture_artifacts and execute)
    sampler.start()
    try:
        suite_run = run_suite(
            config=config,
            suite_path=suite_path,
            execute=execute,
            publish=publish,
            reset_before_execute=reset_before_execute,
        )
    finally:
        sampler.stop()
    after_frames = capture_keyframes(config, artifacts_dir / "frames_after", enabled=capture_artifacts)

    result = RobodojoAutoResult(
        run_dir=str(run_dir),
        selected_task=selected.to_dict(),
        task_path=str(task_path),
        generated=generated.to_dict(),
        package=package.to_dict(),
        suite_path=str(suite_path),
        suite_run=suite_run.to_dict(),
        skill_candidate_artifacts=skill_candidate_paths,
        stage_state_artifacts=None,
        artifacts={
            "frames_before": before_frames,
            "frames_after": after_frames,
            "candidate_arg_preflight": str(run_dir / "candidate_arg_preflight.json"),
            "artifacts_dir": str(artifacts_dir),
        },
        success=bool(suite_run.success),
    )
    payload = result.to_dict()
    write_json(run_dir / "robodojo_auto_report.json", payload)
    (run_dir / "README.md").write_text(render_report_markdown(payload), encoding="utf-8")
    return result


def run_robodojo_staged_auto(
    *,
    config: ManagerConfig,
    output_root: str | Path | None = None,
    task_class: str | None = "put_bottles_into_dustbin",
    tier: int = 4,
    max_scenes: int = 240,
    candidate_prefix: str = "robodojo_staged",
    execute: bool = False,
    publish: bool = True,
    capture_artifacts: bool = True,
    stage_ids: list[str] | None = None,
    stop_after_stage: str | None = "pick_bottle",
) -> RobodojoStagedAutoResult:
    registry = build_registry(config)
    root = Path(output_root).expanduser().resolve() if output_root else config.root_dir / "experiments" / "robodojo_staged"
    pool = discover_task_pool(
        config=config,
        registry=registry,
        task_class=task_class,
        tier=int(tier),
        max_scenes=max_scenes,
    )
    if not pool:
        raise RuntimeError("no admissible RoboDojo task found for staged experiment")
    selected = pool[0]
    skill_candidate_payload = build_skill_candidate_artifacts(
        selected_task=selected.to_dict(),
        registry=registry,
        generated_manifest=None,
    )
    staged: StagedExperimentResult = run_staged_experiment(
        config=config,
        skill_candidate_artifacts=skill_candidate_payload,
        output_root=root,
        stage_ids=stage_ids,
        stop_after_stage=stop_after_stage,
        candidate_prefix=candidate_prefix,
        execute=execute,
        publish=publish,
        capture_artifacts=capture_artifacts,
        registry=registry,
    )
    run_dir = Path(staged.run_dir)
    write_json(run_dir / "selection_report.json", {"selected": selected.to_dict(), "pool": [item.to_dict() for item in pool[:50]]})
    skill_candidate_paths = write_skill_candidate_artifacts(run_dir / "skill_candidates", skill_candidate_payload)
    result = RobodojoStagedAutoResult(
        run_dir=str(run_dir),
        selected_task=selected.to_dict(),
        skill_candidate_artifacts=skill_candidate_paths,
        staged_experiment=staged.to_dict(),
        success=bool(staged.success),
    )
    payload = result.to_dict()
    write_json(run_dir / "robodojo_staged_auto_report.json", payload)
    (run_dir / "README.md").write_text(render_staged_auto_report_markdown(payload), encoding="utf-8")
    return result


def discover_task_pool(
    *,
    config: ManagerConfig,
    registry: ToolRegistry,
    task_class: str | None = None,
    tier: int = 3,
    max_scenes: int = 240,
) -> list[RobodojoPoolItem]:
    if task_class:
        classes = [task_class]
    elif int(tier) >= 4:
        classes = ["put_bottles_into_dustbin", "stack_blocks"]
    else:
        classes = ["general_pickup"]
    items: list[RobodojoPoolItem] = []
    for class_name in classes:
        suite_dir = config.kw_repo / "tasks" / "robodojo" / class_name
        if not suite_dir.exists():
            continue
        for suite_path in sorted(suite_dir.glob("*.suite.yaml"))[: max(1, int(max_scenes))]:
            loaded = _pool_item_from_suite(
                config=config,
                registry=registry,
                suite_path=suite_path,
                task_class=class_name,
                tier=int(tier),
            )
            if isinstance(loaded, list):
                items.extend(item for item in loaded if item.admission.get("accepted"))
            elif loaded and loaded.admission.get("accepted"):
                items.append(loaded)
    return sorted(items, key=lambda item: (-item.score, item.task_id))


def _select_pool_item(pool: list[RobodojoPoolItem], *, preferred_task_id: str | None = None) -> RobodojoPoolItem:
    if not pool:
        raise RuntimeError("no admissible RoboDojo task found for current KSM registry and filters")
    requested = safe_id(str(preferred_task_id or "").strip())
    if not requested:
        return pool[0]
    for item in pool:
        if item.task_id == requested:
            return item
    available = ", ".join(item.task_id for item in pool[:10])
    raise RuntimeError(f"preferred RoboDojo task not found: {requested}; available examples: {available}")


def _with_binding_label_overrides(
    item: RobodojoPoolItem,
    *,
    primary_pick_label: str | None = None,
    primary_place_label: str | None = None,
) -> RobodojoPoolItem:
    pick_label = str(primary_pick_label or "").strip()
    place_label = str(primary_place_label or "").strip()
    if not pick_label and not place_label:
        return item

    binding = dict(item.binding)
    if pick_label:
        binding["primary_pick_label"] = pick_label
        binding["candidate_pick_labels"] = _dedupe([pick_label] + list(binding.get("candidate_pick_labels") or []))
        hints = dict(binding.get("asset_visual_hints") or {})
        hints["source"] = "operator_or_visual_probe"
        hints["preferred_labels"] = _dedupe([pick_label] + list(hints.get("preferred_labels") or []))
        binding["asset_visual_hints"] = hints

    if place_label:
        binding["primary_place_label"] = place_label
        binding["candidate_place_labels"] = _dedupe([place_label] + list(binding.get("candidate_place_labels") or []))

    return replace(item, binding=binding)


def _reset_webui_layout_for_preflight(*, config: ManagerConfig, enabled: bool) -> dict[str, Any]:
    report: dict[str, Any] = {"enabled": bool(enabled), "status": "skipped" if not enabled else "ok"}
    if not enabled:
        return report
    if config.pipeline.mode != "direct":
        return {"enabled": True, "status": "skipped", "reason": f"unsupported_pipeline_mode={config.pipeline.mode}"}
    try:
        reset = PipelineDirectClient(config.pipeline.base_url, timeout_s=12.0).reset_layout(target="manifest", timeout_s=8.0)
    except Exception as exc:
        return {"enabled": True, "status": "failed", "error": repr(exc)}
    status = "ok" if bool(reset.get("ok", False)) else "failed"
    return {"enabled": True, "status": status, "reset": reset}


def _scene_alignment_report(*, config: ManagerConfig, selected: RobodojoPoolItem, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "status": "skipped"}
    try:
        output = subprocess.check_output(["ps", "-eo", "args"], text=True, timeout=2.0)
    except Exception as exc:
        return {"enabled": True, "status": "unknown", "reason": f"process_scan_failed: {exc!r}"}
    webui_port = _webui_port_from_config(config)
    return _scene_alignment_from_process_args(
        expected_scene_path=selected.scene_path,
        process_args=output.splitlines(),
        webui_port=webui_port,
    )


def _scene_alignment_from_process_args(
    *,
    expected_scene_path: str,
    process_args: list[str],
    webui_port: int | None,
) -> dict[str, Any]:
    expected = Path(expected_scene_path).expanduser().resolve()
    candidates: list[dict[str, Any]] = []
    for raw in process_args:
        if "--manifest-path" not in raw or "main.py" not in raw:
            continue
        try:
            parts = shlex.split(raw)
        except ValueError:
            continue
        manifest = _arg_value(parts, "--manifest-path")
        if not manifest:
            continue
        port = _arg_value(parts, "--web-viewer-port")
        if webui_port is not None and port and str(port) != str(webui_port):
            continue
        current = Path(manifest).expanduser().resolve()
        candidates.append(
            {
                "current_scene_path": str(current),
                "webui_port": int(port) if str(port or "").isdigit() else port,
                "process": raw,
            }
        )
    if not candidates:
        return {
            "enabled": True,
            "status": "unknown",
            "reason": "webui_process_not_found",
            "expected_scene_path": str(expected),
            "webui_port": webui_port,
        }
    current = Path(str(candidates[0]["current_scene_path"]))
    return {
        "enabled": True,
        "status": "ok" if current == expected else "mismatch",
        "expected_scene_path": str(expected),
        "current_scene_path": str(current),
        "webui_port": candidates[0].get("webui_port"),
        "matches": candidates,
    }


def _webui_port_from_config(config: ManagerConfig) -> int | None:
    explicit = os.environ.get("KSM_WEBUI_BASE_URL")
    if explicit:
        parsed = urlparse(explicit)
    else:
        parsed = urlparse(config.pipeline.base_url)
    if parsed.port is None:
        return None
    return 8080 if parsed.port == 8000 and not explicit else int(parsed.port)


def _arg_value(parts: list[str], flag: str) -> str | None:
    for index, part in enumerate(parts):
        if part == flag and index + 1 < len(parts):
            return parts[index + 1]
        if part.startswith(f"{flag}="):
            return part.split("=", 1)[1]
    return None



def _candidate_arg_preflight(
    *,
    config: ManagerConfig,
    selected: RobodojoPoolItem,
    generated: Any,
    enabled: bool,
) -> dict[str, Any]:
    original_args = dict(generated.metadata.get("skill_args") or {}) if isinstance(generated.metadata.get("skill_args"), dict) else {}
    report: dict[str, Any] = {
        "enabled": bool(enabled),
        "status": "skipped" if not enabled else "ok",
        "original_skill_args": original_args,
        "skill_args": dict(original_args),
        "checks": {},
        "overrides": {},
    }
    if not enabled:
        return report
    if config.pipeline.mode != "direct":
        report["status"] = "skipped"
        report["reason"] = f"unsupported_pipeline_mode={config.pipeline.mode}"
        return report

    client = PipelineDirectClient(config.pipeline.base_url, timeout_s=12.0)
    robodojo = {"subtask": selected.subtask, "binding": selected.binding}
    if apply_stateful_plan_preflight(report=report, robodojo=robodojo, skill_args=original_args, client=client):
        _finalize_preflight_status(report, override_status="overrode_unverified_candidate_args")
        return report

    pick_report = _validate_generated_label_arg(
        client=client,
        current_label=original_args.get("pick_label"),
        target_position=_position(selected.target_import),
        fallback_labels=_grounding_candidate_labels(selected)[0],
        target_ref=str(selected.target_asset.get("id") or selected.binding.get("target_ref") or ""),
    )
    report["checks"]["pick_label"] = pick_report
    if pick_report.get("selected_label") and pick_report.get("selected_label") != original_args.get("pick_label"):
        report["skill_args"]["pick_label"] = pick_report["selected_label"]
        report["overrides"]["pick_label"] = {
            "from": original_args.get("pick_label"),
            "to": pick_report["selected_label"],
            "reason": pick_report.get("reason"),
        }

    place_position = _position(selected.place_import)
    place_labels = _dedupe(
        [original_args.get("place_label"), selected.binding.get("primary_place_label")]
        + list(selected.binding.get("candidate_place_labels") or [])
    )
    if place_position is not None and place_labels:
        place_report = _validate_generated_label_arg(
            client=client,
            current_label=original_args.get("place_label"),
            target_position=place_position,
            fallback_labels=place_labels,
            target_ref=str(selected.place_asset.get("id") or selected.binding.get("place_ref") or selected.binding.get("support_ref") or ""),
        )
        report["checks"]["place_label"] = place_report
        if place_report.get("selected_label") and place_report.get("selected_label") != original_args.get("place_label"):
            report["skill_args"]["place_label"] = place_report["selected_label"]
            report["overrides"]["place_label"] = {
                "from": original_args.get("place_label"),
                "to": place_report["selected_label"],
                "reason": place_report.get("reason"),
            }
    else:
        report["checks"]["place_label"] = {"status": "skipped", "reason": "missing_place_position_or_labels"}

    _finalize_preflight_status(report, override_status="overrode_unverified_candidate_args")
    return report


def _validate_generated_label_arg(
    *,
    client: PipelineDirectClient,
    current_label: Any,
    target_position: tuple[float, float, float] | None,
    fallback_labels: list[str],
    target_ref: str,
) -> dict[str, Any]:
    current = str(current_label or "").strip()
    labels = _dedupe(([current] if current else []) + fallback_labels)
    attempts = [_grounding_attempt(client=client, label=label, target_position=target_position) for label in labels[:10]]
    current_attempt = next((attempt for attempt in attempts if attempt.get("label") == current), None)
    selected_attempt = _choose_grounded_pick_label(attempts)
    current_ok = _grounding_attempt_matches_target(current_attempt)
    selected_ok = _grounding_attempt_matches_target(selected_attempt)
    selected_label = current if current_ok else (selected_attempt.get("label") if selected_ok and selected_attempt else current)
    if current_ok:
        status = "ok"
        reason = "candidate_arg_grounded_near_target"
    elif selected_ok and selected_attempt:
        status = "overrode"
        reason = "candidate_arg_not_grounded_near_target"
    else:
        status = "failed"
        reason = "no_label_grounded_near_target"
    return {
        "status": status,
        "target_ref": target_ref,
        "target_position": list(target_position) if target_position else None,
        "current_label": current,
        "selected_label": selected_label,
        "reason": reason,
        "attempts": attempts,
    }


def _grounding_attempt_matches_target(attempt: dict[str, Any] | None, *, max_xy_distance_m: float = 0.12) -> bool:
    if not attempt or not attempt.get("success"):
        return False
    distance = attempt.get("xy_distance_m")
    if isinstance(distance, (int, float)):
        return float(distance) <= max_xy_distance_m
    return True


def _sync_generated_skill_arg_defaults(*, generated: Any, skill_args: dict[str, Any]) -> None:
    skill = read_yaml(generated.local_path)
    if not isinstance(skill, dict):
        return
    args = skill.get("args")
    if not isinstance(args, dict):
        return
    changed = False
    for key, value in skill_args.items():
        if key in args and args.get(key) != value:
            args[key] = value
            changed = True
    if changed:
        generated.skill["args"] = args
        write_yaml(generated.local_path, skill)


def _grounding_preflight(*, config: ManagerConfig, selected: RobodojoPoolItem, enabled: bool) -> dict[str, Any]:
    labels, excluded_scene_ref_labels = _grounding_candidate_labels(selected)
    if not enabled:
        return {
            "enabled": False,
            "status": "skipped",
            "candidate_pick_labels": labels,
            "excluded_scene_ref_labels": excluded_scene_ref_labels,
        }
    if config.pipeline.mode != "direct":
        return {
            "enabled": True,
            "status": "skipped",
            "reason": f"unsupported_pipeline_mode={config.pipeline.mode}",
            "candidate_pick_labels": labels,
            "excluded_scene_ref_labels": excluded_scene_ref_labels,
        }
    if not labels:
        return {
            "enabled": True,
            "status": "failed",
            "reason": "no_candidate_pick_labels",
            "excluded_scene_ref_labels": excluded_scene_ref_labels,
            "attempts": [],
        }

    client = PipelineDirectClient(config.pipeline.base_url, timeout_s=12.0)
    target_position = _position(selected.target_import)
    attempts = [_grounding_attempt(client=client, label=label, target_position=target_position) for label in labels[:8]]
    selected_attempt = _choose_grounded_pick_label(attempts)
    status = "ok" if selected_attempt else "failed"
    return {
        "enabled": True,
        "status": status,
        "source": "pipeline.reasoning.qwen_xquat",
        "target_ref": selected.target_asset.get("id"),
        "target_position": list(target_position) if target_position else None,
        "candidate_pick_labels": labels,
        "excluded_scene_ref_labels": excluded_scene_ref_labels,
        "selected_pick_label": selected_attempt.get("label") if selected_attempt else None,
        "selected_attempt": selected_attempt,
        "attempts": attempts,
    }


def _grounding_candidate_labels(selected: RobodojoPoolItem) -> tuple[list[str], list[str]]:
    raw_labels = _dedupe(
        list(selected.binding.get("candidate_pick_labels") or [])
        + list((selected.binding.get("asset_visual_hints") or {}).get("preferred_labels") or [])
        + [selected.binding.get("primary_pick_label")]
    )
    internal_refs = {
        str(selected.target_asset.get("id") or "").strip(),
        str(selected.binding.get("target_ref") or "").strip(),
        str(selected.subtask.get("source_object") or "").strip(),
    }
    labels: list[str] = []
    excluded: list[str] = []
    for label in raw_labels:
        if label in internal_refs:
            excluded.append(label)
        else:
            labels.append(label)
    return labels, excluded


def _grounding_attempt(
    *,
    client: PipelineDirectClient,
    label: str,
    target_position: tuple[float, float, float] | None,
) -> dict[str, Any]:
    kwargs = {"text": [label], "offsets": [[0, 0, 0.07]]}
    attempt: dict[str, Any] = {"label": label, "success": False, "kwargs": kwargs}
    try:
        response = client.run_reasoning("qwen_xquat", kwargs)
    except Exception as exc:
        attempt["error"] = repr(exc)
        return attempt
    parsed = _parse_reasoning_result(response)
    attempt["response_ok"] = bool(response.get("ok", True)) if isinstance(response, dict) else None
    attempt["status"] = parsed.get("status")
    attempt["xyz"] = parsed.get("xyz")
    attempt["success"] = bool(parsed.get("success"))
    if target_position is not None and isinstance(parsed.get("xyz"), list) and len(parsed["xyz"]) >= 2:
        attempt["xy_distance_m"] = ((float(parsed["xyz"][0]) - target_position[0]) ** 2 + (float(parsed["xyz"][1]) - target_position[1]) ** 2) ** 0.5
    return attempt


def _parse_reasoning_result(response: dict[str, Any]) -> dict[str, Any]:
    payload: Any = response
    if isinstance(response.get("response"), dict):
        payload = response["response"]
    raw_result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(raw_result, str):
        try:
            raw_result = ast.literal_eval(raw_result)
        except Exception:
            return {"success": False, "status": "parse_failed"}
    if not isinstance(raw_result, dict):
        return {"success": False, "status": "missing_result"}
    statuses = raw_result.get("status")
    xquats = raw_result.get("xquats")
    status = statuses[0] if isinstance(statuses, list) and statuses else None
    xyz = None
    if isinstance(xquats, list) and xquats and isinstance(xquats[0], list) and xquats[0]:
        first = xquats[0][0]
        if isinstance(first, list) and len(first) >= 3:
            xyz = [float(first[0]), float(first[1]), float(first[2])]
    return {"success": status == "Success" and xyz is not None, "status": status, "xyz": xyz}


def _choose_grounded_pick_label(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    return choose_grounded_label(attempts)


def _dedupe(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def write_selected_task(path: str | Path, selected: RobodojoPoolItem) -> Path:
    success_all = selected.success.get("all_of") if isinstance(selected.success, dict) else None
    predicates = success_all if isinstance(success_all, list) else []
    task = {
        "task_id": selected.task_id,
        "description": f"RoboDojo auto-selected task {selected.task_id}: {selected.prompt}",
        "predicates": predicates,
        "robodojo": {
            "tier_scope": "tier1_task_selection_tier2_object_binding_tier3_yaml_design",
            "generation_mode": "full_task_primary",
            "staged_guide_policy": "disabled_by_default_diagnostic_only",
            "tier": selected.tier,
            "task_class": selected.task_class,
            "suite": selected.suite_path,
            "scene": selected.scene_path,
            "prompt": selected.prompt,
            "target_object": selected.target_asset.get("id"),
            "target_category": selected.target_asset.get("category"),
            "target_asset": selected.target_asset,
            "target_import": selected.target_import,
            "place_asset": selected.place_asset,
            "place_import": selected.place_import,
            "subtask": selected.subtask,
            "success": success_all or selected.success,
            "admission": selected.admission,
            "binding": selected.binding,
        },
    }
    return write_yaml(path, task)


def write_suite(path: str | Path, *, selected: RobodojoPoolItem, task_path: Path, package: CandidatePackage) -> Path:
    suite = {
        "suite_id": safe_id(f"robodojo_auto_{selected.task_id}_tier{selected.tier}"),
        "description": f"KSM RoboDojo Tier {selected.tier} auto-selection, auto-binding, auto-YAML smoke.",
        "output_root": str(Path(path).parent / "suite_runs"),
        "publish_subdir": safe_id(f"robodojo_auto_{selected.task_id}_tier{selected.tier}"),
        "success_threshold": 1.0,
        "tasks": [{"task_path": str(task_path)}],
        "candidate_packages": [package.package_dir],
    }
    return write_yaml(path, suite)


def build_robodojo_prompt(*, selected: RobodojoPoolItem, candidate_id: str, registry: ToolRegistry) -> str:
    prompt_skills, excluded_history_count = _stable_registry_skills_for_prompt(registry)
    registry_payload = {
        "test_skill_dir": registry.test_skill_dir,
        "ctrl": registry.ctrl,
        "info": registry.info,
        "reasoning": _public_reasoning_names(registry.reasoning),
        "capability_counts": registry.capability_counts,
        "history_isolation": {
            "enabled": True,
            "excluded_experiment_skill_count": excluded_history_count,
            "excluded_prefixes": [_registry_test_skill_prefix(registry.test_skill_dir)],
            "reason": "Full-task RoboDojo generation must not learn from older KSM/ASPIRE experiment candidates.",
        },
        "skills": prompt_skills,
    }
    task_payload = {
        "task_id": selected.task_id,
        "task_class": selected.task_class,
        "tier": selected.tier,
        "prompt": selected.prompt,
        "target_asset": selected.target_asset,
        "target_import": selected.target_import,
        "place_asset": selected.place_asset,
        "place_import": selected.place_import,
        "subtask": selected.subtask,
        "success": selected.success,
        "admission": selected.admission,
        "binding": selected.binding,
    }
    workflow_hints = ""
    stateful_plan = selected.subtask.get("stateful_plan") if isinstance(selected.subtask, dict) else None
    if selected.task_class == "stack_blocks" and isinstance(stateful_plan, dict) and stateful_plan.get("steps"):
        workflow_hints = """
Stateful repeated-stack context:
- The selected RoboDojo task is a repeated binary-relation plan, not a single pair wrapper.
- The stateful_plan.steps list is the contract. Generate one stack operation per step, in order.
- Use current_top semantics: after step i succeeds, the source object from step i becomes the support/current_top for step i+1.
- Expose and use the arg names declared in each step.arg_bindings, such as pick_label_1/place_label_1.
- A one-action wrapper around semantic_pickplace is insufficient for this task and should be rejected.
- It is acceptable to compose stable KW pick/place skills; the new value is the reusable repeated-stack interface and state update order.
- Preserve visual labels from the plan. Do not use scene asset ids as visual labels unless they are only retained as fallbacks.
- The observable effects are the per-step stacked(top, bottom) predicates in stateful_plan.success.
"""
    elif selected.task_class == "stack_blocks":
        workflow_hints = """
Stack-object context:
- The selected RoboDojo task has been reduced to one reusable subtask: stack one source object on one support object.
- The intended candidate family is stack_object_on_object(source_label, support_label), not the full three-block tower.
- Decide whether the registry already has a stable generic block stacking skill. Category-specific stack skills should not be treated as generic unless their interface and rationale support it.
- If no generic stack skill exists, this is a YAML-level new subskill candidate that may compose existing KW pick/place skills.
- Preserve the source/support binding labels. Do not hard-code scene asset ids as visual labels.
- The observable task effect is stacked(source_ref, support_ref).
"""
    elif selected.tier >= 4:
        workflow_hints = """
Full-task object-to-container context:
- The selected binding describes one source object and one target container from the RoboDojo scene.
- Decide whether the complete task is expressible with existing KW skill(s), or whether a missing native subskill blocks it.
- Do not decompose into a fixed staged guide unless the generated YAML genuinely needs multiple existing KW calls.
- Do not expand to every repeated object instance unless the task explicitly asks for all instances and bindings are available.
"""
    return f"""
You are KSM generating one KW YAML candidate for a RoboDojo task.

Return exactly one JSON object, no Markdown, with these fields:
- candidate_id: string, exactly "{candidate_id}"
- hypothesis: string
- change_summary: string
- expected_failure_modes: list of strings
- skill_reuse_decision: object with:
  - decision: "reuse_existing_skill", "composition_skill_candidate", "new_yaml_subskill_candidate", or "blocked_by_missing_low_level_primitive"
  - candidate_role: "reuse_existing_skill", "skill_specialization", "new_behavior_skill", or "blocked_by_gap"
  - reusable_interface: object with name, args, expected_effects, observable_success, and failure_modes when candidate_role is skill_specialization or new_behavior_skill
  - added_behavior_contract: object describing added constraints, checks, or task-family semantics when candidate_role is skill_specialization or new_behavior_skill
  - selected_existing_skills: list of KW skill paths used as the main mechanism
  - rationale: short reason based on the task and registry
- skill_args: object
- skill_yaml: string containing the full YAML skill

This is a full-task RoboDojo pressure test:
- Tier 1 task admission has already selected the task from metadata. Respect rejected-risk notes.
- Tier 2 object binding has produced candidate visual labels. Use the primary label unless a fallback is clearly safer.
- YAML design is your job. Choose the workflow from the KW registry; do not rely on a hand-written staged template.
{workflow_hints}

Rules:
- Generate a KW YAML workflow only, not Python.
- The YAML must parse with PyYAML and pass KSM policy.
- The YAML root MUST contain a top-level key named workflow whose value is a list.
- Do not use top-level actions, capabilities, uses_reasoning, uses_control, or is_composite fields in skill_yaml.
- Do not use simulator internals, private state, shell commands, Python imports, files, seed-specific branches, or trial hacks.
- Do not invent unavailable primitives. Use only actions in the registry.
- Do not pass nested maps into action args. Use primitive/list args or omit optional structured args.
- Do not pass action args that are not declared by the called subskill. Do not invent planner_config, gripper_open_angle, hidden strategy maps, or pseudo-control fields.
- Do not pass a gripper map into pickplace/semantic_pickplace.yaml; let the called skill use its defaults.
- For general_pickup, the target object should be picked/lifted. If success includes robot_home, add a conservative home action unless the selected high-level skill already handles it.
- For object-to-container tasks, use the selected source/container labels when the chosen workflow needs pick_label/place_label.
- For stack-object tasks, use the selected source/support labels when the chosen workflow needs pick_label/place_label.
- For stateful repeated-stack tasks, the YAML must contain at least one stack/pickplace operation per stateful_plan step, and each step must use its declared pick/place arg names.
- If the YAML only forwards args to an existing skill, mark candidate_role as reuse_existing_skill; this is a baseline reuse candidate, not a new skill candidate.
- If the YAML calls existing skills but defines a reusable task-family interface, constraints, and effect contract, mark candidate_role as skill_specialization.
- If the YAML changes the behavior mechanism because existing skills do not express the key behavior, mark candidate_role as new_behavior_skill.
- For reuse_existing_skill, pass the public args required by the selected stable KW skill interface, including arm_id when that skill declares it.
- Do not set arm-selection or strategy parameters just because previous experiments used them.
- Preserve language grounding: use the selected binding labels, not scene IDs as visual labels.
- If binding.asset_visual_hints.preferred_labels is non-empty, prefer those visually specific labels over generic category labels.
- Preserve KW defaults unless you have a reason to expose or override a parameter. Do not add optional strategy flags, arm-selection overrides, offsets, or delays merely because examples contain them.

Valid skill_yaml shape:
schema_version: 1.0.0
name: {candidate_id}
description: Short description.
args:
  arm_id: 0
  pick_label: "selected label"
  place_label: ""
workflow:
  - action: pickplace/semantic_pickplace.yaml
    args:
      arm_id: "= args.arm_id"
      pick_label: "= args.pick_label"
      place_label: "= args.place_label"

Candidate id: {candidate_id}

Selected RoboDojo task and KSM binding:
{json.dumps(task_payload, ensure_ascii=False, indent=2)}

Available KW tools and skills:
{json.dumps(registry_payload, ensure_ascii=False, indent=2)}
""".strip()


def _stable_registry_skills_for_prompt(registry: ToolRegistry) -> tuple[list[dict[str, Any]], int]:
    skills: list[dict[str, Any]] = []
    excluded = 0
    for skill in registry.skills:
        if _is_experiment_skill_path(skill.path, registry.test_skill_dir):
            excluded += 1
            continue
        skills.append(
            {
                "path": skill.path,
                "description": _prompt_skill_description(skill.description),
                "args": _prompt_skill_args(skill.args),
                "actions": _public_action_names(skill.actions),
                "capabilities": skill.capabilities,
                "uses_reasoning": skill.uses_reasoning,
                "uses_control": skill.uses_control,
                "is_composite": skill.is_composite,
            }
        )
        if len(skills) >= 80:
            break
    return skills, excluded


def _is_experiment_skill_path(path: str, test_skill_dir: str) -> bool:
    normalized = str(path or "").strip("/")
    prefix = _registry_test_skill_prefix(test_skill_dir)
    return bool(prefix and (normalized == prefix or normalized.startswith(f"{prefix}/")))


def _registry_test_skill_prefix(test_skill_dir: str) -> str:
    prefix = str(test_skill_dir or "").strip("/")
    if prefix.startswith("knowin_skills/"):
        prefix = prefix[len("knowin_skills/") :]
    return prefix.strip("/")


def _prompt_skill_args(args: dict[str, Any]) -> dict[str, Any]:
    strategy_defaults = {
        "direct_pick",
        "adjust_arm_id",
        "pick_offset",
        "place_offset",
        "pick_check_offset",
        "delay_sec",
        "use_motion_planning",
    }
    return {str(k): v for k, v in dict(args or {}).items() if str(k) not in strategy_defaults}


def _prompt_skill_description(description: str) -> str:
    text = str(description or "")
    strategy_tokens = (
        "arm_id",
        "direct_pick",
        "adjust_arm_id",
        "pick_offset",
        "place_offset",
        "pick_check_offset",
        "delay_sec",
    )
    if any(token in text for token in strategy_tokens):
        return ""
    return text


def render_report_markdown(payload: dict[str, Any]) -> str:
    selected = payload.get("selected_task") if isinstance(payload.get("selected_task"), dict) else {}
    binding = selected.get("binding") if isinstance(selected.get("binding"), dict) else {}
    suite_run = payload.get("suite_run") if isinstance(payload.get("suite_run"), dict) else {}
    generated = payload.get("generated") if isinstance(payload.get("generated"), dict) else {}
    metadata = generated.get("metadata") if isinstance(generated.get("metadata"), dict) else {}
    reuse = metadata.get("skill_reuse_decision") if isinstance(metadata.get("skill_reuse_decision"), dict) else {}
    return "\n".join(
        [
            "# RoboDojo KSM Full-Task Auto Experiment",
            "",
            f"- Run dir: `{payload.get('run_dir')}`",
            f"- Success: `{payload.get('success')}`",
            f"- Selected task: `{selected.get('task_id')}`",
            f"- Task class: `{selected.get('task_class')}`",
            f"- Tier: `{selected.get('tier')}`",
            f"- Target category: `{selected.get('target_asset', {}).get('category') if isinstance(selected.get('target_asset'), dict) else ''}`",
            f"- Place category: `{selected.get('place_asset', {}).get('category') if isinstance(selected.get('place_asset'), dict) else ''}`",
            f"- Primary pick label: `{binding.get('primary_pick_label')}`",
            f"- Primary place label: `{binding.get('primary_place_label')}`",
            f"- Agent skill decision: `{reuse.get('decision')}`",
            f"- Agent selected skills: `{reuse.get('selected_existing_skills')}`",
            f"- Suite success rate: `{suite_run.get('success_rate')}`",
            f"- Pipeline success rate: `{suite_run.get('pipeline_success_rate')}`",
            f"- Predicate success rate: `{suite_run.get('predicate_success_rate')}`",
            f"- Policy ok rate: `{suite_run.get('policy_ok_rate')}`",
            "",
            "## Artifacts",
            "",
            f"- Generated skill: `{payload.get('generated', {}).get('local_path') if isinstance(payload.get('generated'), dict) else ''}`",
            f"- Candidate package: `{payload.get('package', {}).get('package_dir') if isinstance(payload.get('package'), dict) else ''}`",
            f"- Suite path: `{payload.get('suite_path')}`",
            f"- Frames/artifacts: `{payload.get('artifacts', {}).get('artifacts_dir') if isinstance(payload.get('artifacts'), dict) else ''}`",
            "",
        ]
    )


def render_staged_auto_report_markdown(payload: dict[str, Any]) -> str:
    selected = payload.get("selected_task") if isinstance(payload.get("selected_task"), dict) else {}
    staged = payload.get("staged_experiment") if isinstance(payload.get("staged_experiment"), dict) else {}
    final_state = staged.get("final_state") if isinstance(staged.get("final_state"), dict) else {}
    lines = [
        "# RoboDojo KSM Staged Experiment",
        "",
        f"- Run dir: `{payload.get('run_dir')}`",
        f"- Success: `{payload.get('success')}`",
        f"- Selected task: `{selected.get('task_id')}`",
        f"- Task class: `{selected.get('task_class')}`",
        f"- Final state: `{final_state.get('state_id')}`",
        "",
        "## Stage Results",
        "",
        "| Stage | Status | Success | Next Allowed | Failure |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in staged.get("stage_results", []) or []:
        if isinstance(result, dict):
            lines.append(
                f"| `{result.get('stage_id')}` | `{result.get('stage_status')}` | `{result.get('success')}` | `{result.get('next_stage_allowed')}` | `{result.get('failure_signature')}` |"
            )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Staged experiment report: `{Path(str(payload.get('run_dir'))) / 'staged_experiment_report.json' if payload.get('run_dir') else ''}`",
            f"- Skill candidate artifacts: `{payload.get('skill_candidate_artifacts', {}).get('report') if isinstance(payload.get('skill_candidate_artifacts'), dict) else ''}`",
            "",
        ]
    )
    return "\n".join(lines)


def _load_candidate_manifest(package: CandidatePackage) -> dict[str, Any]:
    try:
        data = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _robodojo_candidate_metadata(*, generated: Any, selected: RobodojoPoolItem, artifacts_dir: Path | None = None) -> dict[str, Any]:
    metadata = {
        "source": "robodojo_full_task_auto",
        "generation_mode": "full_task_primary",
        "staged_guide_policy": "disabled_by_default_diagnostic_only",
        "task_id": selected.task_id,
        "task_class": selected.task_class,
        "tier": selected.tier,
        "binding": selected.binding,
        "skill_reuse_decision": (
            generated.metadata.get("skill_reuse_decision")
            if isinstance(generated.metadata.get("skill_reuse_decision"), dict)
            else {}
        ),
    }
    visual = _robodojo_visual_feedback_request(artifacts_dir)
    if visual:
        metadata["visual_feedback"] = visual
    return metadata


def _robodojo_visual_feedback_request(artifacts_dir: Path | None) -> dict[str, Any]:
    if artifacts_dir is None:
        return {}
    provider = os.environ.get("KSM_VISUAL_FEEDBACK_PROVIDER")
    if not provider and os.environ.get("GEMINI_API_KEY"):
        provider = "gemini"
    if str(provider or "").lower() not in {"gemini", "google-gemini"}:
        return {}
    return {
        "provider": "gemini",
        "model": os.environ.get("KSM_VISUAL_MODEL", "gemini-3.5-flash"),
        "api_key_env": "GEMINI_API_KEY",
        "max_frames": int(os.environ.get("KSM_VISUAL_MAX_FRAMES", "8")),
        "frame_globs": [
            str(artifacts_dir / "frames_before" / "free_left.jpg"),
            str(artifacts_dir / "frames_during" / "*" / "free_left.jpg"),
            str(artifacts_dir / "frames_after" / "free_left.jpg"),
        ],
        "artifacts": {
            "frames_before_dir": str(artifacts_dir / "frames_before"),
            "frames_during_dir": str(artifacts_dir / "frames_during"),
            "frames_after_dir": str(artifacts_dir / "frames_after"),
        },
    }


def _pool_item_from_suite(
    *,
    config: ManagerConfig,
    registry: ToolRegistry,
    suite_path: Path,
    task_class: str,
    tier: int,
) -> RobodojoPoolItem | list[RobodojoPoolItem] | None:
    try:
        suite = read_yaml(suite_path)
        if not isinstance(suite, dict):
            return None
        scene_rel = str(suite.get("scene") or "")
        scene_path = (config.kw_repo / scene_rel).resolve()
        scene = read_yaml(scene_path)
        if not isinstance(scene, dict):
            return None
        tasks = suite.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return None
        task = tasks[0]
        if not isinstance(task, dict):
            return None
        if int(tier) >= 4 and task_class == "put_bottles_into_dustbin":
            return _pool_items_for_put_bottles(
                suite_path=suite_path,
                scene_path=scene_path,
                task=task,
                task_class=task_class,
                scene=scene,
                registry=registry,
                tier=int(tier),
                assets_root=_assets_root(config),
            )
        if int(tier) >= 4 and task_class == "stack_blocks":
            return _pool_items_for_stack_blocks(
                suite_path=suite_path,
                scene_path=scene_path,
                task=task,
                task_class=task_class,
                scene=scene,
                registry=registry,
                tier=int(tier),
                assets_root=_assets_root(config),
            )
        target_asset = _target_asset(scene)
        target_import = _target_import(scene, str(target_asset.get("id") or "target_prop"))
        success = task.get("success") if isinstance(task.get("success"), dict) else {}
        admission = _admit(
            task_class=task_class,
            task=task,
            target_asset=target_asset,
            target_import=target_import,
            success=success,
            registry=registry,
        )
        binding = _binding(target_asset, assets_root=_assets_root(config))
        score = _score(task_class=task_class, target_asset=target_asset, target_import=target_import, admission=admission)
        return RobodojoPoolItem(
            task_id=safe_id(str(task.get("task_id") or suite_path.stem)),
            task_class=task_class,
            prompt=str(task.get("prompt") or ""),
            tags=[str(tag) for tag in task.get("tags", [])] if isinstance(task.get("tags"), list) else [],
            suite_path=str(suite_path),
            scene_path=str(scene_path),
            target_asset=target_asset,
            target_import=target_import,
            success=success,
            admission=admission,
            binding=binding,
            score=score,
            tier=int(tier),
        )
    except Exception:
        return None


def _admit(
    *,
    task_class: str,
    task: dict[str, Any],
    target_asset: dict[str, Any],
    target_import: dict[str, Any],
    success: dict[str, Any],
    registry: ToolRegistry,
) -> dict[str, Any]:
    reasons: list[str] = []
    risk_notes: list[str] = []
    class_text = " ".join([task_class, str(task.get("task_id") or ""), " ".join(str(tag) for tag in task.get("tags", []))]).lower()
    if task_class not in SUPPORTED_TASK_CLASSES:
        reasons.append(f"unsupported task_class={task_class}")
    if any(keyword in class_text for keyword in TASK_CLASS_REJECT_KEYWORDS):
        reasons.append("task class suggests missing precision/open/insert primitive")
    if not target_asset:
        reasons.append("missing target asset metadata")
    if target_asset and not bool(target_asset.get("qualified")):
        reasons.append("target asset qualified=false")
    if target_asset and not bool(target_asset.get("has_collision_prims")):
        reasons.append("target asset has_collision_prims=false")
    if target_asset and str(target_asset.get("collision_mode") or "") not in {"visual_mesh", "convex", "mesh"}:
        risk_notes.append(f"uncommon collision_mode={target_asset.get('collision_mode')}")
    category = str(target_asset.get("category") or "")
    if category in HIGH_RISK_TARGET_CATEGORIES:
        reasons.append(f"target category is high-risk for current primitive set: {category}")
    if "pickplace/semantic_pickplace.yaml" not in registry.skill_paths and "pickplace/semantic_pick.yaml" not in registry.skill_paths:
        reasons.append("KW registry lacks semantic pick/pickplace skill")
    pose = target_import.get("pose") if isinstance(target_import.get("pose"), dict) else {}
    position = pose.get("position") if isinstance(pose.get("position"), list) else []
    if len(position) >= 3:
        x, y, z = [float(v) for v in position[:3]]
        if not (0.25 <= x <= 1.10 and -0.55 <= y <= 0.55 and 0.70 <= z <= 1.05):
            reasons.append(f"target pose outside conservative workspace: {[x, y, z]}")
    else:
        risk_notes.append("missing target pose; cannot score reachability")
    success_items = success.get("all_of") if isinstance(success, dict) else None
    if isinstance(success_items, list):
        success_types = {str(item.get("type")) for item in success_items if isinstance(item, dict)}
        if not {"lift", "robot_home"}.intersection(success_types):
            risk_notes.append(f"success predicates are not a simple pickup/home set: {sorted(success_types)}")
    return {
        "accepted": not reasons,
        "reject_reasons": reasons,
        "risk_notes": risk_notes,
        "checks": {
            "target_qualified": bool(target_asset.get("qualified")),
            "target_has_collision_prims": bool(target_asset.get("has_collision_prims")),
            "semantic_pick_available": "pickplace/semantic_pick.yaml" in registry.skill_paths,
            "semantic_pickplace_available": "pickplace/semantic_pickplace.yaml" in registry.skill_paths,
        },
    }


def _binding(target_asset: dict[str, Any], *, assets_root: Path | None = None) -> dict[str, Any]:
    category = str(target_asset.get("category") or "object").strip()
    visual_labels = _asset_visual_pick_labels(target_asset, assets_root=assets_root)
    labels = visual_labels + list(CATEGORY_LABELS.get(category, [f"{category}:dof", f"target {category}:dof", "target object:dof"]))
    aliases = []
    for value in labels + [str(target_asset.get("id") or ""), category]:
        if value and value not in aliases:
            aliases.append(value)
    return {
        "target_ref": str(target_asset.get("id") or "target_prop"),
        "target_category": category,
        "primary_pick_label": aliases[0],
        "candidate_pick_labels": aliases,
        "grounding_fallback": [
            "try primary visual label with :dof",
            "fall back to category-level label",
            "fall back to target object wording, but do not use scene id as a visual label unless grounding supports it",
        ],
        "asset_visual_hints": {
            "source": "asset_texture" if visual_labels else "",
            "preferred_labels": visual_labels,
        },
    }


def _multi_binding(source_asset: dict[str, Any], place_asset: dict[str, Any], *, assets_root: Path | None = None) -> dict[str, Any]:
    source = _binding(source_asset, assets_root=assets_root)
    place_category = str(place_asset.get("category") or "target").strip()
    place_labels = list(PLACE_LABELS.get(place_category, [place_category, f"target {place_category}"]))
    aliases = []
    for value in place_labels + [str(place_asset.get("id") or ""), place_category]:
        if value and value not in aliases:
            aliases.append(value)
    source.update(
        {
            "place_ref": str(place_asset.get("id") or ""),
            "place_category": place_category,
            "primary_place_label": aliases[0] if aliases else place_category,
            "candidate_place_labels": aliases,
            "sequence_intent": "pick selected source object and place it inside/on selected target container",
        }
    )
    return source


def _stack_binding(source_asset: dict[str, Any], support_asset: dict[str, Any], *, assets_root: Path | None = None) -> dict[str, Any]:
    source = _binding(source_asset, assets_root=assets_root)
    support = _binding(support_asset, assets_root=assets_root)
    support_labels = _dedupe(
        list(support.get("candidate_pick_labels") or [])
        + list(PLACE_LABELS.get(str(support_asset.get("category") or ""), []))
        + [support_asset.get("id"), support_asset.get("category")]
    )
    source.update(
        {
            "place_ref": str(support_asset.get("id") or ""),
            "place_category": str(support_asset.get("category") or "support"),
            "support_ref": str(support_asset.get("id") or ""),
            "support_category": str(support_asset.get("category") or "support"),
            "primary_place_label": support_labels[0] if support_labels else str(support_asset.get("category") or "support"),
            "candidate_place_labels": support_labels,
            "sequence_intent": "pick selected source object and stack it on the selected support object",
        }
    )
    return source


def _assets_root(config: ManagerConfig) -> Path | None:
    candidates = []
    if os.environ.get("KNOWIN_ASSETS_ROOT"):
        candidates.append(Path(os.environ["KNOWIN_ASSETS_ROOT"]))
    candidates.extend([config.kw_repo.parent / "assets", config.kw_repo / "assets"])
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            continue
        if resolved.exists():
            return resolved
    return None


def _asset_visual_pick_labels(target_asset: dict[str, Any], *, assets_root: Path | None) -> list[str]:
    category = str(target_asset.get("category") or "object").strip()
    rel_path = str(target_asset.get("path") or "").strip()
    if not category or not rel_path or assets_root is None:
        return []
    cache_key = f"{assets_root.resolve()}::{rel_path}"
    if cache_key in _ASSET_VISUAL_LABEL_CACHE:
        return list(_ASSET_VISUAL_LABEL_CACHE[cache_key])
    asset_file = (assets_root / rel_path).resolve()
    asset_dir = asset_file.parent if asset_file.suffix else asset_file
    texture_dir = asset_dir / "SubUSDs" / "textures"
    colors = _dominant_texture_colors(texture_dir)
    if not colors:
        _ASSET_VISUAL_LABEL_CACHE[cache_key] = []
        return []
    category_words = {
        "bottle": ("瓶子", "bottle"),
        "can": ("易拉罐", "can"),
        "cup": ("杯子", "cup"),
        "box": ("盒子", "box"),
        "bowl": ("碗", "bowl"),
        "block": ("积木", "block"),
        "dice": ("骰子", "dice"),
    }
    color_words = {
        "pink": ("粉色", "pink"),
        "blue": ("蓝色", "blue"),
        "yellow": ("黄色", "yellow"),
        "green": ("绿色", "green"),
        "orange": ("橙色", "orange"),
        "red": ("红色", "red"),
        "purple": ("紫色", "purple"),
        "white": ("白色", "white"),
    }
    category_cn, category_en = category_words.get(category, ("目标物体", category))
    labels: list[str] = []
    for color in colors[:2]:
        cn, en = color_words.get(color, ("", color))
        if cn:
            labels.append(f"{cn}{category_cn}:dof")
        labels.append(f"{en} {category_en}:dof")
    if len(colors) >= 2 and {"blue", "white"}.issubset(set(colors[:3])):
        labels.insert(0, f"蓝白{category_cn}:dof")
        labels.insert(1, f"blue and white {category_en}:dof")
    deduped: list[str] = []
    for label in labels:
        if label and label not in deduped:
            deduped.append(label)
    _ASSET_VISUAL_LABEL_CACHE[cache_key] = list(deduped)
    return deduped


def _dominant_texture_colors(texture_dir: Path) -> list[str]:
    if not texture_dir.exists():
        return []
    try:
        from PIL import Image
    except Exception:
        return []
    excluded = ("metallic", "roughness", "normal", "opacity", "ao")
    counts: dict[str, float] = {}
    for path in sorted(texture_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        if any(token in path.name.lower() for token in excluded):
            continue
        try:
            with Image.open(path) as raw:
                image = raw.convert("RGBA")
                image.thumbnail((96, 96))
                for r, g, b, a in image.getdata():
                    if a < 16:
                        continue
                    color = _classify_rgb_color(int(r), int(g), int(b))
                    if color:
                        counts[color] = counts.get(color, 0.0) + (0.2 if color == "white" else 1.0)
        except Exception:
            continue
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    non_neutral = [name for name, _count in ranked if name != "white"]
    if non_neutral:
        colors = non_neutral[:2]
        if "white" in counts and "blue" in colors and "white" not in colors:
            colors.insert(1, "white")
        elif "white" in counts and len(colors) == 1:
            colors.append("white")
        return colors
    return [ranked[0][0]]


def _classify_rgb_color(r: int, g: int, b: int) -> str | None:
    max_v = max(r, g, b)
    min_v = min(r, g, b)
    if max_v < 35:
        return None
    if r > 150 and r > g + 8 and r > b + 8 and max_v - min_v < 95:
        return "pink"
    if max_v > 165 and min_v > 145 and max_v - min_v < 55:
        return "white"
    if max_v - min_v < 18:
        return "white" if max_v > 210 else None
    import colorsys

    hue, saturation, value = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if value < 0.2 or saturation < 0.10:
        return None
    degree = hue * 360.0
    if degree < 15 or degree >= 350:
        return "red"
    if degree < 38:
        return "orange"
    if degree < 68:
        return "yellow"
    if degree < 170:
        return "green"
    if degree < 255:
        return "blue"
    if degree < 310:
        return "purple"
    return "pink"


def _pool_items_for_put_bottles(
    *,
    suite_path: Path,
    scene_path: Path,
    task: dict[str, Any],
    task_class: str,
    scene: dict[str, Any],
    registry: ToolRegistry,
    tier: int,
    assets_root: Path | None = None,
) -> list[RobodojoPoolItem]:
    success = task.get("success") if isinstance(task.get("success"), dict) else {}
    refs = _asset_refs(scene)
    imports = _imports_by_id(scene)
    candidates: list[RobodojoPoolItem] = []
    for inside in _inside_success_items(success):
        source_id = str(inside.get("object") or "")
        container_id = str(inside.get("container") or "")
        source_asset = dict(refs.get(source_id) or {})
        place_asset = dict(refs.get(container_id) or {})
        source_import = dict(imports.get(source_id) or {})
        place_import = dict(imports.get(container_id) or {})
        if not source_asset or not place_asset:
            continue
        admission = _admit_tier4_pickplace(
            task=task,
            source_asset=source_asset,
            source_import=source_import,
            place_asset=place_asset,
            place_import=place_import,
            registry=registry,
        )
        subtask = {
            "subtask_id": f"{source_id}_in_{container_id}",
            "success": {"all_of": [inside]},
            "source_object": source_id,
            "target_container": container_id,
        }
        binding = _multi_binding(source_asset, place_asset, assets_root=assets_root)
        candidates.append(
            RobodojoPoolItem(
                task_id=safe_id(f"{task.get('task_id')}_{source_id}_to_{container_id}"),
                task_class=task_class,
                prompt=f"{task.get('prompt') or ''} Simplified Tier 4 subtask: put {source_id} into {container_id}.",
                tags=[str(tag) for tag in task.get("tags", [])] if isinstance(task.get("tags"), list) else [],
                suite_path=str(suite_path),
                scene_path=str(scene_path),
                target_asset=source_asset,
                target_import=source_import,
                place_asset=place_asset,
                place_import=place_import,
                success=subtask["success"],
                admission=admission,
                binding=binding,
                score=_score_tier4(
                    source_asset=source_asset,
                    source_import=source_import,
                    place_asset=place_asset,
                    place_import=place_import,
                    admission=admission,
                )
                + _tier4_binding_execution_bonus(binding=binding, source_import=source_import, place_import=place_import),
                tier=tier,
                subtask=subtask,
            )
        )
    accepted = [item for item in candidates if item.admission.get("accepted")]
    return sorted(accepted, key=lambda item: (-item.score, item.task_id))


def _pool_items_for_stack_blocks(
    *,
    suite_path: Path,
    scene_path: Path,
    task: dict[str, Any],
    task_class: str,
    scene: dict[str, Any],
    registry: ToolRegistry,
    tier: int,
    assets_root: Path | None = None,
) -> list[RobodojoPoolItem]:
    refs = _asset_refs(scene)
    imports = _imports_by_id(scene)
    candidates: list[RobodojoPoolItem] = []
    for stacked in _stacked_success_items(task.get("success") if isinstance(task.get("success"), dict) else {}):
        objects = [str(item) for item in stacked.get("objects") or [] if str(item or "").strip()]
        for source_id in objects:
            for support_id in objects:
                if source_id == support_id:
                    continue
                source_asset = dict(refs.get(source_id) or {})
                support_asset = dict(refs.get(support_id) or {})
                source_import = dict(imports.get(source_id) or {})
                support_import = dict(imports.get(support_id) or {})
                if not source_asset or not support_asset:
                    continue
                admission = _admit_stack_pair(
                    source_asset=source_asset,
                    source_import=source_import,
                    support_asset=support_asset,
                    support_import=support_import,
                    registry=registry,
                )
                subtask = {
                    "subtask_id": f"{source_id}_on_{support_id}",
                    "success": {"all_of": [{"type": "stacked", "top": source_id, "bottom": support_id}]},
                    "source_object": source_id,
                    "support_object": support_id,
                    "target_support": support_id,
                }
                binding = _stack_binding(source_asset, support_asset, assets_root=assets_root)
                candidates.append(
                    RobodojoPoolItem(
                        task_id=safe_id(f"{task.get('task_id')}_{source_id}_on_{support_id}"),
                        task_class=task_class,
                        prompt=f"{task.get('prompt') or ''} Simplified stack subtask: stack {source_id} on {support_id}.",
                        tags=[str(tag) for tag in task.get("tags", [])] if isinstance(task.get("tags"), list) else [],
                        suite_path=str(suite_path),
                        scene_path=str(scene_path),
                        target_asset=source_asset,
                        target_import=source_import,
                        place_asset=support_asset,
                        place_import=support_import,
                        success=subtask["success"],
                        admission=admission,
                        binding=binding,
                        score=_score_stack_pair(
                            source_asset=source_asset,
                            source_import=source_import,
                            support_asset=support_asset,
                            support_import=support_import,
                            admission=admission,
                        )
                        + _tier4_binding_execution_bonus(binding=binding, source_import=source_import, place_import=support_import),
                        tier=tier,
                        subtask=subtask,
                    )
                )
    if int(tier) >= 5:
        candidates.extend(
            _stateful_stack_pool_items(
                suite_path=suite_path,
                scene_path=scene_path,
                task=task,
                task_class=task_class,
                scene=scene,
                registry=registry,
                tier=tier,
                assets_root=assets_root,
            )
        )
    accepted = [item for item in candidates if item.admission.get("accepted")]
    return sorted(accepted, key=lambda item: (-item.score, item.task_id))


def _stateful_stack_pool_items(
    *,
    suite_path: Path,
    scene_path: Path,
    task: dict[str, Any],
    task_class: str,
    scene: dict[str, Any],
    registry: ToolRegistry,
    tier: int,
    assets_root: Path | None = None,
) -> list[RobodojoPoolItem]:
    refs = _asset_refs(scene)
    imports = _imports_by_id(scene)
    candidates: list[RobodojoPoolItem] = []
    for stacked in _stacked_success_items(task.get("success") if isinstance(task.get("success"), dict) else {}):
        objects = [str(item) for item in stacked.get("objects") or [] if str(item or "").strip()]
        if len(objects) < 3:
            continue
        for variant_index, chain in enumerate(
            _choose_stateful_stack_chains(objects=objects, refs=refs, imports=imports, registry=registry, max_variants=6),
            start=1,
        ):
            if len(chain) < 3:
                continue
            admission = _admit_stack_chain(chain=chain, refs=refs, imports=imports, registry=registry)
            steps, binding_skill_args = _stateful_stack_steps(chain=chain, refs=refs, imports=imports, assets_root=assets_root)
            if not steps:
                continue
            success = {"all_of": [dict(step["success_predicate"]) for step in steps]}
            source_id = chain[1]
            base_id = chain[0]
            stateful_plan = {
                "schema": "ksm.robodojo.stateful_plan.v1",
                "plan_type": "repeated_binary_relation",
                "relation": "stacked",
                "state_variables": {"current_top": base_id},
                "object_order": chain,
                "chain_variant_index": variant_index,
                "base_support": base_id,
                "moving_objects": chain[1:],
                "steps": steps,
                "skill_args": binding_skill_args,
                "success": success,
                "original_success": task.get("success") if isinstance(task.get("success"), dict) else {},
            }
            first_binding = _stack_binding(dict(refs[source_id]), dict(refs[base_id]), assets_root=assets_root)
            first_binding.update(
                {
                    "sequence_intent": "build a repeated stack by updating current_top after every successful stack step",
                    "stateful_plan": stateful_plan,
                    "stateful_skill_args": binding_skill_args,
                }
            )
            variant_suffix = "_".join(chain)
            subtask = {
                "subtask_id": f"stateful_stack_{len(chain)}_objects_v{variant_index}",
                "source_objects": chain[1:],
                "base_support": base_id,
                "target_support": base_id,
                "stateful_plan": stateful_plan,
                "success": success,
            }
            candidates.append(
                RobodojoPoolItem(
                    task_id=safe_id(f"{task.get('task_id')}_stateful_stack_{len(chain)}_objects_v{variant_index}_{variant_suffix}"),
                    task_class=task_class,
                    prompt=f"{task.get('prompt') or ''} Stateful repeated stack task: stack all selected objects into one tower.",
                    tags=[str(tag) for tag in task.get("tags", [])] if isinstance(task.get("tags"), list) else [],
                    suite_path=str(suite_path),
                    scene_path=str(scene_path),
                    target_asset=dict(refs[source_id]),
                    target_import=dict(imports[source_id]),
                    place_asset=dict(refs[base_id]),
                    place_import=dict(imports[base_id]),
                    success=success,
                    admission=admission,
                    binding=first_binding,
                    score=_score_stack_chain(chain=chain, refs=refs, imports=imports, registry=registry, admission=admission) - (variant_index - 1) * 0.01,
                    tier=tier,
                    subtask=subtask,
                )
            )
    return candidates


def _choose_stateful_stack_chains(
    *,
    objects: list[str],
    refs: dict[str, dict[str, Any]],
    imports: dict[str, dict[str, Any]],
    registry: ToolRegistry,
    max_variants: int = 6,
) -> list[list[str]]:
    from itertools import permutations

    scored: list[tuple[float, list[str]]] = []
    for candidate in permutations(objects):
        chain = [str(item) for item in candidate]
        if any(item not in refs or item not in imports for item in chain):
            continue
        admission = _admit_stack_chain(chain=chain, refs=refs, imports=imports, registry=registry)
        if not admission.get("accepted"):
            continue
        score = _score_stack_chain(chain=chain, refs=refs, imports=imports, registry=registry, admission=admission)
        scored.append((score, chain))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [chain for _score, chain in scored[: max(1, int(max_variants))]]


def _choose_stateful_stack_chain(
    *,
    objects: list[str],
    refs: dict[str, dict[str, Any]],
    imports: dict[str, dict[str, Any]],
    registry: ToolRegistry,
) -> list[str]:
    chains = _choose_stateful_stack_chains(objects=objects, refs=refs, imports=imports, registry=registry, max_variants=1)
    return chains[0] if chains else []


def _admit_stack_chain(
    *,
    chain: list[str],
    refs: dict[str, dict[str, Any]],
    imports: dict[str, dict[str, Any]],
    registry: ToolRegistry,
) -> dict[str, Any]:
    reasons: list[str] = []
    risk_notes: list[str] = []
    pair_reports: list[dict[str, Any]] = []
    for index in range(1, len(chain)):
        source_id = chain[index]
        support_id = chain[index - 1]
        report = _admit_stack_pair(
            source_asset=dict(refs.get(source_id) or {}),
            source_import=dict(imports.get(source_id) or {}),
            support_asset=dict(refs.get(support_id) or {}),
            support_import=dict(imports.get(support_id) or {}),
            registry=registry,
        )
        pair_reports.append({"source": source_id, "support": support_id, "admission": report})
        reasons.extend(f"step_{index}:{reason}" for reason in report.get("reject_reasons", []))
        risk_notes.extend(f"step_{index}:{note}" for note in report.get("risk_notes", []))
    return {
        "accepted": not reasons,
        "reject_reasons": reasons,
        "risk_notes": risk_notes,
        "checks": {
            "stateful_stack_steps": max(0, len(chain) - 1),
            "semantic_pick_available": "pickplace/semantic_pick.yaml" in registry.skill_paths,
            "semantic_place_available": "pickplace/semantic_place.yaml" in registry.skill_paths,
            "semantic_pickplace_available": "pickplace/semantic_pickplace.yaml" in registry.skill_paths,
        },
        "pair_reports": pair_reports,
    }


def _stateful_stack_steps(
    *,
    chain: list[str],
    refs: dict[str, dict[str, Any]],
    imports: dict[str, dict[str, Any]],
    assets_root: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    skill_args: dict[str, Any] = {"arm_id": 0}
    current_top = chain[0] if chain else ""
    for index, source_id in enumerate(chain[1:], start=1):
        support_id = current_top
        source_binding = _binding(dict(refs.get(source_id) or {}), assets_root=assets_root)
        support_binding = _binding(dict(refs.get(support_id) or {}), assets_root=assets_root)
        support_labels = _dedupe(
            list(support_binding.get("candidate_pick_labels") or [])
            + list(PLACE_LABELS.get(str((refs.get(support_id) or {}).get("category") or ""), []))
            + [support_id, (refs.get(support_id) or {}).get("category")]
        )
        pick_arg = f"pick_label_{index}"
        place_arg = f"place_label_{index}"
        pick_label = str(source_binding.get("primary_pick_label") or "")
        place_label = str((support_labels or [support_id])[0])
        skill_args[pick_arg] = pick_label
        skill_args[place_arg] = place_label
        steps.append(
            {
                "step_id": f"stack_step_{index}",
                "relation": "stacked",
                "source_object": source_id,
                "support_object": support_id,
                "current_top_before": support_id,
                "current_top_after": source_id,
                "source_asset": dict(refs.get(source_id) or {}),
                "support_asset": dict(refs.get(support_id) or {}),
                "source_import": dict(imports.get(source_id) or {}),
                "support_import": dict(imports.get(support_id) or {}),
                "arg_bindings": {"pick_label": pick_arg, "place_label": place_arg},
                "primary_pick_label": pick_label,
                "candidate_pick_labels": list(source_binding.get("candidate_pick_labels") or []),
                "primary_place_label": place_label,
                "candidate_place_labels": support_labels,
                "success_predicate": {"type": "stacked", "top": source_id, "bottom": support_id},
            }
        )
        current_top = source_id
    return steps, skill_args


def _score_stack_chain(
    *,
    chain: list[str],
    refs: dict[str, dict[str, Any]],
    imports: dict[str, dict[str, Any]],
    registry: ToolRegistry,
    admission: dict[str, Any],
) -> float:
    if not admission.get("accepted"):
        return -1000.0
    score = 240.0 + max(0, len(chain) - 2) * 30.0
    for index in range(1, len(chain)):
        source_id = chain[index]
        support_id = chain[index - 1]
        score += _score_stack_pair(
            source_asset=dict(refs.get(source_id) or {}),
            source_import=dict(imports.get(source_id) or {}),
            support_asset=dict(refs.get(support_id) or {}),
            support_import=dict(imports.get(support_id) or {}),
            admission={"accepted": True},
        ) / 10.0
    return score


def _admit_tier4_pickplace(
    *,
    task: dict[str, Any],
    source_asset: dict[str, Any],
    source_import: dict[str, Any],
    place_asset: dict[str, Any],
    place_import: dict[str, Any],
    registry: ToolRegistry,
) -> dict[str, Any]:
    reasons: list[str] = []
    risk_notes: list[str] = []
    if not bool(source_asset.get("qualified")):
        reasons.append("source asset qualified=false")
    if not bool(source_asset.get("has_collision_prims")):
        reasons.append("source asset has_collision_prims=false")
    if not bool(place_asset.get("qualified")):
        reasons.append("place target qualified=false")
    if str(source_asset.get("category") or "") in HIGH_RISK_TARGET_CATEGORIES:
        reasons.append(f"source category is high-risk for current primitive set: {source_asset.get('category')}")
    if "pickplace/semantic_pickplace.yaml" not in registry.skill_paths:
        reasons.append("KW registry lacks semantic_pickplace skill")
    if not _pose_in_workspace(source_import, y_limit=0.55):
        reasons.append("source pose outside conservative workspace")
    if not _pose_in_workspace(place_import, y_limit=0.75):
        risk_notes.append("place target pose is near/outside normal table workspace; semantic place may still handle it")
    if not bool(place_asset.get("has_collision_prims")):
        risk_notes.append("place target has_collision_prims=false; allowed for static container grounding only")
    return {
        "accepted": not reasons,
        "reject_reasons": reasons,
        "risk_notes": risk_notes,
        "checks": {
            "source_qualified": bool(source_asset.get("qualified")),
            "source_has_collision_prims": bool(source_asset.get("has_collision_prims")),
            "place_qualified": bool(place_asset.get("qualified")),
            "place_has_collision_prims": bool(place_asset.get("has_collision_prims")),
            "semantic_pickplace_available": "pickplace/semantic_pickplace.yaml" in registry.skill_paths,
        },
    }


def _admit_stack_pair(
    *,
    source_asset: dict[str, Any],
    source_import: dict[str, Any],
    support_asset: dict[str, Any],
    support_import: dict[str, Any],
    registry: ToolRegistry,
) -> dict[str, Any]:
    reasons: list[str] = []
    risk_notes: list[str] = []
    if not bool(source_asset.get("qualified")):
        reasons.append("source asset qualified=false")
    if not bool(source_asset.get("has_collision_prims")):
        reasons.append("source asset has_collision_prims=false")
    if not bool(support_asset.get("qualified")):
        reasons.append("support asset qualified=false")
    if not bool(support_asset.get("has_collision_prims")):
        reasons.append("support asset has_collision_prims=false")
    if str(source_asset.get("category") or "") != "block":
        risk_notes.append(f"source category is not block: {source_asset.get('category')}")
    if str(support_asset.get("category") or "") != "block":
        risk_notes.append(f"support category is not block: {support_asset.get('category')}")
    if "pickplace/semantic_pick.yaml" not in registry.skill_paths:
        reasons.append("KW registry lacks semantic_pick skill")
    if "pickplace/semantic_place.yaml" not in registry.skill_paths:
        reasons.append("KW registry lacks semantic_place skill")
    if not _pose_in_workspace(source_import, y_limit=0.60):
        reasons.append("source pose outside conservative workspace")
    if not _pose_in_workspace(support_import, y_limit=0.60):
        reasons.append("support pose outside conservative workspace")
    return {
        "accepted": not reasons,
        "reject_reasons": reasons,
        "risk_notes": risk_notes,
        "checks": {
            "source_qualified": bool(source_asset.get("qualified")),
            "source_has_collision_prims": bool(source_asset.get("has_collision_prims")),
            "support_qualified": bool(support_asset.get("qualified")),
            "support_has_collision_prims": bool(support_asset.get("has_collision_prims")),
            "semantic_pick_available": "pickplace/semantic_pick.yaml" in registry.skill_paths,
            "semantic_place_available": "pickplace/semantic_place.yaml" in registry.skill_paths,
        },
    }


def _score(*, task_class: str, target_asset: dict[str, Any], target_import: dict[str, Any], admission: dict[str, Any]) -> float:
    if not admission.get("accepted"):
        return -1000.0
    score = 100.0
    score += PREFERRED_TARGET_CATEGORIES.get(str(target_asset.get("category") or ""), 0)
    if task_class == "general_pickup":
        score += 20.0
    if target_asset.get("collision_mode") == "visual_mesh":
        score += 5.0
    pose = target_import.get("pose") if isinstance(target_import.get("pose"), dict) else {}
    position = pose.get("position") if isinstance(pose.get("position"), list) else []
    if len(position) >= 2:
        x, y = float(position[0]), abs(float(position[1]))
        score += max(0.0, 12.0 - abs(x - 0.70) * 20.0 - y * 10.0)
    return score


def _score_tier4(
    *,
    source_asset: dict[str, Any],
    source_import: dict[str, Any],
    place_asset: dict[str, Any],
    place_import: dict[str, Any],
    admission: dict[str, Any],
) -> float:
    if not admission.get("accepted"):
        return -1000.0
    score = 120.0
    if source_asset.get("category") == "bottle":
        score += 30.0
    if place_asset.get("category") == "dustbin":
        score += 20.0
    if source_asset.get("collision_mode") == "visual_mesh":
        score += 5.0
    source_pose = _position(source_import)
    place_pose = _position(place_import)
    if source_pose:
        x, y, _z = source_pose
        score += max(0.0, 12.0 - abs(x - 0.65) * 15.0 - abs(y) * 8.0)
    if source_pose and place_pose:
        score -= min(10.0, abs(source_pose[1] - place_pose[1]) * 3.0)
    return score


def _score_stack_pair(
    *,
    source_asset: dict[str, Any],
    source_import: dict[str, Any],
    support_asset: dict[str, Any],
    support_import: dict[str, Any],
    admission: dict[str, Any],
) -> float:
    if not admission.get("accepted"):
        return -1000.0
    score = 130.0
    if source_asset.get("category") == "block":
        score += 20.0
    if support_asset.get("category") == "block":
        score += 20.0
    if source_asset.get("collision_mode") == "visual_mesh":
        score += 4.0
    if support_asset.get("collision_mode") == "visual_mesh":
        score += 4.0
    source_pose = _position(source_import)
    support_pose = _position(support_import)
    if source_pose:
        x, y, _z = source_pose
        score += max(0.0, 10.0 - abs(x - 0.55) * 12.0 - abs(y) * 5.0)
    if source_pose and support_pose:
        dx = float(source_pose[0]) - float(support_pose[0])
        dy = float(source_pose[1]) - float(support_pose[1])
        distance = (dx * dx + dy * dy) ** 0.5
        score += max(0.0, 12.0 - abs(distance - 0.14) * 40.0)
        if float(source_pose[1]) * float(support_pose[1]) > 0:
            score += 4.0
    return score


def _tier4_binding_execution_bonus(*, binding: dict[str, Any], source_import: dict[str, Any], place_import: dict[str, Any]) -> float:
    score = 0.0
    visual_hints = binding.get("asset_visual_hints") if isinstance(binding.get("asset_visual_hints"), dict) else {}
    if visual_hints.get("preferred_labels"):
        score += 1.0
    source_pose = _position(source_import)
    if source_pose is not None:
        _x, y, _z = source_pose
        score += min(5.0, max(0.0, -float(y) * 30.0))
    place_pose = _position(place_import)
    if source_pose is not None and place_pose is not None:
        source_y = float(source_pose[1])
        place_y = float(place_pose[1])
        if source_y * place_y > 0:
            score += 6.0
        elif abs(source_y) > 0.05 and abs(place_y) > 0.05:
            score -= 8.0
    return score


def _target_asset(scene: dict[str, Any]) -> dict[str, Any]:
    metadata = scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {}
    refs = metadata.get("robodojo_asset_refs")
    if not isinstance(refs, list):
        return {}
    for ref in refs:
        if isinstance(ref, dict) and str(ref.get("id") or "") == "target_prop":
            return dict(ref)
    return {}


def _target_import(scene: dict[str, Any], target_id: str) -> dict[str, Any]:
    imports = scene.get("imports")
    if not isinstance(imports, list):
        return {}
    for item in imports:
        if isinstance(item, dict) and str(item.get("id") or "") == target_id:
            return dict(item)
    return {}


def _asset_refs(scene: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metadata = scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {}
    refs = metadata.get("robodojo_asset_refs")
    if not isinstance(refs, list):
        return {}
    return {str(ref.get("id")): dict(ref) for ref in refs if isinstance(ref, dict) and ref.get("id")}


def _imports_by_id(scene: dict[str, Any]) -> dict[str, dict[str, Any]]:
    imports = scene.get("imports")
    if not isinstance(imports, list):
        return {}
    return {str(item.get("id")): dict(item) for item in imports if isinstance(item, dict) and item.get("id")}


def _inside_success_items(success: dict[str, Any]) -> list[dict[str, Any]]:
    items = success.get("all_of") if isinstance(success, dict) else None
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict) and item.get("type") == "inside"]


def _stacked_success_items(success: dict[str, Any]) -> list[dict[str, Any]]:
    items = success.get("all_of") if isinstance(success, dict) else None
    if not isinstance(items, list):
        return []
    stacked: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "stacked":
            continue
        objects = item.get("objects")
        if isinstance(objects, list) and len(objects) >= 2:
            stacked.append(dict(item))
        elif item.get("top") and item.get("bottom"):
            stacked.append({"type": "stacked", "objects": [item.get("bottom"), item.get("top")]})
    return stacked


def _position(import_item: dict[str, Any]) -> tuple[float, float, float] | None:
    pose = import_item.get("pose") if isinstance(import_item.get("pose"), dict) else {}
    position = pose.get("position") if isinstance(pose.get("position"), list) else []
    if len(position) < 3:
        return None
    return float(position[0]), float(position[1]), float(position[2])


def _pose_in_workspace(import_item: dict[str, Any], *, y_limit: float) -> bool:
    pos = _position(import_item)
    if pos is None:
        return False
    x, y, z = pos
    return 0.25 <= x <= 1.15 and abs(y) <= y_limit and 0.35 <= z <= 1.10


def _public_reasoning_names(names: list[str]) -> list[str]:
    return [name for name in names if "qwen" not in name.lower()]


def _public_action_names(actions: list[str]) -> list[str]:
    return [
        action
        for action in actions
        if "qwen" not in action.lower()
        and not action.strip().startswith("=")
        and "args.arm_id" not in action
    ]
