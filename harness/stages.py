"""[phase0 2/9 · stages] stages:优先用 refined trace 的 segments;无 trace 时才走 VLM 切分(需人审)。

trace 格式(components/robot-subtask-seg/tests 为准): segments[{index,start_sec,end_sec,
label,eef_event?,motion_type?,manipulated_object?,target_object?,role?,confidence?}]。
demonstration_bundle 路径将来经 adapters/demo_bundle 接入,此处不重复实现。
"""

from __future__ import annotations

from pathlib import Path

from . import util


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
    """无 trace 时的兜底:Opus 提议切分,写 stages_proposed.json,须人审后改名 stages.json。"""
    from . import llm
    prompt = (util.HARNESS_ROOT / "prompts/stage_split.md").read_text().split("---", 1)[1]
    meta = util.read_json(run_dir / "meta.json")
    frames = meta["frames"][:k_frames]
    content = [{"type": "text", "text": prompt.replace("{N}", str(len(frames)))
                + f"\nTask instruction: {meta.get('task')}"}]
    for fr in frames:
        content.append({"type": "text", "text": f"frame_idx={fr['frame_idx']} t={fr['t_sec']}s"})
        content.append({"type": "image_url", "image_url": {"url":
            "data:image/jpeg;base64," + util.b64_jpeg(run_dir / fr["file"])}})
    out = llm.chat([{"role": "user", "content": content}], run_dir, tag="stage_split",
                   model=model)
    segs = llm.parse_json_block(out)
    fps = meta["video"]["fps"]
    stages = [{"index": i, "name": s["stage"], "label": s.get("boundary_event", ""),
               "start_sec": s["start_frame"] / fps, "end_sec": s["end_frame"] / fps,
               "confidence": s.get("confidence"), "source": "vlm_proposed"}
              for i, s in enumerate(segs)]
    util.write_json(run_dir / "stages_proposed.json", stages)
    return stages


def run(task: str, model: str | None = None) -> list[dict]:
    run_dir = util.latest_run_dir(task)
    trace_path = run_dir / "trace.json"
    if trace_path.exists():
        stages = from_trace(util.read_json(trace_path))
    else:
        stages = vlm_split(run_dir, model)
    util.write_json(run_dir / "stages.json", stages)
    print(f"[stages] {task}: {len(stages)} stages ({stages[0]['source']})")
    return stages
