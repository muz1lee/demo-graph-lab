"""Build a demonstration-wide object registry with one VLM call.

下游提取强制引用稳定的 registry id，以减少共指歧义。
"""

from __future__ import annotations

import re

from ..common import artifacts
from .stages import uniform_sample


_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def validate_registry(objects, total_frames: int) -> list[str]:
    """Validate canonical object entries before downstream extraction."""
    if not isinstance(objects, list) or not objects:
        return ["object registry must be a non-empty list"]
    errors, seen = [], set()
    required = {"id", "category", "distinguishers", "trace_aliases", "first_seen_frame"}
    for index, obj in enumerate(objects):
        prefix = f"object[{index}]"
        if not isinstance(obj, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = sorted(required - set(obj))
        extra = sorted(set(obj) - required)
        if missing:
            errors.append(f"{prefix} missing fields {missing}")
        if extra:
            errors.append(f"{prefix} has unknown fields {extra}")
        object_id = obj.get("id")
        if not isinstance(object_id, str) or not _ID_RE.fullmatch(object_id):
            errors.append(f"{prefix}.id must be snake_case")
        elif object_id in seen:
            errors.append(f"{prefix}.id is duplicated: {object_id!r}")
        else:
            seen.add(object_id)
        for field in ("category", "distinguishers"):
            if not isinstance(obj.get(field), str) or not obj.get(field):
                errors.append(f"{prefix}.{field} must be a non-empty string")
        aliases = obj.get("trace_aliases")
        if (not isinstance(aliases, list)
                or any(not isinstance(alias, str) or not alias for alias in aliases)):
            errors.append(f"{prefix}.trace_aliases must be a string list")
        first_seen = obj.get("first_seen_frame")
        if (not isinstance(first_seen, int) or isinstance(first_seen, bool)
                or not 0 <= first_seen < total_frames):
            errors.append(f"{prefix}.first_seen_frame is outside the video")
    return errors


def run(task: str, model: str | None = None, k_frames: int = 12) -> list[dict]:
    from ..common import llm

    run_dir = artifacts.latest_run_dir(task)
    artifacts.invalidate_outputs(run_dir, (
        "objects.json", "graph.json", "validation.json", "report.html",
        "stage_program.json", "perception_program.json", "policy.py",
        "compile_report.json", "compiled_graph.json", "compiled_objects.json",
    ))
    meta = artifacts.read_json(run_dir / "meta.json")
    prompt = (artifacts.PROMPT_ROOT / "object_registry.md").read_text().split("---", 1)[1]
    mentions = []
    if (run_dir / "trace.json").exists():
        for seg in artifacts.read_json(run_dir / "trace.json").get("segments", []):
            for key in ("manipulated_object", "target_object"):
                v = seg.get(key)
                if v and v != "none":
                    mentions.append(v)
    frames = uniform_sample(meta["frames"], k_frames)
    content = [{"type": "text", "text": prompt.replace("{N}", str(len(frames)))
                + f"\nTask instruction: {artifacts.read_json(run_dir / 'trace.json').get('instruction', task) if (run_dir / 'trace.json').exists() else task}"
                + f"\nUpstream trace object mentions: {sorted(set(mentions))}"}]
    for fr in frames:
        content.append({"type": "text", "text": f"[frame_idx={fr['frame_idx']}]"})
        content.append({"type": "image_url", "image_url": {"url":
            "data:image/jpeg;base64," + artifacts.b64_jpeg(run_dir / fr["file"])}})
    tag = "registry"
    input_refs = ["meta.json", "package:prompts/object_registry.md",
                  *(fr["file"] for fr in frames)]
    if (run_dir / "trace.json").exists():
        input_refs.append("trace.json")
    messages = [{"role": "user", "content": content}]
    request = llm.request_record(
        messages, tag=tag, role="object_registry", model=llm.resolve_model(model),
        max_tokens=1500, temperature=0.1, input_refs=input_refs)
    out = llm.cached_response(run_dir, tag, request)
    if out is None:
        out = llm.chat(messages, run_dir, tag=tag, model=model,
                       temperature=0.1, role="object_registry",
                       input_refs=input_refs)
    try:
        objects = llm.parse_json_block(out)
    except ValueError as error:
        llm.record_result(run_dir, tag, parse_error=str(error))
        raise
    errors = validate_registry(objects, meta["video"]["total_frames"])
    llm.record_result(run_dir, tag, parsed=objects, validation_errors=errors)
    if errors:
        raise ValueError("invalid object registry: " + "; ".join(errors))
    artifacts.write_json(run_dir / "objects.json", objects)
    print(f"[objects] {task}: {len(objects)} instances: "
          f"{[o.get('id') for o in objects]}")
    return objects
