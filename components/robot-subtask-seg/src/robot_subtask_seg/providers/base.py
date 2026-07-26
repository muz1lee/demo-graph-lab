from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TextVisionProvider(Protocol):
    name: str
    model: str

    def generate_json(self, *, prompt: str, image_paths: list[Path]) -> str:
        """Return a JSON string generated from text plus image inputs."""


class ProviderError(RuntimeError):
    pass

