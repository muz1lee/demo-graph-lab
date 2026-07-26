from robot_subtask_seg.normalize import extract_json_object, normalize_segmentation, normalize_segments


def test_extract_json_object_from_fenced_response():
    raw = '```json\n{"segments":[{"start_sec":0,"end_sec":1,"subtask":"pick"}]}\n```'
    assert extract_json_object(raw)["segments"][0]["subtask"] == "pick"


def test_normalize_segments_sorts_and_drops_invalid():
    segments = normalize_segments(
        {
            "segments": [
                {"start_sec": 2.0, "end_sec": 1.0, "subtask": "bad"},
                {"start_sec": 1.0, "end_sec": 2.0, "subtask": "place"},
                {"start_sec": -1.0, "end_sec": 0.5, "subtask": "pick"},
            ]
        }
    )
    assert [(s.start_sec, s.end_sec, s.subtask) for s in segments] == [
        (0.0, 0.5, "pick"),
        (1.0, 2.0, "place"),
    ]


def test_normalize_segmentation_keeps_eef_object_role_fields():
    result = normalize_segmentation(
        {
            "demonstration_method": [
                "grasp coin",
                "handover coin",
                "align coin plane with coin-bank slot",
            ],
            "quality_warnings": None,
            "segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 2.0,
                    "subtask": "Hand over the coin to the other arm.",
                    "actor_arm": "right_arm",
                    "receiver_arm": "left_arm",
                    "eef_event": "handover",
                    "motion_type": "handover",
                    "manipulated_object": "coin",
                    "target_object": "other_gripper",
                    "target_role": "other_gripper",
                    "requires_bimanual": True,
                    "requires_alignment": False,
                    "role": "core",
                    "confidence": 1.5,
                    "risk_flags": None,
                },
                {
                    "start_sec": 2.0,
                    "end_sec": 3.0,
                    "subtask": "Align the coin with the slot.",
                    "eef_event": "align",
                    "motion_type": "fine_alignment",
                    "manipulated_object": "coin",
                    "target_object": "coin_bank_slot",
                    "target_role": "slot",
                    "requires_alignment": True,
                },
            ],
        }
    )

    assert result.quality_warnings == []
    assert result.demonstration_method[1] == "handover coin"
    handover = result.segments[0]
    assert handover.manipulated_object == "coin"
    assert handover.target_object == "other_gripper"
    assert handover.requires_bimanual is True
    assert handover.confidence == 1.0
    align = result.segments[1]
    assert align.eef_event == "align"
    assert align.requires_alignment is True
