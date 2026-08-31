from __future__ import annotations

from datetime import UTC, datetime, timedelta


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetimes are forbidden")
    return value.astimezone(UTC)


def floor_quarter_hour(value: datetime) -> datetime:
    value = ensure_utc(value)
    return value.replace(minute=(value.minute // 15) * 15, second=0, microsecond=0)


def next_quarter_hour(value: datetime) -> datetime:
    floor = floor_quarter_hour(value)
    return floor if floor == value else floor + timedelta(minutes=15)

