"""OpenRouter 客户端(OpenAI 兼容)。

只用于离线示范理解和 policy 编译；在线执行路径不调用。每次调用记账并受成本
上限保护。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import artifacts


class CostCapExceeded(RuntimeError):
    pass


def resolve_model(model: str | None = None) -> str:
    """Return the concrete model slug used for this call."""
    return model or os.environ.get("DGL_VLM_MODEL") or "anthropic/claude-opus-4.8"


def _call_dir(run_dir: Path, tag: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
        raise ValueError(f"invalid model call tag: {tag!r}")
    path = Path(run_dir) / "model_calls" / tag
    path.mkdir(parents=True, exist_ok=True)
    return path


def _redact_embedded_images(value):
    """Copy request metadata while replacing image bytes with a stable fingerprint."""
    if isinstance(value, dict):
        return {key: _redact_embedded_images(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_embedded_images(item) for item in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        fingerprint = hashlib.blake2b(value.encode(), digest_size=16).hexdigest()
        return f"<embedded-image:{fingerprint}; see input_refs>"
    return value


def request_record(
    messages: list,
    *,
    tag: str,
    role: str,
    model: str,
    max_tokens: int,
    temperature: float,
    input_refs: list[str] | None,
) -> dict:
    """Build the exact image-safe request metadata used for logging and resume checks."""
    return {
        "tag": tag,
        "role": role,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "input_refs": list(input_refs or []),
        "messages": _redact_embedded_images(messages),
    }


def cached_response(run_dir: Path, tag: str, expected_request: dict) -> str | None:
    """Reuse a raw reply only when its complete image-safe request is unchanged."""
    call_dir = Path(run_dir) / "model_calls" / tag
    raw_path = call_dir / "raw.txt"
    request_path = call_dir / "request.json"
    call_path = call_dir / "call.json"
    result_path = call_dir / "result.json"
    if not raw_path.exists() or not request_path.exists() or not call_path.exists():
        return None
    try:
        previous_request = json.loads(request_path.read_text())
        previous_call = json.loads(call_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if previous_request != expected_request or previous_call.get("status") != "ok":
        return None
    if result_path.exists():
        try:
            previous_result = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if (previous_result.get("parse_status") == "failed"
                or previous_result.get("validator_status") == "failed"):
            return None
    return raw_path.read_text()


def _archive_previous_call(call_dir: Path) -> None:
    """Keep one reviewable directory per explicit call without mixing attempts."""
    names = ("request.json", "raw.txt", "call.json", "result.json")
    existing = [call_dir / name for name in names if (call_dir / name).exists()]
    if not existing:
        return
    history = call_dir / "history"
    history.mkdir(exist_ok=True)
    number = 1
    while (history / f"call_{number:03d}").exists():
        number += 1
    destination = history / f"call_{number:03d}"
    destination.mkdir()
    for path in existing:
        path.replace(destination / path.name)


def record_result(
    run_dir: Path,
    tag: str,
    *,
    parsed=None,
    parse_error: str | None = None,
    validation_errors: list[str] | None = None,
) -> None:
    """Attach parse and schema-validation results to a completed model call."""
    if parse_error is not None:
        parse_status = "failed"
        validator_status = "not_run"
    else:
        parse_status = "passed"
        validator_status = "passed" if not validation_errors else "failed"
    (_call_dir(run_dir, tag) / "result.json").write_text(json.dumps({
        "parse_status": parse_status,
        "validator_status": validator_status,
        "parse_error": parse_error,
        "validation_errors": validation_errors or [],
        "parsed": parsed,
    }, ensure_ascii=False, indent=2))


def chat(messages: list, run_dir: Path, tag: str, model: str | None = None,
         max_tokens: int = 1500, temperature: float = 0.2, retries: int = 4,
         role: str | None = None, input_refs: list[str] | None = None) -> str:
    """Run one chat call and persist a reviewable, image-safe call record."""
    from openai import OpenAI  # lazy: mac 单测不需要

    model = resolve_model(model)
    role = role or tag
    prior_cost = artifacts.accumulated_cost(run_dir)
    cap = artifacts.cost_cap()
    if prior_cost >= cap:
        raise CostCapExceeded(
            f"recorded cost ${prior_cost:.2f} reached cap ${cap:.2f}"
        )
    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )
    call_dir = _call_dir(run_dir, tag)
    _archive_previous_call(call_dir)
    request = request_record(
        messages, tag=tag, role=role, model=model, max_tokens=max_tokens,
        temperature=temperature, input_refs=input_refs)
    (call_dir / "request.json").write_text(json.dumps(
        request, ensure_ascii=False, indent=2))
    (call_dir / "result.json").write_text(json.dumps({
        "parse_status": "not_recorded",
        "validator_status": "not_recorded",
    }, ensure_ascii=False, indent=2))
    last_err = None
    resp = None
    elapsed = None
    used_attempt = None
    for attempt in range(retries + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                extra_body={"usage": {"include": True}},
            )
            elapsed = round(time.time() - t0, 3)
            used_attempt = attempt + 1
            break
        except Exception as e:  # 限流走长退避(OpenRouter 新账号 10 RPM),其余指数退避
            last_err = e
            if attempt < retries:
                time.sleep(
                    min(60, 15 * (attempt + 1))
                    if "429" in str(e) else 2 ** attempt
                )
    if resp is None:
        (call_dir / "call.json").write_text(json.dumps({
            "tag": tag, "role": role, "model": model,
            "status": "failed", "attempts": retries + 1,
            "error": str(last_err),
        }, ensure_ascii=False, indent=2))
        raise RuntimeError(f"LLM call failed after {retries + 1} attempts: {last_err}")

    try:
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        usage_record = usage.model_dump() if usage is not None else {}
        response_model = getattr(resp, "model", None) or model
    except Exception as error:
        (call_dir / "call.json").write_text(json.dumps({
            "tag": tag, "role": role, "model": model,
            "status": "failed", "attempts": used_attempt,
            "error": f"invalid_response:{type(error).__name__}:{error}",
        }, ensure_ascii=False, indent=2))
        raise RuntimeError("model returned an invalid response") from error

    call_record = {
        "tag": tag, "role": role, "model": model,
        "response_model": response_model, "sec": elapsed,
        "prompt_tokens": usage_record.get("prompt_tokens"),
        "completion_tokens": usage_record.get("completion_tokens"),
        "cost": (usage_record.get("cost") or 0.0),
        "attempt": used_attempt, "status": "ok",
        "artifact": str(Path("model_calls") / tag),
    }
    try:
        (call_dir / "raw.txt").write_text(content)
        total = artifacts.append_cost(run_dir, call_record)
        (call_dir / "call.json").write_text(json.dumps(
            call_record, ensure_ascii=False, indent=2))
    except Exception as error:
        raise RuntimeError(
            "model call succeeded but local artifact persistence failed"
        ) from error
    if total > cap:
        raise CostCapExceeded(
            f"cumulative cost ${total:.2f} > cap ${cap:.2f}"
        )
    return content


def parse_json_block(text: str):
    """容错解析:剥 ```json 围栏、截取首尾大括号/中括号。解析失败抛 ValueError。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        t = t[t.find("\n") + 1:] if "\n" in t else t
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no parseable JSON in model output: {text[:200]!r}")
