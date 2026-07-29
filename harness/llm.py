"""OpenRouter 客户端(OpenAI 兼容)。仅编译期使用;每次调用记账并受成本上限保护。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import util


class CostCapExceeded(RuntimeError):
    pass


def chat(messages: list, run_dir: Path, tag: str, model: str | None = None,
         max_tokens: int = 1500, temperature: float = 0.2, retries: int = 4) -> str:
    """一次 chat 调用。返回文本;usage/cost 记入 run_dir/cost.jsonl。"""
    from openai import OpenAI  # lazy: mac 单测不需要

    model = model or os.environ.get("HARNESS_VLM_MODEL", "anthropic/claude-opus-4.8")
    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                extra_body={"usage": {"include": True}},
            )
            content = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            u = usage.model_dump() if usage is not None else {}
            total = util.append_cost(run_dir, {
                "tag": tag, "model": model, "sec": round(time.time() - t0, 1),
                "prompt_tokens": u.get("prompt_tokens"),
                "completion_tokens": u.get("completion_tokens"),
                "cost": (u.get("cost") or 0.0),
            })
            if total > util.cost_cap():
                raise CostCapExceeded(
                    f"cumulative cost ${total:.2f} > cap ${util.cost_cap():.2f}")
            return content
        except CostCapExceeded:
            raise
        except Exception as e:  # 限流走长退避(OpenRouter 新账号 10 RPM),其余指数退避
            last_err = e
            time.sleep(min(60, 15 * (attempt + 1)) if "429" in str(e) else 2 ** attempt)
    raise RuntimeError(f"LLM call failed after {retries + 1} attempts: {last_err}")


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
