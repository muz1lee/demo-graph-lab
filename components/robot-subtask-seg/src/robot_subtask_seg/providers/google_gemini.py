from __future__ import annotations

import os
from pathlib import Path

from robot_subtask_seg.providers.base import ProviderError


class GoogleGeminiProvider:
    name = "google_gemini"

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
        temperature: float = 0.1,
        max_retries: int = 4,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.api_key = api_key or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
        if not self.api_key:
            raise ProviderError("GOOGLE_GENERATIVE_AI_API_KEY is required for google_gemini")

    def generate_json(self, *, prompt: str, image_paths: list[Path]) -> str:
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise ProviderError("google-genai package is required") from exc

        client = genai.Client(api_key=self.api_key)
        contents: list[object] = [prompt]
        for path in image_paths:
            contents.append(
                types.Part.from_bytes(
                    data=Path(path).read_bytes(),
                    mime_type="image/jpeg",
                )
            )

        config = types.GenerateContentConfig(
            temperature=self.temperature,
            response_mime_type="application/json",
        )
        last_error: Exception | None = None
        for _ in range(max(1, self.max_retries)):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                text = getattr(response, "text", None)
                if not text:
                    raise ProviderError(f"empty response from {self.model}")
                return str(text)
            except Exception as exc:  # pragma: no cover - network/API dependent
                last_error = exc
        raise ProviderError(f"Gemini request failed after retries: {last_error}") from last_error
