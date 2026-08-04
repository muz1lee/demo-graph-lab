"""Split a demonstration into stages, preferring an existing refined trace.

Trace segments contain ``index/start_sec/end_sec/label`` plus optional motion,
object, role and confidence fields.
"""

from __future__ import annotations

from pathlib import Path

from ..common import artifacts


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
    frames = meta["frames"][:k_frames]
    content = [{"type": "text", "text": prompt.replace("{N}", str(len(frames)))
                + f"\nTask instruction: {meta.get('task')}"}]
    for fr in frames:
        content.append({"type": "text", "text": f"frame_idx={fr['frame_idx']} t={fr['t_sec']}s"})
        content.append({"type": "image_url", "image_url": {"url":
            "data:image/jpeg;base64," + artifacts.b64_jpeg(run_dir / fr["file"])}})
    out = llm.chat([{"role": "user", "content": content}], run_dir, tag="stage_split",
                   model=model)
    segs = llm.parse_json_block(out)
    fps = meta["video"]["fps"]
    stages = [{"index": i, "name": s["stage"], "label": s.get("boundary_event", ""),
               "start_sec": s["start_frame"] / fps, "end_sec": s["end_frame"] / fps,
               "confidence": s.get("confidence"), "source": "vlm_proposed"}
              for i, s in enumerate(segs)]
    artifacts.write_json(run_dir / "stages_proposed.json", stages)
    return stages


def run(task: str, model: str | None = None) -> list[dict]:
    run_dir = artifacts.latest_run_dir(task)
    trace_path = run_dir / "trace.json"
    if trace_path.exists():
        stages = from_trace(artifacts.read_json(trace_path))
    else:
        stages = vlm_split(run_dir, model)
    if not stages:
        raise ValueError(f"no stages found for task {task!r}")
    artifacts.write_json(run_dir / "stages.json", stages)
    print(f"[stages] {task}: {len(stages)} stages ({stages[0]['source']})")
    return stages
