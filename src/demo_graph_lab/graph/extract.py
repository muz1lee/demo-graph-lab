"""Extract constraints, acceptance checks, and typed holes for each stage."""

from __future__ import annotations

import json
import math
from pathlib import Path

from ..common import artifacts
from . import vocab


_RESERVED = {"name", "args", "stage", "confidence", "evidence_frames",
             "provenance", "notes", "votes"}


def _norm_item(item: dict) -> dict:
    """归一 args:列表→按词表槽位;缺失→从顶层多余键抢救(模型常把参数平铺在顶层)。"""
    name, args = item.get("name"), item.get("args")
    if not args:
        args = {k: v for k, v in item.items() if k not in _RESERVED}
    if isinstance(args, list):
        spec = vocab.CONSTRAINT_VOCAB.get(name, {})
        slots = spec.get("args", []) + spec.get("optional", [])
        args = {slots[i] if i < len(slots) else f"arg{i}": v
                for i, v in enumerate(args)}
    elif not isinstance(args, dict):
        args = {"arg0": args}
    item["args"] = args
    return item


def _canon(item: dict) -> tuple:
    _norm_item(item)
    return (item.get("name"), json.dumps(item["args"], sort_keys=True, ensure_ascii=False))


def merge_samples(samples: list[dict], key_fields=("constraints", "acceptance")) -> dict:
    """多数票合并 k 个采样。约束/验收按 (name,args) 计票,洞按 (name,type)。纯函数,可单测。"""
    k = len(samples)
    need = 1 if k == 1 else math.ceil(k / 2)
    merged = {}
    for field in key_fields:
        buckets: dict[tuple, list[dict]] = {}
        for s in samples:
            for it in s.get(field, []) or []:
                buckets.setdefault(_canon(it), []).append(it)
        out = []
        for key, items in buckets.items():
            if len(items) < need:
                continue
            base = dict(items[0])
            confs = [i.get("confidence", 0.5) for i in items]
            base["confidence"] = round(sum(confs) / len(confs) * len(items) / k, 3)
            ev = sorted({f for i in items for f in (i.get("evidence_frames") or [])})
            base["evidence_frames"] = ev[:8]
            base["votes"] = f"{len(items)}/{k}"
            base.setdefault("provenance", "demo_video")
            out.append(base)
        merged[field] = sorted(out, key=lambda x: -float(x["confidence"]))
    hole_buckets: dict[tuple, list[dict]] = {}
    for s in samples:
        for h in s.get("holes", []) or []:
            hole_buckets.setdefault((h.get("name"), h.get("type")), []).append(h)
    merged["holes"] = [dict(v[0], votes=f"{len(v)}/{k}")
                       for v in hole_buckets.values() if len(v) >= need]
    return merged


def _stage_messages(prompt: str, instruction: str, stage: dict,
                    frames: list[dict], run_dir: Path) -> list:
    txt = (prompt.replace("{N}", str(len(frames)))
           + f"\n\nTask instruction: {instruction}"
           + f"\nSTAGE: {stage['name']} (label: {stage.get('label','')};"
             f" manipulated: {stage.get('manipulated_object')};"
             f" target: {stage.get('target_object')})")
    content = [{"type": "text", "text": txt}]
    for fr in frames:
        content.append({"type": "text",
                        "text": f"[frame_idx={fr['frame_idx']} t={fr['t_sec']}s]"})
        content.append({"type": "image_url", "image_url": {"url":
            "data:image/jpeg;base64," + artifacts.b64_jpeg(run_dir / fr["file"])}})
    return [{"role": "user", "content": content}]


def run(task: str, k: int = 5, model: str | None = None,
        max_stages: int | None = None) -> Path:
    from ..common import llm

    run_dir = artifacts.latest_run_dir(task)
    meta = artifacts.read_json(run_dir / "meta.json")
    stages = artifacts.read_json(run_dir / "stages.json")
    keyframes = artifacts.read_json(run_dir / "keyframes.json")
    instruction = meta.get("task", task)
    if (run_dir / "trace.json").exists():
        instruction = artifacts.read_json(run_dir / "trace.json").get("instruction", instruction)
    prompt = (artifacts.PROMPT_ROOT / "constraint_extract.md").read_text().split("---", 1)[1]
    if (run_dir / "objects.json").exists():
        objects = artifacts.read_json(run_dir / "objects.json")
        prompt += ("\n\n## OBJECT REGISTRY (use EXACTLY these ids for object references)\n"
                   + json.dumps(objects, ensure_ascii=False))

    graph = {"task": task, "instruction": instruction, "model": model,
             "k": k, "stages": []}
    todo = stages[:max_stages] if max_stages else stages
    for st in todo:
        frames = keyframes.get(str(st["index"]), [])
        msgs = _stage_messages(prompt, instruction, st, frames, run_dir)
        samples, parse_fail = [], 0
        for i in range(k):
            out = llm.chat(msgs, run_dir, tag=f"extract_s{st['index']}_k{i}", model=model,
                           temperature=0.1 if k == 1 else 0.3)
            try:
                samples.append(llm.parse_json_block(out))
            except ValueError:
                parse_fail += 1
        artifacts.write_json(run_dir / "samples" / f"stage{st['index']:02d}.json", samples)
        merged = merge_samples(samples) if samples else {"constraints": [], "acceptance": [], "holes": []}
        so_votes = [json.dumps(s.get("stage_objects"), sort_keys=True)
                    for s in samples if s.get("stage_objects")]
        stage_objects = (json.loads(max(set(so_votes), key=so_votes.count))
                         if so_votes else None)
        graph["stages"].append({**{k_: st[k_] for k_ in
                                   ("index", "name", "label", "start_sec", "end_sec")},
                                "role": st.get("role", "core"),
                                "stage_objects": stage_objects,
                                **merged, "k_valid": len(samples), "parse_fail": parse_fail})
        print(f"[extract] stage {st['index']} {st['name']}: "
              f"{len(merged['constraints'])} constraints, {len(merged['holes'])} holes "
              f"({len(samples)}/{k} parsed)")
    artifacts.write_json(run_dir / "graph.json", graph)
    return run_dir / "graph.json"
