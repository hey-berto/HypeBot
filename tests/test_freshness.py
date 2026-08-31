from datetime import UTC, datetime, timedelta

from hype_autopilot.data.freshness import assess_freshness


def test_required_stale_or_missing_fails_closed():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    quality = assess_freshness(at, {"a": at - timedelta(seconds=11)}, {"a": 10, "b": 10},
                               {"a", "b"}, {"optional"})
    assert not quality.scoreable
    assert "a" in quality.stale_sources
    assert "MISSING_REQUIRED:b" in quality.rejection_reasons
    assert "optional" in quality.missing_optional_fields


def test_optional_missing_does_not_fail_scoreability():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    quality = assess_freshness(at, {"a": at}, {"a": 10, "optional": 10}, {"a"}, {"optional"})
    assert quality.scoreable
