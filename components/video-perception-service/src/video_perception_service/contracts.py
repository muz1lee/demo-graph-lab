from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PointQuery(BaseModel):
    query_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    timestamp_sec: float = Field(ge=0.0)
    x: float
    y: float


class TrackPointsRequest(BaseModel):
    video_path: str = Field(min_length=1)
    queries: list[PointQuery] = Field(min_length=1, max_length=512)
    query_coordinate_width: int | None = Field(default=None, ge=2)
    query_coordinate_height: int | None = Field(default=None, ge=2)
    target_fps: float = Field(default=10.0, gt=0.0, le=30.0)
    inference_width: int = Field(default=512, ge=128, le=1024)
    max_frames: int = Field(default=300, ge=2, le=1200)
    backward_tracking: bool = True

    @field_validator("queries")
    @classmethod
    def unique_query_ids(cls, value: list[PointQuery]) -> list[PointQuery]:
        ids = [query.query_id for query in value]
        if len(ids) != len(set(ids)):
            raise ValueError("query_id values must be unique")
        return value
