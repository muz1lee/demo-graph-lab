from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import LLMConfig


@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    auth_mode: str
    temperature: float
    max_tokens: int
    timeout_s: float

    def safe_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["api_key"] = _redact(self.api_key)
        return payload


@dataclass(frozen=True)
class LLMResponse:
    provider: str
    model: str
    text: str
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GPTChatClient:
    def __init__(self, settings: ResolvedLLMConfig) -> None:
        if not settings.base_url:
            raise ValueError("GPT base_url is empty")
        if not settings.model:
            raise ValueError("GPT model is empty")
        if not settings.api_key:
            raise ValueError("OpenAI API key is empty; set OPENAI_API_KEY or the configured api_key_env")
        self.settings = settings

    def complete(self, messages: list[dict[str, Any]]) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
        }
        if _uses_gpt5_chat_settings(self.settings):
            payload["max_completion_tokens"] = self.settings.max_tokens
        else:
            payload["temperature"] = self.settings.temperature
            payload["max_tokens"] = self.settings.max_tokens
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            if self.settings.auth_mode.lower() == "raw":
                headers["Authorization"] = self.settings.api_key
            else:
                headers["Authorization"] = f"Bearer {self.settings.api_key}"
        req = Request(
            _completion_url(self.settings.base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.settings.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM service returned {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"failed to reach LLM service: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("LLM service returned non-object JSON")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"LLM service returned no choices: {data}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        return LLMResponse(
            provider=self.settings.provider,
            model=self.settings.model,
            text=_content_to_text(content),
            raw=data,
        )


class StaticResponseChatClient:
    def __init__(self, *, response_text: str, provider: str = "static", model: str = "static-response") -> None:
        self.response_text = response_text
        self.provider = provider
        self.model = model

    @classmethod
    def from_file(cls, path: str | Path) -> "StaticResponseChatClient":
        target = Path(path).expanduser().resolve()
        return cls(response_text=target.read_text(encoding="utf-8"), model=str(target))

    def complete(self, messages: list[dict[str, Any]]) -> LLMResponse:
        return LLMResponse(provider=self.provider, model=self.model, text=self.response_text, raw={"static": True})


def resolve_llm_config(config: LLMConfig, overrides: dict[str, Any] | None = None) -> ResolvedLLMConfig:
    overrides = {key: value for key, value in (overrides or {}).items() if value not in (None, "")}
    patched = replace(config, **{key: value for key, value in overrides.items() if hasattr(config, key)})
    env_values = _load_env_file(patched.env_file)
    base_url = patched.base_url or _lookup_env(patched.base_url_env, env_values)
    api_key = _lookup_env(patched.api_key_env, env_values)
    model = patched.model or _lookup_env(patched.model_env, env_values)
    return ResolvedLLMConfig(
        provider=patched.provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        auth_mode=patched.auth_mode,
        temperature=patched.temperature,
        max_tokens=patched.max_tokens,
        timeout_s=patched.timeout_s,
    )


def _completion_url(base_url: str) -> str:
    url = str(base_url).strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1") or url.endswith("/compatible-mode/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def _uses_gpt5_chat_settings(settings: ResolvedLLMConfig) -> bool:
    return settings.model.lower().startswith("gpt-5")


def _load_env_file(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def _lookup_env(name: str, env_values: dict[str, str]) -> str:
    if not name:
        return ""
    return os.environ.get(name) or env_values.get(name) or ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    chunks.append(str(text))
            elif item is not None:
                chunks.append(str(item))
        return "\n".join(chunks)
    return "" if content is None else str(content)


def _redact(value: str) -> str:
    if not value:
        return ""
    return f"len:{len(value)}"
