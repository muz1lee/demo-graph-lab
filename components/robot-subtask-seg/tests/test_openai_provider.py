from robot_subtask_seg.providers.openai_provider import _rejects_temperature


def test_rejects_temperature_only_for_explicit_parameter_errors() -> None:
    assert _rejects_temperature(
        ValueError("Unsupported value: temperature does not support 0.1")
    )
    assert not _rejects_temperature(ValueError("temporary network failure"))
