from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .config import ManagerConfig


@dataclass(frozen=True)
class RunResult:
    skill_path: str
    submit_result: dict[str, Any]
    final_status: dict[str, Any]
    duration_s: float
    pre_run: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PipelineDirectClient:
    def __init__(self, base_url: str, *, timeout_s: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def pipeline_status(self) -> dict[str, Any]:
        return self._get_json("/pipeline_status")

    def run_skill(self, skill_path: str, kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {
            "action": "skill",
            "name": skill_path,
            "kwargs": json.dumps(kwargs or {}, ensure_ascii=False),
        }
        return self._get_json("/run", params)

    def reset_pipeline(self) -> str:
        return self._get_text("/reset")

    def reset_layout(self, *, target: str = "manifest", timeout_s: float = 8.0) -> dict[str, Any]:
        return self._post_json(
            "/api/reset_layout",
            {"target": target, "timeout": float(timeout_s)},
        )

    def run_control(self, name: str, kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {
            "action": "ctrl",
            "name": name,
            "kwargs": json.dumps(kwargs or {}, ensure_ascii=False),
        }
        return self._get_json("/run", params)

    def run_reasoning(self, name: str, kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {
            "action": "reasoning",
            "name": name,
            "kwargs": json.dumps(kwargs or {}, ensure_ascii=False),
        }
        return self._get_json("/run", params)

    def wait_done(self, *, poll_interval_s: float, timeout_s: float) -> dict[str, Any]:
        deadline = time.time() + float(timeout_s)
        last: dict[str, Any] = {}
        while True:
            last = self.pipeline_status()
            if not bool(last.get("running")):
                return last
            if time.time() > deadline:
                raise TimeoutError(f"pipeline did not finish after {timeout_s:.1f}s")
            time.sleep(max(0.05, float(poll_interval_s)))

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        with urlopen(url, timeout=self.timeout_s) as resp:
            data = resp.read().decode("utf-8")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError(f"pipeline response is not an object: {url}")
        return payload

    def _get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        with urlopen(url, timeout=self.timeout_s) as resp:
            return resp.read().decode("utf-8", "replace")

    def _post_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.webui_base_url}{path}"
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=self.timeout_s) as resp:
            data = resp.read().decode("utf-8")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError(f"pipeline response is not an object: {url}")
        return parsed

    @property
    def webui_base_url(self) -> str:
        explicit = os.environ.get("KSM_WEBUI_BASE_URL")
        if explicit:
            return explicit.rstrip("/")
        parsed = urlparse(self.base_url)
        netloc = parsed.netloc
        if ":" in netloc:
            host = netloc.rsplit(":", 1)[0]
            netloc = f"{host}:8080"
        else:
            netloc = f"{netloc}:8080"
        return urlunparse((parsed.scheme or "http", netloc, "", "", "", "")).rstrip("/")


def run_published_skill(
    *,
    config: ManagerConfig,
    skill_path: str,
    kwargs: dict[str, Any] | None = None,
    reset_before_run: bool = True,
) -> RunResult:
    if config.pipeline.mode != "direct":
        raise ValueError(f"unsupported pipeline mode: {config.pipeline.mode}")
    client = PipelineDirectClient(config.pipeline.base_url)
    started = time.time()
    pre_run = reset_runtime_state(client) if reset_before_run else {"reset_before_run": False}
    submit = client.run_skill(skill_path, kwargs)
    status = client.wait_done(
        poll_interval_s=config.pipeline.poll_interval_s,
        timeout_s=config.pipeline.timeout_s,
    )
    return RunResult(
        skill_path=skill_path,
        submit_result=submit,
        final_status=status,
        duration_s=time.time() - started,
        pre_run=pre_run,
    )


def reset_runtime_state(client: PipelineDirectClient) -> dict[str, Any]:
    result: dict[str, Any] = {"reset_before_run": True, "layout_reset": None, "pipeline_reset": None, "go_home": []}
    layout_reset = client.reset_layout(target="manifest", timeout_s=8.0)
    result["layout_reset"] = layout_reset
    if not bool(layout_reset.get("ok", False)):
        raise RuntimeError(f"WebUI layout reset failed: {layout_reset}")
    result["pipeline_reset"] = client.reset_pipeline()
    for arm_id in (0, 1):
        try:
            go_home = client.run_control("go_home", {"arm_id": arm_id})
            result["go_home"].append({"arm_id": arm_id, "result": go_home})
        except Exception as exc:
            result["go_home"].append({"arm_id": arm_id, "error": repr(exc)})
            raise
    return result
