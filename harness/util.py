"""公共工具:.env 加载、run 目录、成本账本、JSON IO。零第三方依赖。"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_ROOT.parent


def load_env() -> dict:
    """读 repo 根 .env(存在则)并合入 os.environ;返回合并视图。不打印任何值。"""
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    return dict(os.environ)


def data_root() -> Path:
    return Path(os.environ.get(
        "HARNESS_DATA_ROOT",
        str(Path.home() / "data/upstream/robot-subtask-seg"))).expanduser()


def new_run_dir(task: str, kind: str = "harness") -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    d = HARNESS_ROOT / "runs" / f"{kind}_{task}_{ts}"
    (d / "frames").mkdir(parents=True, exist_ok=True)
    return d


def latest_run_dir(task: str) -> Path:
    runs = sorted((HARNESS_ROOT / "runs").glob(f"harness_{task}_*"))
    if not runs:
        raise FileNotFoundError(f"no run dir for task {task}; run `ingest` first")
    return runs[-1]


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path: Path):
    return json.loads(Path(path).read_text())


def append_cost(run_dir: Path, record: dict) -> float:
    """追加一条 LLM 调用成本记录,返回累计成本(USD)。"""
    ledger = run_dir / "cost.jsonl"
    with ledger.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    total = 0.0
    for line in ledger.read_text().splitlines():
        total += json.loads(line).get("cost", 0.0) or 0.0
    return total


def cost_cap() -> float:
    return float(os.environ.get("HARNESS_COST_CAP", "8.0"))


def b64_jpeg(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()
