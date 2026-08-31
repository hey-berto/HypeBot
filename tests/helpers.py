from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from hype_autopilot.data.models import (
    AssetContext, BboObservation, Candle, FundingObservation, ObservationClass,
)
from hype_autopilot.data.repository import Repository


def memory_repo() -> Repository:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    repo = Repository(db)
    repo.initialize()
    return repo


def candle_series(symbol: str, interval: str, end: datetime, count: int,
                  step: timedelta, base: float = 100.0) -> list[Candle]:
    start = end - count * step
    rows = []
    for index in range(count):
        open_time = start + index * step
        close_time = open_time + step
        price = base + index * 0.01 + (index % 7) * 0.02
        rows.append(Candle(
            symbol=symbol, interval=interval, open_time=open_time, close_time=close_time,
            open=price, high=price + 1.0, low=price - 1.0, close=price + 0.1,
            volume=1000 + index, trade_count=10, received_at=close_time,
            observation_class=ObservationClass.WARMUP,
        ))
    return rows


def populate_scoreable(repo: Repository, end: datetime | None = None) -> datetime:
    end = end or datetime(2026, 3, 5, 12, 0, tzinfo=UTC)
    for symbol in ("HYPE", "BTC"):
        repo.save_candles(candle_series(symbol, "15m", end, 400, timedelta(minutes=15), 100 if symbol == "HYPE" else 70000))
        repo.save_candles(candle_series(symbol, "1h", end, 1500, timedelta(hours=1), 100 if symbol == "HYPE" else 70000))
        repo.save_candles(candle_series(symbol, "4h", end, 400, timedelta(hours=4), 100 if symbol == "HYPE" else 70000))
    repo.save_candles(candle_series("HYPE", "1m", end, 180, timedelta(minutes=1)))
    repo.save_candles(candle_series("HYPE", "5m", end, 180, timedelta(minutes=5)))
    for index in range(100):
        at = end - timedelta(minutes=15 * (99 - index))
        repo.save_asset_context(AssetContext(
            symbol="HYPE", source_timestamp=at, received_at=at, mark_price=120 + index * .01,
            mid_price=120 + index * .01, oracle_price=120, funding_rate=0.0001,
            open_interest=1_000_000 + index * 1000, day_notional_volume=50_000_000,
        ))
    repo.save_bbo(BboObservation(symbol="HYPE", source_timestamp=end, received_at=end,
                                 bid_price=120.0, bid_size=10, ask_price=120.01, ask_size=9))
    repo.save_funding(FundingObservation(
        symbol="HYPE", source_timestamp=end - timedelta(hours=167 - index),
        received_at=end - timedelta(hours=167 - index), funding_rate=0.0001 + index * 1e-7,
        observation_class=ObservationClass.WARMUP,
    ) for index in range(168))
    return end
