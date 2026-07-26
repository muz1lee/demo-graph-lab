from __future__ import annotations

import hashlib
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import ManagerConfig
from .io import safe_id

MAX_PUBLISHED_FILENAME_BYTES = 180


@dataclass(frozen=True)
class PublishResult:
    candidate_id: str
    source_path: str
    published_path: str
    pipeline_skill_path: str
    test_skill_dir: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def publish_skill(
    *,
    config: ManagerConfig,
    candidate_id: str,
    source_path: str | Path,
    publish_subdir: str | None = None,
) -> PublishResult:
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(str(source))
    cleaned_id = safe_id(candidate_id)
    subdir = str(publish_subdir or "").strip("/")
    target_dir = config.test_skill_abs_dir / subdir if subdir else config.test_skill_abs_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _published_yaml_filename(cleaned_id)
    shutil.copy2(source, target)
    pipeline_dir = "/".join(part for part in (config.test_skill_dir.strip("/"), subdir) if part)
    pipeline_skill_path = f"{pipeline_dir}/{target.name}" if pipeline_dir else target.name
    return PublishResult(
        candidate_id=cleaned_id,
        source_path=str(source),
        published_path=str(target),
        pipeline_skill_path=pipeline_skill_path,
        test_skill_dir=pipeline_dir or config.test_skill_dir,
    )


def _published_yaml_filename(candidate_id: str, *, max_bytes: int = MAX_PUBLISHED_FILENAME_BYTES) -> str:
    filename = f"{candidate_id}.yaml"
    if len(filename.encode("utf-8")) <= int(max_bytes):
        return filename
    digest = hashlib.sha1(candidate_id.encode("utf-8")).hexdigest()[:10]
    suffix = f"_{digest}.yaml"
    budget = max(8, int(max_bytes) - len(suffix.encode("utf-8")))
    prefix = candidate_id.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip("_-")
    if not prefix:
        prefix = "skill"
    return f"{prefix}{suffix}"
