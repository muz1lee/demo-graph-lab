from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from adapters.knowin_world import EvalProtocolError, KnowinWorldAdapter


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append((method, path, body))
        return self.responses.pop(0)


def _reset_response() -> dict[str, Any]:
    return {
        "session_id": "session-1",
        "run_id": "run-1",
        "task_id": "insert_tubes_000",
        "mode": "skill",
        "state": {
            "entities": {"tube": {"exact_pose": [1.0, 2.0, 3.0]}},
            "probes": {"task_success": True},
        },
    }


def _skill_response(**updates: Any) -> dict[str, Any]:
    response = {
        "run_id": "run-1",
        "path": "knowin_skills/generated/insert_tube.yaml",
        "args": {},
        "status": "succeeded",
        "duration_s": 1.5,
        "error": None,
        "queue_id": 17,
        "quiescence_confirmed": True,
        "state": {"entities": {"tube": {"exact_pose": [4.0, 5.0, 6.0]}}},
    }
    response.update(updates)
    return response


def test_lifecycle_is_sync_and_strips_ground_truth_state() -> None:
    transport = FakeTransport(
        [
            _reset_response(),
            _skill_response(),
            {
                "session_id": "session-1",
                "run_id": "run-1",
                "execution_success": True,
                "task_success": False,
                "run_success": False,
            },
        ]
    )
    adapter = KnowinWorldAdapter(transport)

    session = adapter.reset("insert_tubes_000")
    skill = adapter.execute_skill("knowin_skills/generated/insert_tube.yaml")
    final = adapter.finalize()

    assert session.session_id == "session-1"
    assert skill.queue_id == "17"
    assert skill.quiescence_confirmed is True
    assert final.provenance == "privileged_oracle"
    assert "state" not in session.__slots__
    assert "state" not in skill.__slots__
    assert "exact_pose" not in repr(session)
    assert "exact_pose" not in repr(skill)
    assert [call[:2] for call in transport.calls] == [
        ("POST", "/session/reset"),
        ("POST", "/skill"),
        ("POST", "/session/finalize"),
    ]


def test_method_receipt_digest_does_not_fingerprint_ground_truth_state() -> None:
    first = KnowinWorldAdapter(FakeTransport([_reset_response()])).reset(
        "insert_tubes_000"
    )
    changed = _reset_response()
    changed["state"] = {"entities": {"tube": {"exact_pose": [99.0, 98.0, 97.0]}}}
    second = KnowinWorldAdapter(FakeTransport([changed])).reset(
        "insert_tubes_000"
    )

    assert first.response_digest == second.response_digest


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"queue_id": None}, "queue_id"),
        ({"queue_id": "None"}, "queue_id"),
        ({"quiescence_confirmed": False}, "quiescence"),
        ({"status": "timeout"}, "status"),
    ],
)
def test_skill_fails_closed_without_exact_terminal_quiescence(
    updates: dict[str, Any],
    message: str,
) -> None:
    adapter = KnowinWorldAdapter(
        FakeTransport([_reset_response(), _skill_response(**updates)])
    )
    adapter.reset("insert_tubes_000")

    with pytest.raises(EvalProtocolError, match=message):
        adapter.execute_skill("knowin_skills/generated/insert_tube.yaml")
    with pytest.raises(EvalProtocolError, match="cannot finalize"):
        adapter.finalize()


def test_skill_path_cannot_escape_skill_root() -> None:
    adapter = KnowinWorldAdapter(FakeTransport([_reset_response()]))
    adapter.reset("insert_tubes_000")

    with pytest.raises(ValueError, match="knowin_skills"):
        adapter.execute_skill("../scene/private.yaml")
