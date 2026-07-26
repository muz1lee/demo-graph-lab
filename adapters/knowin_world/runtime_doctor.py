"""Read-only runtime compatibility and reproducibility checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._json import content_digest, jsonable, require_mapping


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    name: str
    base_url: str
    health_path: str
    schema: str

    @property
    def url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.health_path}"


@dataclass(frozen=True, slots=True)
class GitRepositorySpec:
    name: str
    path: str
    require_clean_for_golden: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeDoctorConfig:
    endpoints: tuple[EndpointSpec, ...]
    repositories: tuple[GitRepositorySpec, ...]
    timeout_s: float = 2.0

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> "RuntimeDoctorConfig":
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = require_mapping(json.load(stream), name="runtime config")
        environment = environment if environment is not None else os.environ
        timeout = payload.get("timeout_s", 2.0)
        if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
            raise ValueError("runtime config timeout_s must be positive")
        raw_endpoints = payload.get("endpoints")
        if not isinstance(raw_endpoints, Sequence) or isinstance(raw_endpoints, str | bytes):
            raise ValueError("runtime config endpoints must be an array")
        endpoints = tuple(_endpoint_spec(item) for item in raw_endpoints)
        raw_repositories = payload.get("repositories", ())
        if not isinstance(raw_repositories, Sequence) or isinstance(raw_repositories, str | bytes):
            raise ValueError("runtime config repositories must be an array")
        repositories: list[GitRepositorySpec] = []
        for raw in raw_repositories:
            item = require_mapping(raw, name="repository config")
            env_name = _required_string(item, "path_env")
            resolved = environment.get(env_name)
            if not resolved:
                resolved = f"<missing-env:{env_name}>"
            repositories.append(
                GitRepositorySpec(
                    name=_required_string(item, "name"),
                    path=resolved,
                    require_clean_for_golden=item.get("require_clean_for_golden", True) is True,
                )
            )
        return cls(
            endpoints=endpoints,
            repositories=tuple(repositories),
            timeout_s=float(timeout),
        )


@dataclass(frozen=True, slots=True)
class EndpointCheck:
    name: str
    ok: bool
    schema: str
    latency_ms: float
    response_digest: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class GitRevision:
    name: str
    commit: str | None
    dirty: bool | None
    dirty_entry_count: int | None
    dirty_digest: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class RuntimeManifest:
    schema: str
    generated_at_unix_ns: int
    endpoints: tuple[EndpointCheck, ...]
    repositories: tuple[GitRevision, ...]
    golden_eligible: bool
    manifest_digest: str

    def to_dict(self) -> dict[str, Any]:
        return jsonable(self)


JsonFetcher = Callable[[str, float], Mapping[str, Any]]


class RuntimeDoctor:
    def __init__(
        self,
        config: RuntimeDoctorConfig,
        *,
        fetch_json: JsonFetcher | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._fetch_json = fetch_json or _fetch_json
        self._clock_ns = clock_ns
        self._monotonic = monotonic

    def run(self) -> RuntimeManifest:
        endpoint_checks = tuple(self._check_endpoint(spec) for spec in self._config.endpoints)
        revisions = tuple(collect_git_revision(spec) for spec in self._config.repositories)
        required_clean = {
            spec.name for spec in self._config.repositories if spec.require_clean_for_golden
        }
        golden = all(check.ok for check in endpoint_checks) and all(
            revision.error is None
            and (revision.name not in required_clean or revision.dirty is False)
            for revision in revisions
        )
        body = {
            "schema": "demo_graph.runtime_manifest.v1",
            "generated_at_unix_ns": self._clock_ns(),
            "endpoints": endpoint_checks,
            "repositories": revisions,
            "golden_eligible": golden,
        }
        return RuntimeManifest(
            schema=body["schema"],
            generated_at_unix_ns=body["generated_at_unix_ns"],
            endpoints=endpoint_checks,
            repositories=revisions,
            golden_eligible=golden,
            manifest_digest=content_digest(body),
        )

    def _check_endpoint(self, spec: EndpointSpec) -> EndpointCheck:
        started = self._monotonic()
        try:
            payload = require_mapping(
                self._fetch_json(spec.url, self._config.timeout_s),
                name=f"{spec.name} health response",
            )
            _validate_schema(spec.schema, payload)
            return EndpointCheck(
                name=spec.name,
                ok=True,
                schema=spec.schema,
                latency_ms=(self._monotonic() - started) * 1_000.0,
                response_digest=content_digest(payload),
                error=None,
            )
        except Exception as exc:
            return EndpointCheck(
                name=spec.name,
                ok=False,
                schema=spec.schema,
                latency_ms=(self._monotonic() - started) * 1_000.0,
                response_digest=None,
                error=f"{type(exc).__name__}: {exc}",
            )


def collect_git_revision(spec: GitRepositorySpec) -> GitRevision:
    if spec.path.startswith("<missing-env:"):
        return GitRevision(spec.name, None, None, None, None, spec.path)
    path = Path(spec.path)
    if not path.is_dir():
        return GitRevision(spec.name, None, None, None, None, "repository path is not a directory")
    try:
        commit = _git(path, "rev-parse", "HEAD").strip()
        status = _git(path, "status", "--porcelain=v1", "--untracked-files=normal")
    except (OSError, subprocess.CalledProcessError) as exc:
        return GitRevision(
            spec.name,
            None,
            None,
            None,
            None,
            f"{type(exc).__name__}: git inspection failed",
        )
    entries = tuple(line for line in status.splitlines() if line)
    return GitRevision(
        name=spec.name,
        commit=commit,
        dirty=bool(entries),
        dirty_entry_count=len(entries),
        dirty_digest=f"sha256:{hashlib.sha256(status.encode('utf-8')).hexdigest()}",
        error=None,
    )


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    return result.stdout


def _fetch_json(url: str, timeout_s: float) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"health request failed: {exc.reason}") from exc
    try:
        return require_mapping(json.loads(raw), name="health response")
    except json.JSONDecodeError as exc:
        raise RuntimeError("health response was not JSON") from exc


def _validate_schema(schema: str, payload: Mapping[str, Any]) -> None:
    if schema == "webui_pipeline_status.v1":
        if payload.get("ok") is not True:
            raise ValueError("WebUI pipeline proxy requires ok=true")
        if payload.get("cmd") != "pipeline_status":
            raise ValueError("WebUI pipeline proxy returned the wrong command")
        nested = payload.get("response")
        if not isinstance(nested, Mapping):
            raise ValueError("WebUI pipeline proxy requires object response")
        _validate_schema("pipeline_status.v1", nested)
        return
    if schema == "pipeline_status.v1":
        if not isinstance(payload.get("running"), bool):
            raise ValueError("pipeline status requires boolean running")
        for key in ("results", "success", "failed", "queued_skills"):
            if key in payload and not isinstance(payload[key], list):
                raise ValueError(f"pipeline status {key} must be an array")
        return
    if schema == "eval_health.v1":
        required = {"scene": str, "scene_id": str, "task_ids": list, "busy": bool}
        for key, expected_type in required.items():
            if not isinstance(payload.get(key), expected_type):
                raise ValueError(f"eval health {key} must be {expected_type.__name__}")
        if not all(isinstance(item, str) for item in payload["task_ids"]):
            raise ValueError("eval health task_ids must contain strings")
        if payload.get("session") is not None and not isinstance(payload.get("session"), str):
            raise ValueError("eval health session must be string or null")
        return
    raise ValueError(f"unknown runtime health schema: {schema}")


def _endpoint_spec(value: Any) -> EndpointSpec:
    payload = require_mapping(value, name="endpoint config")
    base_url = _required_string(payload, "base_url")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("endpoint base_url must use http:// or https://")
    health_path = _required_string(payload, "health_path")
    if not health_path.startswith("/") or health_path == "/state":
        raise ValueError("health_path must be an absolute non-privileged API path")
    return EndpointSpec(
        name=_required_string(payload, "name"),
        base_url=base_url,
        health_path=health_path,
        schema=_required_string(payload, "schema"),
    )


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Knowin World runtime compatibility")
    parser.add_argument("--config", required=True, help="path to a sanitized runtime JSON config")
    args = parser.parse_args(argv)
    manifest = RuntimeDoctor(RuntimeDoctorConfig.from_file(args.config)).run()
    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if manifest.golden_eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
