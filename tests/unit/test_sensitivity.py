from gisnet.validation.sensitivity import _comparison_row


def test_sensitivity_change_flag_and_unavailable_status() -> None:
    changed = _comparison_row("S01", "A", "count", "base", "alt", 100, 125)
    assert changed["absolute_relative_change"] == 0.25
    assert changed["major_change"] is True
    assert changed["primary_result_overwritten"] is False
    unavailable = _comparison_row(
        "S08", "Registry", "count", "provisional", "reviewed", None, None, status="not_available"
    )
    assert unavailable["major_change"] is False
    assert unavailable["status"] == "not_available"
