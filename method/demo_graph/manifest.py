"""运行清单：记录依赖、摘要与 API 审计，用于可复现实验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ._json import content_digest, to_primitive


def _require_digest(value: str, name: str) -> None:
    if not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{name} must be sha256:<64 hex>")


@dataclass(frozen=True, slots=True)
class RunManifest:
    """一次试验的可复现元数据。

    正式 golden 跑必须拒绝 dirty Knowin World / data；开发实验可记 dirty hash
    但只能标记为 non-golden。
    """

    ksm_commit: str
    knowin_world_commit: str
    knowin_world_dirty_hash: str | None
    data_asset_lock: str
    config_digest: str
    model_ids: tuple[str, ...]
    seed: int
    graph_digest: str
    code_digest: str
    api_audit_digests: tuple[str, ...]
    golden: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("ksm_commit", self.ksm_commit),
            ("knowin_world_commit", self.knowin_world_commit),
            ("data_asset_lock", self.data_asset_lock),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} must not be empty")
        _require_digest(self.config_digest, "config_digest")
        _require_digest(self.graph_digest, "graph_digest")
        _require_digest(self.code_digest, "code_digest")
        object.__setattr__(self, "model_ids", tuple(self.model_ids))
        object.__setattr__(self, "api_audit_digests", tuple(self.api_audit_digests))
        for digest in self.api_audit_digests:
            _require_digest(digest, "api_audit_digests item")
        if self.golden and self.knowin_world_dirty_hash:
            raise ValueError("dirty Knowin World dependency cannot be marked golden")

    @property
    def digest(self) -> str:
        return content_digest(self)

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)

    @classmethod
    def from_parts(
        cls,
        *,
        ksm_commit: str,
        knowin_world_commit: str,
        knowin_world_dirty_hash: str | None,
        data_asset_lock: str,
        config: Mapping[str, Any],
        model_ids: Sequence[str],
        seed: int,
        graph_digest: str,
        code_digest: str,
        api_audit_digests: Sequence[str],
        golden: bool = False,
    ) -> "RunManifest":
        return cls(
            ksm_commit=ksm_commit,
            knowin_world_commit=knowin_world_commit,
            knowin_world_dirty_hash=knowin_world_dirty_hash,
            data_asset_lock=data_asset_lock,
            config_digest=content_digest(dict(config)),
            model_ids=tuple(model_ids),
            seed=int(seed),
            graph_digest=graph_digest,
            code_digest=code_digest,
            api_audit_digests=tuple(api_audit_digests),
            golden=golden,
        )
