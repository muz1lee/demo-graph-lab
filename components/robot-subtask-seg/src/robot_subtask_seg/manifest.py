from __future__ import annotations

import json
from pathlib import Path

from robot_subtask_seg.schema import Manifest, VideoItem


ROBODOJO_INSTRUCTIONS: dict[str, str] = {
    "align_blocks": "Use the set square to push the three blocks into a straight, aligned row.",
    "deposit_coin": "Pick up the coin and insert it into the coin bank.",
    "general_pickup": "Pick up the target object by 10 cm.",
    "insert_key": "Pick up the key and insert it into the key slot.",
    "insert_tubes": "Insert the three tubes into the rack one by one.",
    "plug_in_charger": "Plug the charger into the power strip.",
    "pour_balls_into_vase": "Pour all the balls from the cup into the vase.",
    "push_T": "Push the T-shaped block to align it with the pad.",
    "push_T_random": "Push the T-shaped block to align it with the pad.",
    "put_bottles_into_dustbin": "Pick up the bottles and throw them into the dustbin.",
    "stack_blocks": "Stack the three blocks with different textures.",
    "stack_blocks_random": "Stack the three blocks with different textures.",
    "stack_bowls": "Stack the three bowls together.",
    "stack_bowls_random": "Stack the three bowls together.",
}


def build_manifest_from_dir(input_dir: str | Path, *, source: str) -> Manifest:
    root = Path(input_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    videos: list[VideoItem] = []
    for path in sorted(root.glob("*/*.mp4")):
        task_class = path.parent.name
        task_id = f"robodojo_{task_class}__{path.stem}"
        videos.append(
            VideoItem(
                task_id=task_id,
                task_class=task_class,
                instruction=ROBODOJO_INSTRUCTIONS.get(task_class, ""),
                video_path=str(path),
                source=source,
                metadata={"input_dir": str(root)},
            )
        )
    if not videos:
        raise ValueError(f"no mp4 videos found under {root}")
    return Manifest(source=source, videos=videos)


def load_manifest(path: str | Path) -> Manifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest.model_validate(data)


def write_manifest(manifest: Manifest, path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")

