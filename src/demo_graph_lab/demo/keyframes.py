"""Sample the first, last, and evenly spaced frames from each stage.

当前实现只做确定性时间采样，不做事件检测。
"""

from __future__ import annotations

from ..common import artifacts
from . import ingest


def run(task: str, per_stage: int = 5) -> dict:
    run_dir = artifacts.latest_run_dir(task)
    meta = artifacts.read_json(run_dir / "meta.json")
    stages = artifacts.read_json(run_dir / "stages.json")
    video = meta["video"]["video"]
    out = {}
    for st in stages:
        span = max(0.0, st["end_sec"] - st["start_sec"])
        ts = [st["start_sec"] + span * i / max(1, per_stage - 1) for i in range(per_stage)]
        kf_dir = run_dir / "frames" / f"stage{st['index']:02d}"
        frames = []
        for t in ts:
            info = ingest.grab_frame(video, t, kf_dir / f"t{t:07.2f}.jpg".replace(".", "_", 1))
            info["file"] = f"frames/stage{st['index']:02d}/{info['file']}"
            frames.append(info)
        out[str(st["index"])] = frames
    artifacts.write_json(run_dir / "keyframes.json", out)
    n = sum(len(v) for v in out.values())
    print(f"[keyframes] {task}: {n} frames across {len(stages)} stages")
    return out
