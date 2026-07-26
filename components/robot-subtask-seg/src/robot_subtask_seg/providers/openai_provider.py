from __future__ import annotations

import base64
import os
from pathlib import Path

from robot_subtask_seg.providers.base import ProviderError


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        model: str = "gpt-4.1",
        api_key: str | None = None,
        temperature: float = 0.1,
        max_retries: int = 4,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is required for openai provider")

    def generate_json(self, *, prompt: str, image_paths: list[Path]) -> str:
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise ProviderError("openai package is required") from exc

        client = OpenAI(api_key=self.api_key)
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for path in image_paths:
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                }
            )

        last_error: Exception | None = None
        include_temperature = True
        for _ in range(max(1, self.max_retries)):
            try:
                request: dict[str, object] = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                    "response_format": {"type": "json_object"},
                }
                if include_temperature:
                    request["temperature"] = self.temperature
                response = client.chat.completions.create(
                    **request,
                )
                text = response.choices[0].message.content
                if not text:
                    raise ProviderError(f"empty response from {self.model}")
                return text
            except Exception as exc:  # pragma: no cover - network/API dependent
                last_error = exc
                if include_temperature and _rejects_temperature(exc):
                    include_temperature = False
        raise ProviderError(f"OpenAI request failed after retries: {last_error}") from last_error


def _rejects_temperature(exc: Exception) -> bool:
    message = str(exc).lower()
    return "temperature" in message and (
        "unsupported" in message or "does not support" in message
    )
