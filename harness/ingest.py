"""ingest:定位任务视频与 refined trace → 抽帧 + meta.json 写入新 run 目录。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import util


def find_video(task: str) -> Path:
    d = util.data_root() / "data/filtered_1024_task_videos" / task
    vids = sorted(p for p in d.glob("*") if p.suffix.lower() in (".mp4", ".mov", ".avi", ".webm"))
    if not vids:
        raise FileNotFoundError(f"no video under {d}")
    return vids[0]


def find_trace(task: str) -> Path | None:
    """在 refined 输出里找带 segments 的 trace JSON(schema 见 robot_subtask_seg)。"""
    base = util.data_root() / "outputs/gemini35_eef_trace_v2_refined" / task
    if not base.exists():
        return None
    exact = sorted(base.rglob("trace.json"))
    for p in exact or sorted(base.rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if (isinstance(d, dict) and d.get("trace_id")
                and isinstance(d.get("segments"), list) and d["segments"]
                and "start_sec" in d["segments"][0]):
            return p
    return None


def sample_frames(video: Path, out_dir: Path, n: int = 24, max_width: int = 640) -> list[dict]:
    import cv2  # lazy

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total / fps if fps else 0.0
    frames = []
    for i in range(n):
        idx = min(total - 1, round(i * (total - 1) / max(1, n - 1)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, img = cap.read()
        if not ok:
            continue
        h, w = img.shape[:2]
        if w > max_width:
            img = cv2.resize(img, (max_width, int(h * max_width / w)))
        name = f"f{idx:05d}.jpg"
        cv2.imwrite(str(out_dir / name), img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        frames.append({"frame_idx": idx, "t_sec": round(idx / fps, 3), "file": f"frames/{name}"})
    cap.release()
    return frames, {"fps": fps, "total_frames": total, "duration_sec": round(duration, 3),
                    "video": str(video)}


def grab_frame(video: Path, t_sec: float, out_path: Path, max_width: int = 640) -> dict:
    """按时间戳精确抓一帧(供 keyframes 用)。"""
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    idx = max(0, round(t_sec * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"cannot read frame at {t_sec}s from {video}")
    h, w = img.shape[:2]
    if w > max_width:
        img = cv2.resize(img, (max_width, int(h * max_width / w)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return {"frame_idx": idx, "t_sec": round(t_sec, 3), "file": out_path.name}


def run(task: str, video: str | None = None, trace: str | None = None,
        n_frames: int = 24) -> Path:
    vid = Path(video) if video else find_video(task)
    trc = Path(trace) if trace else find_trace(task)
    run_dir = util.new_run_dir(task)
    frames, vmeta = sample_frames(vid, run_dir / "frames", n=n_frames)
    if trc:
        shutil.copy(trc, run_dir / "trace.json")
    util.write_json(run_dir / "meta.json", {
        "task": task, "video": vmeta, "trace_source": str(trc) if trc else None,
        "frames": frames,
    })
    print(f"[ingest] {task}: {len(frames)} frames, trace={'yes' if trc else 'NO'} -> {run_dir}")
    return run_dir
