"""Extract constraints, acceptance checks, and typed holes for each stage."""

from __future__ import annotations

import json
from pathlib import Path

from ..common import artifacts
from . import validate, vocab


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
    return (
        item.get("name"),
        json.dumps(item["args"], sort_keys=True, ensure_ascii=False),
        item.get("holds"),
    )


def _stable_tuple(values: tuple) -> tuple[str, ...]:
    return tuple(json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values)


def _hole_canon(hole: dict) -> tuple:
    return (
        hole.get("name"),
        hole.get("type"),
        hole.get("frame"),
        hole.get("purpose"),
        hole.get("resolver"),
        json.dumps(hole.get("anchor"), sort_keys=True, ensure_ascii=False),
        hole.get("solver_hint"),
    )


def merge_samples(samples: list[dict], key_fields=("constraints", "acceptance"),
                  total_samples: int | None = None) -> dict:
    """多数票合并 k 个采样，时间语义和完整 hole 契约分别计票。"""
    k = len(samples) if total_samples is None else total_samples
    if k <= 0 or k < len(samples):
        raise ValueError("total_samples must be positive and cover all samples")
    need = k // 2 + 1
    merged = {}
    for field in key_fields:
        buckets: dict[tuple, list[dict]] = {}
        for s in samples:
            seen = set()
            for it in s.get(field, []) or []:
                key = _canon(it)
                if key not in seen:
                    buckets.setdefault(key, []).append(it)
                    seen.add(key)
        out = []
        for key in sorted(buckets, key=_stable_tuple):
            items = buckets[key]
            if len(items) < need:
                continue
            base = dict(min(
                items,
                key=lambda item: json.dumps(
                    item, sort_keys=True, ensure_ascii=False),
            ))
            confs = [i.get("confidence", 0.5) for i in items]
            base["confidence"] = round(sum(confs) / len(confs) * len(items) / k, 3)
            ev = sorted({f for i in items for f in (i.get("evidence_frames") or [])})
            base["evidence_frames"] = ev[:8]
            base["votes"] = f"{len(items)}/{k}"
            base.setdefault("provenance", "demo_video")
            out.append(base)
        merged[field] = sorted(
            out,
            key=lambda item: (-float(item["confidence"]), *_stable_tuple(_canon(item))),
        )
    hole_buckets: dict[tuple, list[dict]] = {}
    for s in samples:
        seen = set()
        for h in s.get("holes", []) or []:
            key = _hole_canon(h)
            if key not in seen:
                hole_buckets.setdefault(key, []).append(h)
                seen.add(key)
    merged["holes"] = [
        dict(
            min(hole_buckets[key], key=lambda hole: json.dumps(
                hole, sort_keys=True, ensure_ascii=False)),
            votes=f"{len(hole_buckets[key])}/{k}",
        )
        for key in sorted(hole_buckets, key=_stable_tuple)
        if len(hole_buckets[key]) >= need
    ]
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
    from ..demo.registry import validate_registry

    if k <= 0:
        raise ValueError("k must be positive")
    run_dir = artifacts.latest_run_dir(task)
    artifacts.invalidate_outputs(run_dir, (
        "graph.json", "validation.json", "report.html", "stage_program.json",
        "policy.py", "compile_report.json", "compiled_graph.json",
        "compiled_objects.json",
    ))
    meta = artifacts.read_json(run_dir / "meta.json")
    stages = artifacts.read_json(run_dir / "stages.json")
    keyframes = artifacts.read_json(run_dir / "keyframes.json")
    objects_path = run_dir / "objects.json"
    if not objects_path.exists():
        raise FileNotFoundError(
            f"{objects_path} is required; run `dgl objects --task {task}` first")
    objects = artifacts.read_json(objects_path)
    registry_errors = validate_registry(objects, meta["video"]["total_frames"])
    if registry_errors:
        raise ValueError("invalid objects.json: " + "; ".join(registry_errors))
    registry_ids = {obj["id"] for obj in objects}
    instruction = meta.get("task", task)
    if (run_dir / "trace.json").exists():
        instruction = artifacts.read_json(run_dir / "trace.json").get("instruction", instruction)
    prompt = (artifacts.PROMPT_ROOT / "constraint_extract.md").read_text().split("---", 1)[1]
    prompt += ("\n\n## OBJECT REGISTRY (use EXACTLY these ids for object references)\n"
               + json.dumps(objects, ensure_ascii=False))

    graph = {"task": task, "instruction": instruction, "model": llm.resolve_model(model),
             "k": k, "stages": []}
    todo = stages[:max_stages] if max_stages else stages
    for st in todo:
        frames = keyframes.get(str(st["index"]), [])
        if not isinstance(frames, list) or not frames:
            raise ValueError(
                f"stage {st['index']} has no keyframes; rerun `dgl keyframes` "
                "with --per-stage > 0"
            )
        allowed_evidence_frames = {frame["frame_idx"] for frame in frames}
        msgs = _stage_messages(prompt, instruction, st, frames, run_dir)
        samples, parse_fail, schema_fail, replayed = [], 0, 0, 0
        input_refs = [
            "meta.json", "stages.json", "keyframes.json", "objects.json",
            "package:prompts/constraint_extract.md", *(fr["file"] for fr in frames),
        ]
        for i in range(k):
            tag = f"extract_s{st['index']}_k{i}"
            temperature = 0.1 if k == 1 else 0.3
            expected_request = llm.request_record(
                msgs, tag=tag, role="constraint_extract",
                model=llm.resolve_model(model), max_tokens=1500,
                temperature=temperature, input_refs=input_refs)
            out = llm.cached_response(run_dir, tag, expected_request)
            if out is None:
                out = llm.chat(msgs, run_dir, tag=tag, model=model,
                               temperature=temperature,
                               role="constraint_extract", input_refs=input_refs)
            else:
                replayed += 1
            try:
                parsed = llm.parse_json_block(out)
            except ValueError as error:
                parse_fail += 1
                llm.record_result(run_dir, tag, parse_error=str(error))
                continue
            validation_errors = validate.validate_stage_sample(
                parsed, st, registry_ids,
                total_frames=meta["video"]["total_frames"],
                allowed_evidence_frames=allowed_evidence_frames,
            )
            llm.record_result(
                run_dir, tag, parsed=parsed, validation_errors=validation_errors)
            if validation_errors:
                schema_fail += 1
                continue
            samples.append(parsed)
        artifacts.write_json(run_dir / "samples" / f"stage{st['index']:02d}.json", samples)
        merged = merge_samples(samples, total_samples=k)
        so_votes = [json.dumps(s.get("stage_objects"), sort_keys=True)
                    for s in samples if s.get("stage_objects")]
        stage_objects = None
        if so_votes:
            winner = max(set(so_votes), key=so_votes.count)
            if so_votes.count(winner) >= k // 2 + 1:
                stage_objects = json.loads(winner)
        graph["stages"].append({**{k_: st[k_] for k_ in
                                   ("index", "name", "label", "start_sec", "end_sec")},
                                "role": st.get("role", "core"),
                                "stage_objects": stage_objects,
                                **merged, "k_valid": len(samples),
                                "parse_fail": parse_fail, "schema_fail": schema_fail})
        print(f"[extract] stage {st['index']} {st['name']}: "
              f"{len(merged['constraints'])} constraints, {len(merged['holes'])} holes "
              f"({len(samples)}/{k} valid; parse_fail={parse_fail}; "
              f"schema_fail={schema_fail}; replayed={replayed})")
    artifacts.write_json(run_dir / "graph.json", graph)
    return run_dir / "graph.json"
