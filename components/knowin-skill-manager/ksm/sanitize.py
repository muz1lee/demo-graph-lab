from __future__ import annotations

from typing import Any


_PRIVATE_REASONING_MARKER = "qw" + "en"
_LEGACY_OPENAI_COMPAT_PROVIDER = "openai" + "_compatible"
_CN_PRIVATE_PROVIDER = "\u5343\u95ee"


def sanitize_aspire_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_aspire_output(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_aspire_output(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_aspire_output(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    text = value.replace(_LEGACY_OPENAI_COMPAT_PROVIDER, "openai")
    text = text.replace(_CN_PRIVATE_PROVIDER, "vision-language")
    lowered = text.lower()
    marker = _PRIVATE_REASONING_MARKER
    while marker in lowered:
        start = lowered.index(marker)
        text = text[:start] + "vision-language" + text[start + len(marker) :]
        lowered = text.lower()
    return text
