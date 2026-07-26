from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from robot_subtask_seg.video import sample_frames_at_interval, sample_frames_uniform


@dataclass(frozen=True, slots=True)
class TimestampedContactSheet:
    path: Path
    index: int
    timestamps: tuple[float, ...]
    width: int
    height: int
    rows: int
    columns: int

    @property
    def start_sec(self) -> float:
        return self.timestamps[0]

    @property
    def end_sec(self) -> float:
        return self.timestamps[-1]


def draw_timestamp_badge(image: Image.Image, timestamp: float) -> Image.Image:
    result = image.copy()
    draw = ImageDraw.Draw(result)
    label = f"{timestamp:06.2f}s"
    font = _load_font()
    draw.rectangle((0, 0, 72, 26), fill=(0, 0, 0))
    draw.text((7, 3), label, fill=(255, 255, 255), font=font)
    return result


def _load_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, 17)
        except OSError:
            pass
    return ImageFont.load_default()


def build_contact_sheet_image(
    frames: Sequence[tuple[float, Image.Image]],
    *,
    rows: int,
    columns: int,
) -> Image.Image:
    if not frames:
        raise ValueError("frames must be non-empty")
    if rows <= 0 or columns <= 0:
        raise ValueError("rows and columns must be > 0")
    frame_width, frame_height = frames[0][1].size
    sheet = Image.new("RGB", (frame_width * columns, frame_height * rows), color=(0, 0, 0))
    for index, (timestamp, image) in enumerate(frames):
        x = (index % columns) * frame_width
        y = (index // columns) * frame_height
        sheet.paste(draw_timestamp_badge(image, timestamp), (x, y))
    return sheet


def save_contact_sheet(
    frames: Sequence[tuple[float, Image.Image]],
    *,
    path: Path,
    index: int,
    rows: int,
    columns: int,
    quality: int,
) -> TimestampedContactSheet:
    image = build_contact_sheet_image(frames, rows=rows, columns=columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=quality, subsampling=2)
    return TimestampedContactSheet(
        path=path,
        index=index,
        timestamps=tuple(float(t) for t, _ in frames),
        width=image.width,
        height=image.height,
        rows=rows,
        columns=columns,
    )


def build_episode_contact_sheets(
    video_path: str | Path,
    *,
    output_dir: str | Path,
    sample_sec: float = 0.5,
    frame_width: int = 224,
    frames_per_sheet: int = 20,
    columns: int = 5,
    quality: int = 95,
) -> list[TimestampedContactSheet]:
    if frames_per_sheet <= 0:
        raise ValueError("frames_per_sheet must be > 0")
    if columns <= 0:
        raise ValueError("columns must be > 0")
    if quality <= 0 or quality > 100:
        raise ValueError("quality must be between 1 and 100")

    out = Path(output_dir)
    rows = math.ceil(frames_per_sheet / columns)
    batch: list[tuple[float, Image.Image]] = []
    sheets: list[TimestampedContactSheet] = []
    for frame in sample_frames_at_interval(
        video_path,
        sample_sec=sample_sec,
        frame_width=frame_width,
    ):
        batch.append(frame)
        if len(batch) == frames_per_sheet:
            sheet_index = len(sheets) + 1
            sheets.append(
                save_contact_sheet(
                    batch,
                    path=out / f"sheet_{sheet_index:03d}.jpg",
                    index=sheet_index,
                    rows=rows,
                    columns=columns,
                    quality=quality,
                )
            )
            batch = []
    if batch:
        sheet_index = len(sheets) + 1
        sheets.append(
            save_contact_sheet(
                batch,
                path=out / f"sheet_{sheet_index:03d}.jpg",
                index=sheet_index,
                rows=rows,
                columns=columns,
                quality=quality,
            )
        )
    if not sheets:
        raise ValueError(f"video produced no frames: {video_path}")
    return sheets


def build_segment_contact_sheet(
    video_path: str | Path,
    *,
    output_path: str | Path,
    start_sec: float,
    end_sec: float,
    frame_width: int = 336,
    max_frames: int = 5,
    columns: int = 3,
    quality: int = 95,
) -> TimestampedContactSheet:
    frames = sample_frames_uniform(
        video_path,
        start_sec=start_sec,
        end_sec=end_sec,
        max_frames=max_frames,
        frame_width=frame_width,
    )
    if not frames:
        raise ValueError(f"segment produced no frames: {start_sec}-{end_sec}")
    rows = math.ceil(len(frames) / columns)
    return save_contact_sheet(
        frames,
        path=Path(output_path),
        index=1,
        rows=rows,
        columns=columns,
        quality=quality,
    )


def sheet_bytes(sheet: TimestampedContactSheet) -> bytes:
    return sheet.path.read_bytes()


def image_to_jpeg_bytes(image: Image.Image, *, quality: int = 95) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, subsampling=2)
    return output.getvalue()

