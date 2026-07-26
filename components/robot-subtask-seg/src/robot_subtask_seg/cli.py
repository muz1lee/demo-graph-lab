from __future__ import annotations

import json
from pathlib import Path
from traceback import format_exc

import typer
from rich.console import Console

from robot_subtask_seg.action_trace import materialize_action_trace, write_action_trace
from robot_subtask_seg.archive import archive_run
from robot_subtask_seg.config import load_config, set_provider
from robot_subtask_seg.contact_sheet import build_episode_contact_sheets
from robot_subtask_seg.dense_tracking import (
    CoTrackerClient,
    DenseTrackingError,
    enrich_dense_tracking,
)
from robot_subtask_seg.demonstration_bundle import (
    build_demonstration_bundle,
    write_demonstration_bundle,
)
from robot_subtask_seg.manifest import build_manifest_from_dir, load_manifest, write_manifest
from robot_subtask_seg.operation_structure import build_operation_structure
from robot_subtask_seg.providers import build_provider
from robot_subtask_seg.quality import audit_run, write_quality_report
from robot_subtask_seg.refinement import refine_trace_actions, write_refined_trace
from robot_subtask_seg.schema import Trace
from robot_subtask_seg.segmentation import segment_video_item, trace_summary
from robot_subtask_seg.video_evidence import Sam3Client, extract_video_evidence

app = typer.Typer(help="Robot video subtask segmentation reproduction CLI.")
console = Console()


@app.command("build-manifest")
def build_manifest_cmd(
    input_dir: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True),
    output: Path = typer.Option(...),
    source: str = typer.Option("robodojo_1024_filtered"),
) -> None:
    manifest = build_manifest_from_dir(input_dir, source=source)
    write_manifest(manifest, output)
    console.print(f"Wrote {len(manifest.videos)} videos to {output}")


@app.command("make-sheets")
def make_sheets_cmd(
    video: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    output_dir: Path = typer.Option(...),
    config: Path | None = typer.Option(None),
) -> None:
    cfg = load_config(config)
    seg = cfg["segmentation"]
    sheets = build_episode_contact_sheets(
        video,
        output_dir=output_dir,
        sample_sec=float(seg["sample_sec"]),
        frame_width=int(seg["frame_width"]),
        frames_per_sheet=int(seg["frames_per_sheet"]),
        columns=int(seg["columns"]),
        quality=int(seg["jpeg_quality"]),
    )
    console.print(f"Wrote {len(sheets)} contact sheets to {output_dir}")


@app.command("segment")
def segment_cmd(
    manifest: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    output_dir: Path = typer.Option(...),
    config: Path | None = typer.Option(None),
    provider: str | None = typer.Option(None),
    limit: int | None = typer.Option(None),
    keep_going: bool = typer.Option(True, help="Continue after per-video failures."),
) -> None:
    cfg = set_provider(load_config(config), provider)
    provider_impl = build_provider(cfg)
    data = load_manifest(manifest)
    items = data.videos[:limit] if limit is not None else data.videos
    summaries = []
    errors = []
    for item in items:
        console.print(f"[bold]segment[/bold] {item.task_id} ({item.task_class})")
        try:
            trace = segment_video_item(item, config=cfg, output_dir=output_dir, provider=provider_impl)
        except Exception as exc:
            error = {
                "task_id": item.task_id,
                "task_class": item.task_class,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            errors.append(error)
            error_dir = output_dir / item.task_class / item.task_id
            error_dir.mkdir(parents=True, exist_ok=True)
            (error_dir / "error.json").write_text(json.dumps(error, indent=2) + "\n", encoding="utf-8")
            (error_dir / "traceback.txt").write_text(format_exc(), encoding="utf-8")
            console.print(f"  [red]failed[/red] {type(exc).__name__}: {exc}")
            if not keep_going:
                raise
            continue
        else:
            summaries.append(trace_summary(trace))
            console.print(f"  segments={len(trace.segments)} trace={trace.trace_id}")
    summary_path = output_dir / "summary.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    if errors:
        (output_dir / "errors.json").write_text(json.dumps(errors, indent=2) + "\n", encoding="utf-8")
    console.print(f"Wrote summary to {summary_path}")
    report = audit_run(output_dir)
    report_path = output_dir / "quality_report.json"
    write_quality_report(report, report_path)
    console.print(
        f"Wrote quality report to {report_path} "
        f"(status={report['status']}, execution_ready={report['execution_ready']})"
    )
    if errors:
        console.print(f"[yellow]Completed with {len(errors)} errors[/yellow]")


@app.command("inspect")
def inspect_cmd(trace: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False)) -> None:
    parsed = Trace.model_validate_json(trace.read_text(encoding="utf-8"))
    console.print_json(json.dumps(trace_summary(parsed)))


