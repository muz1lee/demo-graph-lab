from pathlib import Path

from robot_subtask_seg.manifest import build_manifest_from_dir


def test_build_manifest_from_dir(tmp_path):
    task = tmp_path / "stack_blocks"
    task.mkdir()
    video = task / "stack-blocks.mp4"
    video.write_bytes(b"placeholder")
    manifest = build_manifest_from_dir(tmp_path, source="test")
    assert len(manifest.videos) == 1
    assert manifest.videos[0].task_class == "stack_blocks"
    assert "Stack the three blocks" in manifest.videos[0].instruction

