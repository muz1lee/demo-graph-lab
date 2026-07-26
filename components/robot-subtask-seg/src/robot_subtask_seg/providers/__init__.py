from __future__ import annotations

from typing import Any

from robot_subtask_seg.providers.base import TextVisionProvider
from robot_subtask_seg.providers.fake import FakeProvider
from robot_subtask_seg.providers.google_gemini import GoogleGeminiProvider
from robot_subtask_seg.providers.openai_provider import OpenAIProvider


def build_provider(config: dict[str, Any]) -> TextVisionProvider:
    provider_cfg = config.get("provider", {})
    name = provider_cfg.get("name", "fake")
    if name == "fake":
        return FakeProvider()
    if name == "google_gemini":
        return GoogleGeminiProvider(
            model=provider_cfg.get("model", "gemini-2.5-flash"),
            temperature=float(provider_cfg.get("temperature", 0.1)),
            max_retries=int(provider_cfg.get("max_retries", 4)),
        )
    if name == "openai":
        return OpenAIProvider(
            model=provider_cfg.get("model", "gpt-4.1"),
            temperature=float(provider_cfg.get("temperature", 0.1)),
            max_retries=int(provider_cfg.get("max_retries", 4)),
        )
    raise ValueError(f"unknown provider: {name}")