@app.command("audit-run")
def audit_run_cmd(
    output_dir: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True),
    report: Path | None = typer.Option(None),
    archive_failed: bool = typer.Option(False, help="Move structurally failed runs to _bad_runs."),
    archive_root: Path | None = typer.Option(None),
) -> None:
    parsed = audit_run(output_dir)
    report_path = report or (output_dir / "quality_report.json")
    write_quality_report(parsed, report_path)
    console.print_json(json.dumps(parsed))
    console.print(f"Wrote quality report to {report_path}")
    if archive_failed and parsed["archive_recommended"]:
        dest = archive_run(
            output_dir,
            archive_root=archive_root or output_dir.parent / "_bad_runs",
            reason=(
                "Archived by audit-run because the quality gate found structural errors. "
                f"Report: {report_path}"
            ),
        )
        console.print(f"[yellow]Archived failed run to {dest}[/yellow]")


@app.command("archive-run")
def archive_run_cmd(
    run_dir: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True),
    archive_root: Path | None = typer.Option(None),
    reason: str = typer.Option(...),
) -> None:
    dest = archive_run(
        run_dir,
        archive_root=archive_root or run_dir.parent / "_bad_runs",
        reason=reason,
    )
    console.print(f"Archived {run_dir} to {dest}")


@app.command("materialize-actions")
def materialize_actions_cmd(
    output_dir: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True),
    action_dir: Path | None = typer.Option(None),
    include_cleanup: bool = typer.Option(True),
) -> None:
    destination = action_dir or output_dir / "action_traces"
    summaries = []
    for trace_path in sorted(output_dir.glob("*/*/trace.json")):
        trace = Trace.model_validate_json(trace_path.read_text(encoding="utf-8"))
        action_trace = materialize_action_trace(trace, include_cleanup=include_cleanup)
        task_dir = destination / trace.task_class / trace.task_id
        out_path = task_dir / "action_trace.json"
        write_action_trace(action_trace, out_path)
        summaries.append(
            {
                "task_class": trace.task_class,
                "task_id": trace.task_id,
                "step_count": len(action_trace["steps"]),
                "heuristic_split_count": action_trace["heuristic_split_count"],
                "filtered_segment_count": action_trace["filtered_segment_count"],
                "path": str(out_path),
            }
        )
    destination.mkdir(parents=True, exist_ok=True)
    summary_path = destination / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    console.print(f"Wrote {len(summaries)} action traces to {destination}")
    console.print(f"Wrote action summary to {summary_path}")


