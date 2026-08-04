"""Split a demonstration into stages, preferring an existing refined trace.

Trace segments contain ``index/start_sec/end_sec/label`` plus optional motion,
object, role and confidence fields.
"""

from __future__ import annotations

from pathlib import Path

from ..common import artifacts
from ..graph import vocab


def uniform_sample(items: list, count: int) -> list:
    """Select at most ``count`` items while including both ends when possible."""
    if count <= 0:
        raise ValueError("sample count must be positive")
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    indices = [round(i * (len(items) - 1) / (count - 1)) for i in range(count)]
    return [items[index] for index in indices]


def validate_proposal(proposal, total_frames: int) -> list[str]:
    """Validate the strict VLM stage-split schema."""
    if not isinstance(proposal, list) or not proposal:
        return ["stage split must be a non-empty list"]
    errors = []
    previous_end = -1
    required = {"stage", "start_frame", "end_frame", "boundary_event", "confidence"}
    for index, stage in enumerate(proposal):
        prefix = f"stage[{index}]"
        if not isinstance(stage, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = sorted(required - set(stage))
        extra = sorted(set(stage) - required)
        if missing:
            errors.append(f"{prefix} missing fields {missing}")
        if extra:
            errors.append(f"{prefix} has unknown fields {extra}")
        if stage.get("stage") not in vocab.STAGE_VOCAB:
            errors.append(f"{prefix}.stage is outside the closed vocabulary")
        start, end = stage.get("start_frame"), stage.get("end_frame")
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)):
            errors.append(f"{prefix} frame bounds must be integers")
        elif not (0 <= start <= end < total_frames):
            errors.append(f"{prefix} frame bounds are outside the video")
        elif start < previous_end:
            errors.append(f"{prefix} overlaps the previous stage")
        else:
            previous_end = end
        if not isinstance(stage.get("boundary_event"), str) or not stage.get("boundary_event"):
            errors.append(f"{prefix}.boundary_event must be a non-empty string")
        confidence = stage.get("confidence")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1):
            errors.append(f"{prefix}.confidence must be in [0, 1]")
    return errors


def from_trace(trace: dict) -> list[dict]:
    stages = []
    for i, seg in enumerate(trace.get("segments", [])):
        stages.append({
            "index": seg.get("index", i) if seg.get("index") is not None else i,
            "name": seg.get("motion_type") or seg.get("label", f"seg{seg.get('index')}"),
            "label": seg.get("label", ""),
            "start_sec": float(seg["start_sec"]),
            "end_sec": float(seg["end_sec"]),
            "manipulated_object": seg.get("manipulated_object"),
            "target_object": seg.get("target_object"),
            "eef_event": seg.get("eef_event"),
            "role": seg.get("role", "core"),
            "source": "trace",
        })
    return stages


def vlm_split(run_dir: Path, model: str | None, k_frames: int = 16) -> list[dict]:
    """无 trace 时让 VLM 提议切分，并保留 proposed 文件供人工检查。"""
    from ..common import llm
    prompt = (artifacts.PROMPT_ROOT / "stage_split.md").read_text().split("---", 1)[1]
    meta = artifacts.read_json(run_dir / "meta.json")
    frames = uniform_sample(meta["frames"], k_frames)
    content = [{"type": "text", "text": prompt.replace("{N}", str(len(frames)))
                + f"\nTask instruction: {meta.get('task')}"}]
    for fr in frames:
        content.append({"type": "text", "text": f"frame_idx={fr['frame_idx']} t={fr['t_sec']}s"})
        content.append({"type": "image_url", "image_url": {"url":
            "data:image/jpeg;base64," + artifacts.b64_jpeg(run_dir / fr["file"])}})
    tag = "stage_split"
    input_refs = ["meta.json", "package:prompts/stage_split.md",
                  *(fr["file"] for fr in frames)]
    messages = [{"role": "user", "content": content}]
    request = llm.request_record(
        messages, tag=tag, role="stage_split", model=llm.resolve_model(model),
        max_tokens=1500, temperature=0.2, input_refs=input_refs)
    out = llm.cached_response(run_dir, tag, request)
    if out is None:
        out = llm.chat(messages, run_dir, tag=tag, model=model,
                       role="stage_split", input_refs=input_refs)
    try:
        segs = llm.parse_json_block(out)
    except ValueError as error:
        llm.record_result(run_dir, tag, parse_error=str(error))
        raise
    errors = validate_proposal(segs, meta["video"]["total_frames"])
    llm.record_result(run_dir, tag, parsed=segs, validation_errors=errors)
    if errors:
        raise ValueError("invalid stage split: " + "; ".join(errors))
    fps = meta["video"]["fps"]
    stages = [{"index": i, "name": s["stage"], "label": s.get("boundary_event", ""),
               "start_sec": s["start_frame"] / fps, "end_sec": s["end_frame"] / fps,
               "confidence": s.get("confidence"), "source": "vlm_proposed"}
              for i, s in enumerate(segs)]
    artifacts.write_json(run_dir / "stages_proposed.json", stages)
    return stages


def run(task: str, model: str | None = None) -> list[dict]:
    run_dir = artifacts.latest_run_dir(task)
    artifacts.invalidate_outputs(run_dir, (
        "stages.json", "stages_proposed.json", "keyframes.json", "objects.json",
        "graph.json", "validation.json", "report.html", "stage_program.json",
        "perception_program.json", "policy.py", "compile_report.json",
        "compiled_graph.json", "compiled_objects.json",
    ))
    trace_path = run_dir / "trace.json"
    if trace_path.exists():
        stages = from_trace(artifacts.read_json(trace_path))
    else:
        stages = vlm_split(run_dir, model)
    if not stages:
        raise ValueError(f"no stages found for task {task!r}")
    from ..graph.validate import validate_stage_manifest
    meta = artifacts.read_json(run_dir / "meta.json")
    manifest_errors = validate_stage_manifest(
        stages,
        fps=meta.get("video", {}).get("fps"),
        total_frames=meta.get("video", {}).get("total_frames"),
    )
    if manifest_errors:
        raise ValueError("invalid stage manifest: " + "; ".join(manifest_errors))
    artifacts.write_json(run_dir / "stages.json", stages)
    print(f"[stages] {task}: {len(stages)} stages ({stages[0]['source']})")
    return stages
