from datetime import UTC, datetime, timedelta

import pytest

from tests.phase2_live_non_scored_soak import (
    MINIMUM_BOUNDARIES,
    MINIMUM_CONTINUOUS_DURATION,
    acceptance_threshold_met,
    prepare,
)


def test_soak_policy_requires_24_boundaries_and_6_continuous_hours():
    start = datetime(2026, 9, 10, tzinfo=UTC)
    assert MINIMUM_BOUNDARIES == 24
    assert MINIMUM_CONTINUOUS_DURATION == timedelta(hours=6)
    assert not acceptance_threshold_met(
        prepared_at=start,
        observed_at=start + timedelta(hours=6),
        completed_boundaries=23,
    )
    assert not acceptance_threshold_met(
        prepared_at=start,
        observed_at=start + timedelta(hours=5, minutes=59, seconds=59),
        completed_boundaries=24,
    )
    assert acceptance_threshold_met(
        prepared_at=start,
        observed_at=start + timedelta(hours=6),
        completed_boundaries=24,
    )


def test_prepare_rejects_a_shortened_soak_before_creating_evidence(tmp_path):
    case = tmp_path / "shortened"
    with pytest.raises(ValueError, match="at least 24"):
        prepare(case, 23)
    assert not case.exists()
