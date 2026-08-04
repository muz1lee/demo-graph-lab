"""Build a demonstration-wide object registry with one VLM call.

下游提取强制引用稳定的 registry id，以减少共指歧义。
"""

from __future__ import annotations

from ..common import artifacts


def run(task: str, model: str | None = None, k_frames: int = 12) -> list[dict]:
    from ..common import llm

    run_dir = artifacts.latest_run_dir(task)
    meta = artifacts.read_json(run_dir / "meta.json")
    prompt = (artifacts.PROMPT_ROOT / "object_registry.md").read_text().split("---", 1)[1]
    mentions = []
    if (run_dir / "trace.json").exists():
        for seg in artifacts.read_json(run_dir / "trace.json").get("segments", []):
            for key in ("manipulated_object", "target_object"):
                v = seg.get(key)
                if v and v != "none":
                    mentions.append(v)
    frames = meta["frames"][:: max(1, len(meta["frames"]) // k_frames)][:k_frames]
    content = [{"type": "text", "text": prompt.replace("{N}", str(len(frames)))
                + f"\nTask instruction: {artifacts.read_json(run_dir / 'trace.json').get('instruction', task) if (run_dir / 'trace.json').exists() else task}"
                + f"\nUpstream trace object mentions: {sorted(set(mentions))}"}]
    for fr in frames:
        content.append({"type": "text", "text": f"[frame_idx={fr['frame_idx']}]"})
        content.append({"type": "image_url", "image_url": {"url":
            "data:image/jpeg;base64," + artifacts.b64_jpeg(run_dir / fr["file"])}})
    out = llm.chat([{"role": "user", "content": content}], run_dir, tag="registry",
                   model=model, temperature=0.1)
    objects = llm.parse_json_block(out)
    artifacts.write_json(run_dir / "objects.json", objects)
    print(f"[objects] {task}: {len(objects)} instances: "
          f"{[o.get('id') for o in objects]}")
    return objects
