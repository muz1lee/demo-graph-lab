from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PipelineConfig:
    mode: str
    base_url: str
    poll_interval_s: float
    timeout_s: float


@dataclass(frozen=True)
class ArtifactConfig:
    candidates_dir: Path
    runs_dir: Path


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    base_url_env: str
    api_key_env: str
    model: str
    model_env: str
    auth_mode: str
    env_file: Path | None
    temperature: float
    max_tokens: int
    timeout_s: float


@dataclass(frozen=True)
class SkillLibraryConfig:
    root: Path
    top_k: int = 4
    snippet_chars: int = 1200
    max_chars: int = 6000


def _default_skill_library_config() -> SkillLibraryConfig:
    return SkillLibraryConfig(root=Path("skill_library").resolve())


@dataclass(frozen=True)
class ManagerConfig:
    root_dir: Path
    kw_repo: Path
    k1_dir: Path
    test_skill_dir: str
    pipeline: PipelineConfig
    artifacts: ArtifactConfig
    llm: LLMConfig
    skill_library: SkillLibraryConfig = field(default_factory=_default_skill_library_config)

    @property
    def test_skill_abs_dir(self) -> Path:
        return self.k1_dir / self.test_skill_dir


def load_config(path: str | Path) -> ManagerConfig:
    config_path = Path(path).expanduser().resolve()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: {config_path}")
    root_dir = config_path.parents[1]
    pipeline_data = data.get("pipeline") or {}
    artifact_data = data.get("artifacts") or {}
    llm_data = data.get("llm") or {}
    skill_library_data = data.get("skill_library") or {}
    return ManagerConfig(
        root_dir=root_dir,
        kw_repo=_path(data["kw_repo"]),
        k1_dir=_path(data["k1_dir"]),
        test_skill_dir=str(data.get("test_skill_dir") or "knowin_skills/knowin_skill_manager_tests").strip("/"),
        pipeline=PipelineConfig(
            mode=str(pipeline_data.get("mode", "direct")),
            base_url=str(pipeline_data.get("base_url", "http://127.0.0.1:8000")).rstrip("/"),
            poll_interval_s=float(pipeline_data.get("poll_interval_s", 0.5)),
            timeout_s=float(pipeline_data.get("timeout_s", 180.0)),
        ),
        artifacts=ArtifactConfig(
            candidates_dir=_resolve(root_dir, artifact_data.get("candidates_dir", "candidates")),
            runs_dir=_resolve(root_dir, artifact_data.get("runs_dir", "runs")),
        ),
        llm=LLMConfig(
            provider=str(llm_data.get("provider", "openai")),
            base_url=str(llm_data.get("base_url", "https://api.openai.com/v1")),
            base_url_env=str(llm_data.get("base_url_env", "OPENAI_BASE_URL")),
            api_key_env=str(llm_data.get("api_key_env", "OPENAI_API_KEY")),
            model=str(llm_data.get("model", "gpt-5.5")),
            model_env=str(llm_data.get("model_env", "OPENAI_MODEL")),
            auth_mode=str(llm_data.get("auth_mode", "bearer")),
            env_file=_optional_path(llm_data.get("env_file")),
            temperature=float(llm_data.get("temperature", 1.0)),
            max_tokens=int(llm_data.get("max_tokens", 12000)),
            timeout_s=float(llm_data.get("timeout_s", 120.0)),
        ),
        skill_library=SkillLibraryConfig(
            root=_resolve(root_dir, skill_library_data.get("root", "skill_library")),
            top_k=int(skill_library_data.get("top_k", 4)),
            snippet_chars=int(skill_library_data.get("snippet_chars", 1200)),
            max_chars=int(skill_library_data.get("max_chars", 6000)),
        ),
    )


def _path(value: Any) -> Path:
    return Path(str(value)).expanduser().resolve()


def _resolve(root: Path, value: Any) -> Path:
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _optional_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    return _path(value)
