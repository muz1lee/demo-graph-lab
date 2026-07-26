from __future__ import annotations

import base64

from PIL import Image

from robot_subtask_seg.video_evidence import (
    GroundingDetection,
    Sam3Client,
    link_detection_tracklets,
)


class _Response:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "success": True,
            "detections": [
                {
                    "bbox": [10, 20, 30, 50],
                    "score": 0.8,
                    "mask": base64.b64encode(b"png").decode("ascii"),
                    "stage": "initial",
                }
            ],
        }


class _Session:
    def __init__(self) -> None:
        self.payload = None

    def post(self, url: str, *, json: dict, timeout: float) -> _Response:
        self.payload = {"url": url, "json": json, "timeout": timeout}
        return _Response()


def _observation(
    observation_id: str,
    *,
    frame_index: int,
    timestamp_sec: float,
    bbox: list[float],
) -> dict:
    return {
        "observation_id": observation_id,
        "frame_index": frame_index,
        "timestamp_sec": timestamp_sec,
        "prompt": "object",
        "bbox_xyxy": bbox,
        "center_xy": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
        "score": 0.9,
        "mask_path": None,
        "frame_path": f"frame_{frame_index}.jpg",
        "segment_indices": [],
        "coordinate_frame": "image_pixels",
        "evidence_source": "test",
        "source_details": {},
    }


def test_sam3_client_uses_text_prompt_contract() -> None:
    session = _Session()
    client = Sam3Client("http://sam3.test", session=session)

    detections = client.segment(Image.new("RGB", (32, 24)), text_prompt="target part")

    assert detections == [
        GroundingDetection(
            bbox_xyxy=(10.0, 20.0, 30.0, 50.0),
            score=0.8,
            mask_base64=base64.b64encode(b"png").decode("ascii"),
            source_details={"stage": "initial"},
        )
    ]
    assert session.payload is not None
    assert session.payload["url"] == "http://sam3.test/segment"
    assert session.payload["json"]["text_prompt"] == "target part"
    assert isinstance(session.payload["json"]["image"], str)


def test_detection_linking_preserves_two_nearby_trajectories() -> None:
    observations = [
        _observation("a0", frame_index=0, timestamp_sec=0.0, bbox=[10, 10, 30, 30]),
        _observation("b0", frame_index=0, timestamp_sec=0.0, bbox=[110, 10, 130, 30]),
        _observation("a1", frame_index=1, timestamp_sec=1.0, bbox=[15, 10, 35, 30]),
        _observation("b1", frame_index=1, timestamp_sec=1.0, bbox=[105, 10, 125, 30]),
    ]

    tracks = link_detection_tracklets(observations, frame_width=200, sample_sec=1.0)

    assert len(tracks) == 2
    assert sorted(track["observation_count"] for track in tracks) == [2, 2]
    assert sorted(track["displacement_xy_px"][0] for track in tracks) == [-5.0, 5.0]


def test_detection_linking_does_not_claim_far_jump() -> None:
    observations = [
        _observation("a0", frame_index=0, timestamp_sec=0.0, bbox=[10, 10, 30, 30]),
        _observation("a1", frame_index=1, timestamp_sec=1.0, bbox=[170, 10, 190, 30]),
    ]

    tracks = link_detection_tracklets(observations, frame_width=200, sample_sec=1.0)

    assert len(tracks) == 2
    assert all(track["observation_count"] == 1 for track in tracks)
