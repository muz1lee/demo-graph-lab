from __future__ import annotations

from pathlib import Path
from typing import Iterator

import av
from PIL import Image


def get_video_duration(path: str | Path) -> float | None:
    video_path = Path(path)
    with av.open(str(video_path)) as container:
        if container.duration is not None:
            # PyAV exposes container duration in AV_TIME_BASE units.
            return float(container.duration / av.time_base)
        streams = [stream for stream in container.streams if stream.type == "video"]
        for stream in streams:
            if stream.duration is not None and stream.time_base is not None:
                return float(stream.duration * stream.time_base)
    return _decode_duration(video_path)


def _decode_duration(path: Path) -> float | None:
    last_timestamp: float | None = None
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            timestamp = frame_time_sec(frame)
            if timestamp is not None:
                last_timestamp = timestamp
    return last_timestamp


def frame_time_sec(frame: av.VideoFrame) -> float | None:
    if frame.time is not None:
        return float(frame.time)
    if frame.pts is not None and frame.time_base is not None:
        return float(frame.pts * frame.time_base)
    return None


def resize_to_width(image: Image.Image, width: int) -> Image.Image:
    if width <= 0:
        raise ValueError("width must be > 0")
    if image.width == width:
        return image
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), resample=Image.Resampling.BOX)


def sample_frames_at_interval(
    path: str | Path,
    *,
    sample_sec: float,
    frame_width: int,
    start_sec: float = 0.0,
    end_sec: float | None = None,
) -> Iterator[tuple[float, Image.Image]]:
    if sample_sec <= 0:
        raise ValueError("sample_sec must be > 0")
    target_timestamp = max(0.0, float(start_sec))
    last_image: Image.Image | None = None

    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            timestamp = frame_time_sec(frame)
            if timestamp is None:
                continue
            if end_sec is not None and target_timestamp - 1e-6 > end_sec:
                break
            if timestamp + 1e-6 < target_timestamp:
                continue

            image = resize_to_width(frame.to_image().convert("RGB"), frame_width)
            last_image = image
            while timestamp + 1e-6 >= target_timestamp:
                if end_sec is not None and target_timestamp - 1e-6 > end_sec:
                    break
                yield round(target_timestamp, 6), image.copy()
                target_timestamp += sample_sec

    if last_image is not None and end_sec is not None:
        while target_timestamp <= end_sec + 1e-6:
            yield round(target_timestamp, 6), last_image.copy()
            target_timestamp += sample_sec


def sample_frames_uniform(
    path: str | Path,
    *,
    start_sec: float,
    end_sec: float,
    max_frames: int,
    frame_width: int,
) -> list[tuple[float, Image.Image]]:
    if end_sec <= start_sec:
        raise ValueError("end_sec must be greater than start_sec")
    if max_frames <= 0:
        raise ValueError("max_frames must be > 0")
    if max_frames == 1:
        targets = [(start_sec + end_sec) / 2.0]
    else:
        step = (end_sec - start_sec) / (max_frames - 1)
        targets = [start_sec + i * step for i in range(max_frames)]

    frames: list[tuple[float, Image.Image]] = []
    target_index = 0
    last_image: Image.Image | None = None
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            timestamp = frame_time_sec(frame)
            if timestamp is None:
                continue
            if target_index >= len(targets):
                break
            if timestamp + 1e-6 < targets[target_index]:
                continue
            image = resize_to_width(frame.to_image().convert("RGB"), frame_width)
            last_image = image
            while target_index < len(targets) and timestamp + 1e-6 >= targets[target_index]:
                target = round(float(targets[target_index]), 6)
                frames.append((target, image.copy()))
                target_index += 1

    if last_image is not None:
        while target_index < len(targets):
            target = round(float(targets[target_index]), 6)
            frames.append((target, last_image.copy()))
            target_index += 1
    return frames
