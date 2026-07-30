"""Minimal third-person trial recorder (WebUI frame grab -> mp4)."""
from __future__ import annotations
import argparse, json, os, shutil, signal, subprocess, sys, threading, time, urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = os.getenv("KNOWIN_WEBUI_URL", "http://127.0.0.1:5150")
DEFAULT_FPS = 1.8

@dataclass
class RecordingStats:
    frames: int = 0
    mp4_path: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    errors: list[str] = field(default_factory=list)

class TrialRecorder:
    def __init__(self, run_dir, base_url=DEFAULT_BASE_URL, fps=DEFAULT_FPS,
                 source="free", view="left", width=1280, height=720):
        self.run_dir = Path(run_dir); self.run_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.fps = float(fps)
        self.source, self.view, self.width, self.height = source, view, int(width), int(height)
        self.stats = RecordingStats()
        self._stop = threading.Event()
        self._thread = None
        self._frames_dir = self.run_dir / "_frames"
        self._frames_dir.mkdir(exist_ok=True)

    def __enter__(self):
        self.start(); return self
    def __exit__(self, *exc):
        self.stop(); return False

    def start(self):
        self.stats.started_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=30)
        self.stats.finished_at = time.time()
        self._encode()

    def _loop(self):
        interval = 1.0 / max(self.fps, 0.1)
        url = (f"{self.base_url}/api/frame.jpg?source={self.source}&view={self.view}"
               f"&width={self.width}&height={self.height}&timeout=8")
        while not self._stop.is_set():
            t0 = time.time()
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                if data and len(data) > 1000:
                    idx = self.stats.frames
                    (self._frames_dir / f"frame_{idx:06d}.jpg").write_bytes(data)
                    self.stats.frames += 1
            except Exception as exc:
                self.stats.errors.append(str(exc))
                time.sleep(0.5)
            dt = time.time() - t0
            time.sleep(max(0.0, interval - dt))

    def _encode(self):
        out = self.run_dir / "third_person.mp4"
        if self.stats.frames <= 0:
            self.stats.errors.append("no frames captured"); return
        cmd = ["ffmpeg","-y","-framerate",str(self.fps),
               "-i",str(self._frames_dir/"frame_%06d.jpg"),
               "-c:v","libx264","-pix_fmt","yuv420p",str(out)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.stats.mp4_path = str(out)
        except Exception as exc:
            self.stats.errors.append(f"ffmpeg failed: {exc}")
        shutil.rmtree(self._frames_dir, ignore_errors=True)

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--until-file")
    p.add_argument("--duration", type=float)
    args = p.parse_args(argv)
    rec = TrialRecorder(args.out)
    rec.start()
    try:
        if args.until_file:
            done = Path(args.until_file)
            while not done.exists() and not rec._stop.is_set():
                time.sleep(0.2)
        elif args.duration:
            time.sleep(args.duration)
        else:
            signal.pause()
    finally:
        rec.stop()
        print(json.dumps(asdict(rec.stats), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
