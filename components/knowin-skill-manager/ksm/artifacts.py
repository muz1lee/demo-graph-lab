from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .io import write_json


def new_run_dir(root: str | Path, candidate_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(root) / f"{stamp}_{candidate_id}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_report(run_dir: str | Path, payload: dict[str, Any]) -> Path:
    return write_json(Path(run_dir) / "report.json", payload)

