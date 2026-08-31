from __future__ import annotations

import math
from statistics import fmean, median, pstdev
from typing import Sequence

from hype_autopilot.data.models import Candle


def ema(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    seed = fmean(values[:period])
    alpha = 2.0 / (period + 1)
    result = seed
    for value in values[period:]:
        result = alpha * value + (1 - alpha) * result
    return result


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    return [ema(values[: index + 1], period) for index in range(len(values))]


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    changes = [b - a for a, b in zip(values[-period - 1:-1], values[-period:])]
    gain = fmean(max(change, 0.0) for change in changes)
    loss = fmean(max(-change, 0.0) for change in changes)
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    window = candles[-period:]
    previous = candles[-period - 1].close
    ranges: list[float] = []
    for candle in window:
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous)))
        previous = candle.close
    return fmean(ranges)


def realised_volatility(values: Sequence[float], periods: int = 20) -> float | None:
    if len(values) < periods + 1:
        return None
    returns = [math.log(b / a) for a, b in zip(values[-periods - 1:-1], values[-periods:]) if a > 0 and b > 0]
    return pstdev(returns) if len(returns) == periods else None


def trailing_zscore(values: Sequence[float], window: int, minimum: int) -> float | None:
    trailing = list(values[-window:])
    if len(trailing) < minimum:
        return None
    std = pstdev(trailing)
    return 0.0 if std == 0 else (trailing[-1] - fmean(trailing)) / std


def rolling_summary(values: Sequence[float], window: int, minimum: int) -> tuple[float | None, float | None]:
    trailing = list(values[-window:])
    if len(trailing) < minimum:
        return None, None
    return fmean(trailing), pstdev(trailing)


def vwap(candles: Sequence[Candle]) -> float | None:
    volume = sum(item.volume for item in candles)
    if not candles or volume <= 0:
        return None
    return sum(((item.high + item.low + item.close) / 3.0) * item.volume for item in candles) / volume


def volume_ratios(candles: Sequence[Candle], window: int = 20) -> tuple[float | None, float | None]:
    if len(candles) < window:
        return None, None
    volumes = [item.volume for item in candles[-window:]]
    mean_value, median_value = fmean(volumes), median(volumes)
    return volumes[-1] / mean_value if mean_value else None, volumes[-1] / median_value if median_value else None


def prior_donchian(candles: Sequence[Candle], lookback: int = 20) -> tuple[float | None, float | None]:
    """Threshold from completed candles before the current 1h defining period."""
    if len(candles) < lookback:
        return None, None
    prior = candles[-lookback:]
    return max(item.high for item in prior), min(item.low for item in prior)
