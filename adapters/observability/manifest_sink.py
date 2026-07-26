"""从 Broker 审计与配置拼装 RunManifest。"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from method.demo_graph.manifest import RunManifest

from ..method_broker import ApiCallRecord, MethodBroker


def build_run_manifest_from_broker(
    *,
    broker: MethodBroker,
    ksm_commit: str,
    knowin_world_commit: str,
    knowin_world_dirty_hash: str | None,
    data_asset_lock: str,
    config: Mapping[str, Any],
    model_ids: Sequence[str],
    seed: int,
    graph_digest: str,
    code_digest: str,
    golden: bool = False,
) -> RunManifest:
    audits: tuple[ApiCallRecord, ...] = broker.audit_records
    return RunManifest.from_parts(
        ksm_commit=ksm_commit,
        knowin_world_commit=knowin_world_commit,
        knowin_world_dirty_hash=knowin_world_dirty_hash,
        data_asset_lock=data_asset_lock,
        config=config,
        model_ids=model_ids,
        seed=seed,
        graph_digest=graph_digest,
        code_digest=code_digest,
        api_audit_digests=tuple(record.digest for record in audits),
        golden=golden,
    )