@app.command("refine-actions")
def refine_actions_cmd(
    output_dir: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True),
    refined_dir: Path | None = typer.Option(None),
    config: Path | None = typer.Option(None),
    provider: str | None = typer.Option(None),
    materialize: bool = typer.Option(True, help="Also write action_traces for the refined traces."),
) -> None:
    cfg = set_provider(load_config(config), provider)
    provider_impl = build_provider(cfg)
    destination = refined_dir or output_dir / "refined_traces"
    summaries = []
    for trace_path in sorted(output_dir.glob("*/*/trace.json")):
        source_trace = Trace.model_validate_json(trace_path.read_text(encoding="utf-8"))
        task_dir = destination / source_trace.task_class / source_trace.task_id
        refined, manifest = refine_trace_actions(
            source_trace,
            config=cfg,
            output_dir=task_dir,
            provider=provider_impl,
        )
        write_refined_trace(refined, manifest, task_dir)
        summaries.append(trace_summary(refined))
        console.print(
            f"refined {source_trace.task_id}: "
            f"{len(source_trace.segments)} -> {len(refined.segments)} segments"
        )
    destination.mkdir(parents=True, exist_ok=True)
    summary_path = destination / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    report = audit_run(destination)
    write_quality_report(report, destination / "quality_report.json")
    console.print(
        f"Wrote refined traces to {destination} "
        f"(status={report['status']}, execution_ready={report['execution_ready']})"
    )
    if materialize:
        _write_action_traces(destination, destination / "action_traces")


@app.command("extract-video-evidence")
def extract_video_evidence_cmd(
    video: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    output_dir: Path = typer.Option(...),
    prompt: list[str] = typer.Option(
        ...,
        "--prompt",
        help="Object or part description for grounding. Repeat for multiple targets.",
    ),
    task_id: str | None = typer.Option(None),
    instruction: str = typer.Option(""),
    trace: Path | None = typer.Option(None, exists=True, file_okay=True, dir_okay=False),
    sam3_url: str = typer.Option("http://127.0.0.1:6068"),
    sample_sec: float = typer.Option(1.0, min=0.05),
    frame_width: int = typer.Option(640, min=64),
    start_sec: float = typer.Option(0.0, min=0.0),
    end_sec: float | None = typer.Option(None, min=0.0),
    max_frames: int = typer.Option(120, min=1),
    timeout_sec: float = typer.Option(60.0, min=1.0),
    max_retries: int = typer.Option(3, min=0),
    keep_going: bool = typer.Option(True),
) -> None:
    parsed_trace = (
        Trace.model_validate_json(trace.read_text(encoding="utf-8")) if trace is not None else None
    )
    segmenter = Sam3Client(
        sam3_url,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
    )
    bundle = extract_video_evidence(
        video,
        output_dir=output_dir,
        prompts=prompt,
        segmenter=segmenter,
        task_id=task_id,
        instruction=instruction,
        trace=parsed_trace,
        trace_path=trace,
        sample_sec=sample_sec,
        frame_width=frame_width,
        start_sec=start_sec,
        end_sec=end_sec,
        max_frames=max_frames,
        keep_going=keep_going,
    )
    console.print(
        f"Wrote video evidence to {output_dir / 'video_evidence.json'} "
        f"(frames={len(bundle['frames'])}, observations={len(bundle['observations'])}, "
        f"tracks={bundle['tracking']['track_count']}, errors={len(bundle['service_errors'])})"
    )


