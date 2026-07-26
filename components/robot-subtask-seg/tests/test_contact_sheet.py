from pathlib import Path

import av
import numpy as np
from PIL import Image

from robot_subtask_seg.contact_sheet import build_episode_contact_sheets
from robot_subtask_seg.video import get_video_duration


def write_video(path: Path, *, num_frames: int = 12, fps: int = 6) -> None:
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        for value in range(num_frames):
            frame = av.VideoFrame.from_ndarray(
                np.full((48, 64, 3), 32 + value * 8, dtype=np.uint8),
                format="rgb24",
            )
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def test_build_episode_contact_sheets(tmp_path):
    video = tmp_path / "demo.mp4"
    write_video(video)
    sheets = build_episode_contact_sheets(
        video,
        output_dir=tmp_path / "sheets",
        sample_sec=0.5,
        frame_width=64,
        frames_per_sheet=2,
        columns=2,
    )
    assert sheets
    assert sheets[0].path.exists()
    image = Image.open(sheets[0].path)
    assert image.width == 128
    assert image.height == 48
    assert sheets[0].timestamps[0] == 0.0


def test_get_video_duration_uses_seconds(tmp_path):
    video = tmp_path / "demo.mp4"
    write_video(video, num_frames=12, fps=6)
    duration = get_video_duration(video)
    assert duration is not None
    assert 1.5 <= duration <= 2.5
