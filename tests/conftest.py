from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hype_autopilot.data.models import Candle


@pytest.fixture
def candles() -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(symbol="HYPE", interval="1h", open_time=start + timedelta(hours=i),
               close_time=start + timedelta(hours=i + 1), open=100 + i,
               high=102 + i, low=99 + i, close=101 + i, volume=1000 + i,
               received_at=start + timedelta(hours=i + 1))
        for i in range(60)
    ]