@app.command("enrich-dense-tracking")
def enrich_dense_tracking_cmd(
    evidence_dir: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True),
    output_dir: Path | None = typer.Option(None),
    tracker_url: str = typer.Option("http://127.0.0.1:8093"),
    points_per_object: int = typer.Option(16, min=4, max=64),
    target_fps: float = typer.Option(10.0, min=1.0, max=30.0),
    inference_width: int = typer.Option(512, min=128, max=1024),
    max_frames: int = typer.Option(300, min=2, max=1200),
    min_visible_ratio: float = typer.Option(0.2, min=0.05, max=1.0),
    timeout_sec: float = typer.Option(180.0, min=1.0),
    max_retries: int = typer.Option(2, min=0),
) -> None:
    tracker = CoTrackerClient(
        tracker_url,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
    )
    try:
        enriched = enrich_dense_tracking(
            evidence_dir,
            tracker=tracker,
            output_dir=output_dir,
            points_per_object=points_per_object,
            target_fps=target_fps,
            inference_width=inference_width,
            max_frames=max_frames,
            min_visible_ratio=min_visible_ratio,
        )
    except DenseTrackingError as exc:
        console.print(f"[red]Dense tracking failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    destination = output_dir or evidence_dir
    dense = enriched["dense_tracking"]
    console.print(
        f"Wrote dense evidence to {destination / 'dense_video_evidence.json'} "
        f"(objects={dense['object_count']}, frames={dense['frame_count']}, "
        f"linked_observations={dense['association']['linked_observation_count']})"
    )


@app.command("export-demonstration-bundle")
def export_demonstration_bundle_cmd(
    trace: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    dense_evidence: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    output: Path = typer.Option(...),
) -> None:
    bundle = build_demonstration_bundle(
        trace_path=trace,
        dense_evidence_path=dense_evidence,
    )
    write_demonstration_bundle(bundle, output)
    summary = bundle["summary"]
    console.print(
        f"Wrote demonstration bundle to {output} "
        f"(segments={summary['segment_count']}, objects={summary['object_count']}, "
        f"dense_frames={summary['dense_frame_count']})"
    )


@app.command("refine-operation-structure")
def refine_operation_structure_cmd(
    trace: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    video: Path | None = typer.Option(None, exists=True, file_okay=True, dir_okay=False),
    output_dir: Path = typer.Option(...),
    mode: str = typer.Option("evidence_guided"),
    demonstration_bundle: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    config: Path | None = typer.Option(None),
    provider: str | None = typer.Option(None),
) -> None:
    if mode not in {"visual_only", "evidence_guided"}:
        raise typer.BadParameter("mode must be visual_only or evidence_guided")
    cfg = set_provider(load_config(config), provider)
    provider_impl = build_provider(cfg)
    parsed_trace = Trace.model_validate_json(trace.read_text(encoding="utf-8"))
    bundle = (
        json.loads(demonstration_bundle.read_text(encoding="utf-8"))
        if demonstration_bundle is not None
        else None
    )
    structure_cfg = cfg.get("operation_structure", {})
    result = build_operation_structure(
        parsed_trace,
        provider=provider_impl,
        output_dir=output_dir,
        video_path=video,
        mode=mode,
        demonstration_bundle=bundle,
        sample_sec=float(structure_cfg.get("sample_sec", 0.25)),
        frame_width=int(structure_cfg.get("frame_width", 320)),
        frames_per_sheet=int(structure_cfg.get("frames_per_sheet", 16)),
        columns=int(structure_cfg.get("columns", 4)),
        jpeg_quality=int(structure_cfg.get("jpeg_quality", 95)),
    )
    summary = result["summary"]
    console.print(
        f"Wrote operation structure to {output_dir / 'operation_structure.json'} "
        f"(procedures={summary['procedure_count']}, instances={summary['instance_count']}, "
        f"phases={summary['phase_count']}, reused={summary['reused_procedure_count']})"
    )


def _write_action_traces(output_dir: Path, destination: Path, *, include_cleanup: bool = True) -> None:
    summaries = []
    for trace_path in sorted(output_dir.glob("*/*/trace.json")):
        trace = Trace.model_validate_json(trace_path.read_text(encoding="utf-8"))
        action_trace = materialize_action_trace(trace, include_cleanup=include_cleanup)
        task_dir = destination / trace.task_class / trace.task_id
        out_path = task_dir / "action_trace.json"
        write_action_trace(action_trace, out_path)
        summaries.append(
            {
                "task_class": trace.task_class,
                "task_id": trace.task_id,
                "step_count": len(action_trace["steps"]),
                "heuristic_split_count": action_trace["heuristic_split_count"],
                "filtered_segment_count": action_trace["filtered_segment_count"],
                "path": str(out_path),
            }
        )
    destination.mkdir(parents=True, exist_ok=True)
    summary_path = destination / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    console.print(f"Wrote {len(summaries)} action traces to {destination}")


if __name__ == "__main__":
    app()
