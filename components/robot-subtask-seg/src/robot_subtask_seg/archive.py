from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil


def archive_run(
    run_dir: str | Path,
    *,
    archive_root: str | Path,
    reason: str,
    now: datetime | None = None,
) -> Path:
    source = Path(run_dir)
    if not source.exists():
        raise FileNotFoundError(f"run_dir does not exist: {source}")
    root = Path(archive_root)
    root.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    dest = root / f"{source.name}_{stamp}_archived"
    suffix = 1
    while dest.exists():
        dest = root / f"{source.name}_{stamp}_archived_{suffix}"
        suffix += 1
    shutil.move(str(source), str(dest))
    (dest / "ARCHIVE_REASON.txt").write_text(reason.strip() + "\n", encoding="utf-8")
    return dest
