from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import urlopen

from .config import ManagerConfig
from .io import write_json


def capture_keyframes(config: ManagerConfig, output_dir: str | Path, *, enabled: bool) -> dict[str, Any]:
    target = Path(output_dir)
    if not enabled:
        return {"enabled": False, "frames": [], "errors": []}
    target.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    errors: list[str] = []
    webui_base = webui_base_url(config)
    for source, view in (("free", "left"), ("head", "left"), ("left_hand", "left"), ("right_hand", "left")):
        try:
            params = urlencode(
                {
                    "source": source,
                    "view": view,
                    "width": "1280" if source == "free" else "640",
                    "height": "720" if source == "free" else "360",
                    "quality": "86",
                    "show_ee": "1",
                    "timeout": "4",
                    "_": str(time.time()),
                }
            )
            url = f"{webui_base}/api/frame.jpg?{params}"
            out = target / f"{source}_{view}.jpg"
            with urlopen(url, timeout=8.0) as resp:
                out.write_bytes(resp.read())
            frames.append(
                {
                    "source": source,
                    "view": view,
                    "path": str(out),
                    "url": url,
                    "timestamp_s": time.time(),
                }
            )
        except Exception as exc:
            errors.append(f"{source}/{view}: {exc!r}")
    write_json(target / "capture_manifest.json", {"method": "webui_frame_jpg", "webui_base_url": webui_base, "frames": frames, "errors": errors})
    return {"enabled": True, "method": "webui_frame_jpg", "frames": frames, "errors": errors, "dir": str(target)}


class PeriodicKeyframeSampler:
    def __init__(self, *, config: ManagerConfig, output_dir: Path, interval_s: float = 4.0, enabled: bool = True) -> None:
        self.config = config
        self.output_dir = output_dir
        self.interval_s = max(1.0, float(interval_s))
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        index = 0
        while not self._stop.wait(self.interval_s):
            index += 1
            capture_keyframes(self.config, self.output_dir / f"sample_{index:03d}", enabled=True)


def webui_base_url(config: ManagerConfig) -> str:
    explicit = os.environ.get("KSM_WEBUI_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    parsed = urlparse(config.pipeline.base_url)
    netloc = parsed.netloc
    if ":" in netloc:
        host = netloc.rsplit(":", 1)[0]
        netloc = f"{host}:8080"
    else:
        netloc = f"{netloc}:8080"
    return urlunparse((parsed.scheme or "http", netloc, "", "", "", "")).rstrip("/")
