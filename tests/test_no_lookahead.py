from datetime import timedelta

from hype_autopilot.features.engine import completed
from hype_autopilot.features.indicators import prior_donchian


def test_future_candle_is_excluded(candles):
    cutoff = candles[30].close_time
    assert completed(candles, cutoff) == candles[:31]


def test_donchian_uses_only_supplied_prior_bars(candles):
    threshold = prior_donchian(candles[:20], 20)
    future = candles[20].model_copy(update={"high": 10000.0, "low": 1.0})
    assert prior_donchian(candles[:20], 20) == threshold
    assert prior_donchian(candles[:20] + [future], 20) != threshold

