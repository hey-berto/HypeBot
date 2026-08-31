from __future__ import annotations

from typing import Sequence

from hype_autopilot.features.models import FeatureSet
from hype_autopilot.regimes.models import Regime, TrendRegime, VolatilityRegime


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def classify_regime(features: FeatureSet, trailing_atr_pct: Sequence[float]) -> Regime:
    if None in (features.ema20_1h, features.ema50_1h, features.ema20_1h_lag6):
        trend = TrendRegime.UNKNOWN
    elif features.ema20_1h > features.ema50_1h and features.ema20_1h > features.ema20_1h_lag6:
        trend = TrendRegime.UP
    elif features.ema20_1h < features.ema50_1h and features.ema20_1h < features.ema20_1h_lag6:
        trend = TrendRegime.DOWN
    else:
        trend = TrendRegime.RANGE

    window = list(trailing_atr_pct[-1440:])
    if features.atr_pct_1h is None or len(window) < 720:
        volatility = VolatilityRegime.UNKNOWN
    else:
        low, high = _percentile(window, 0.25), _percentile(window, 0.75)
        if features.atr_pct_1h <= low:
            volatility = VolatilityRegime.LOW
        elif features.atr_pct_1h >= high:
            volatility = VolatilityRegime.HIGH
        else:
            volatility = VolatilityRegime.NORMAL
    return Regime(trend=trend, volatility=volatility, combined=f"{trend.value}_{volatility.value}")

