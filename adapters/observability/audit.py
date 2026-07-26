"""Method Broker 调用审计：只写脱敏摘要，不写原始观测大包。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from ..method_broker import ApiCallRecord


@dataclass
class AuditLog:
    records: list[dict] = field(default_factory=list)
    _digests: list[str] = field(default_factory=list)

    def append(self, record: ApiCallRecord) -> None:
        payload = asdict(record)
        payload["digest"] = record.digest
        self.records.append(payload)
        self._digests.append(record.digest)

    def digests(self) -> tuple[str, ...]:
        return tuple(self._digests)


def write_audit_records(path: str | Path, records: Iterable[ApiCallRecord]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for record in records:
        item = asdict(record)
        item["digest"] = record.digest
        payload.append(item)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out
