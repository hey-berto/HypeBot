from datetime import UTC, datetime, timedelta

from hype_autopilot.features.engine import build_features
from hype_autopilot.features.indicators import atr, ema, rsi, trailing_zscore
from hype_autopilot.data.models import FundingObservation
from tests.helpers import memory_repo
from tests.helpers import candle_series


def test_future_candles_and_funding_do_not_change_features():
    cutoff = datetime(2026, 1, 10, tzinfo=UTC)
    bars15 = candle_series("HYPE", "15m", cutoff, 120, timedelta(minutes=15))
    bars1h = candle_series("HYPE", "1h", cutoff, 100, timedelta(hours=1))
    funding = [(cutoff - timedelta(hours=80 - i), 0.0001 + i * 1e-6) for i in range(80)]
    baseline = build_features(cutoff, bars15, bars1h, funding)
    future15 = candle_series("HYPE", "15m", cutoff + timedelta(minutes=15), 1, timedelta(minutes=15), 9999)
    future1h = candle_series("HYPE", "1h", cutoff + timedelta(hours=1), 1, timedelta(hours=1), 9999)
    changed = build_features(cutoff, bars15 + future15, bars1h + future1h,
                             funding + [(cutoff + timedelta(hours=1), 99.0)])
    assert baseline == changed


def test_indicators_only_change_when_completed_input_is_supplied():
    cutoff = datetime(2026, 1, 10, tzinfo=UTC)
    bars = candle_series("HYPE", "1h", cutoff, 60, timedelta(hours=1))
    closes = [item.close for item in bars]
    assert ema(closes, 20) == ema(closes, 20)
    assert rsi(closes, 14) == rsi(closes, 14)
    assert atr(bars, 14) == atr(bars, 14)


def test_funding_zscore_is_trailing_only_and_windowed():
    values = [float(index) for index in range(200)]
    assert trailing_zscore(values, 168, 72) == trailing_zscore(values[-168:], 168, 72)
    assert trailing_zscore(values[:71], 168, 72) is None


def test_identical_funding_refetch_is_idempotent():
    repo = memory_repo()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    item = FundingObservation(symbol="HYPE", source_timestamp=at, received_at=at, funding_rate=.001)
    assert repo.save_funding([item]) == 1
    assert repo.save_funding([item.model_copy(update={"received_at": at + timedelta(minutes=1)})]) == 0
