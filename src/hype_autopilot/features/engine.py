from __future__ import annotations

from datetime import datetime
from typing import Sequence

from hype_autopilot.clock import ensure_utc
from hype_autopilot.data.models import AssetContext, BboObservation, Candle
from hype_autopilot.features.indicators import (
    atr, ema, ema_series, prior_donchian, realised_volatility, rolling_summary,
    rsi, trailing_zscore, volume_ratios, vwap,
)
from hype_autopilot.features.models import FeatureSet


def completed(candles: Sequence[Candle], cutoff: datetime) -> list[Candle]:
    cutoff = ensure_utc(cutoff)
    return sorted((c for c in candles if c.close_time <= cutoff), key=lambda c: c.close_time)


def _return(closes: list[float], periods: int) -> float | None:
    if len(closes) <= periods or closes[-periods - 1] == 0:
        return None
    return closes[-1] / closes[-periods - 1] - 1.0


def build_features(
    snapshot_at: datetime,
    candles_15m: Sequence[Candle],
    candles_1h: Sequence[Candle],
    funding_history: Sequence[tuple[datetime, float]],
    context: AssetContext | None = None,
    bbo: BboObservation | None = None,
    context_history: Sequence[AssetContext] = (),
) -> FeatureSet:
    snapshot_at = ensure_utc(snapshot_at)
    bars15 = completed(candles_15m, snapshot_at)
    bars1h = completed(candles_1h, snapshot_at)
    if not bars15:
        raise ValueError("a completed 15m candle is required")
    closes15, closes1h = [bar.close for bar in bars15], [bar.close for bar in bars1h]
    current = closes15[-1]
    ema20_values = ema_series(closes1h, 20)
    ema20, ema50, ema200 = ema(closes1h, 20), ema(closes1h, 50), ema(closes1h, 200)
    atr14 = atr(bars1h, 14)
    donchian_high, donchian_low = prior_donchian(bars1h, 20)
    funding = [value for at, value in sorted(funding_history) if ensure_utc(at) <= snapshot_at]
    funding_mean, funding_std = rolling_summary(funding, 168, 72)
    volume_mean, volume_median = volume_ratios(bars1h, 20)
    recent = bars1h[-20:]
    contexts = sorted(
        (item for item in context_history if item.source_timestamp <= snapshot_at),
        key=lambda item: item.source_timestamp,
    )

    def oi_change(minutes: int) -> float | None:
        if not context or context.open_interest is None:
            return None
        threshold = snapshot_at.timestamp() - minutes * 60
        prior = [item for item in contexts if item.source_timestamp.timestamp() <= threshold and item.open_interest]
        if not prior or not prior[-1].open_interest:
            return None
        return context.open_interest / prior[-1].open_interest - 1.0

    spread_bps = imbalance = None
    if bbo and bbo.bid_price and bbo.ask_price:
        midpoint = (bbo.bid_price + bbo.ask_price) / 2
        spread_bps = (bbo.ask_price - bbo.bid_price) / midpoint * 10_000
    if bbo and bbo.bid_size is not None and bbo.ask_size is not None and bbo.bid_size + bbo.ask_size:
        imbalance = (bbo.bid_size - bbo.ask_size) / (bbo.bid_size + bbo.ask_size)
    basis = None
    if context and context.mark_price is not None and context.oracle_price:
        basis = (context.mark_price / context.oracle_price - 1.0) * 10_000
    return_1h, oi_1h = _return(closes15, 4), oi_change(60)
    price_oi_state = None
    if return_1h is not None and oi_1h is not None:
        price_oi_state = f"PRICE_{'UP' if return_1h >= 0 else 'DOWN'}_OI_{'UP' if oi_1h >= 0 else 'DOWN'}"

    return FeatureSet(
        last_15m_close=current,
        return_15m=_return(closes15, 1), return_1h=return_1h,
        return_4h=_return(closes15, 16), return_24h=_return(closes15, 96),
        ema20_1h=ema20, ema50_1h=ema50, ema200_1h=ema200,
        ema20_1h_lag6=ema20_values[-7] if len(ema20_values) >= 7 else None,
        distance_ema20_1h=(current / ema20 - 1.0) if ema20 else None,
        distance_ema50_1h=(current / ema50 - 1.0) if ema50 else None,
        distance_ema200_1h=(current / ema200 - 1.0) if ema200 else None,
        vwap_24h=vwap(bars15[-96:]),
        rsi14_1h=rsi(closes1h, 14), atr14_1h=atr14,
        atr_pct_1h=(atr14 / closes1h[-1]) if atr14 is not None and closes1h else None,
        realised_vol_1h=realised_volatility(closes1h, 20),
        donchian_high_1h=donchian_high, donchian_low_1h=donchian_low,
        distance_donchian_high_1h=(current / donchian_high - 1.0) if donchian_high else None,
        distance_donchian_low_1h=(current / donchian_low - 1.0) if donchian_low else None,
        swing_high_20_1h=max((bar.high for bar in recent), default=None),
        swing_low_20_1h=min((bar.low for bar in recent), default=None),
        volume_vs_mean_20_1h=volume_mean, volume_vs_median_20_1h=volume_median,
        funding_rate=(context.funding_rate if context else (funding[-1] if funding else None)),
        funding_mean=funding_mean, funding_std=funding_std,
        funding_zscore=trailing_zscore(funding, 168, 72),
        open_interest=context.open_interest if context else None,
        oi_change_15m=oi_change(15), oi_change_1h=oi_1h,
        oi_change_4h=oi_change(240), oi_change_24h=oi_change(1440),
        price_oi_state_1h=price_oi_state, mark_oracle_basis_bps=basis,
        spread_bps=spread_bps, bbo_imbalance=imbalance,
    )


def atr_percent_history(candles_1h: Sequence[Candle], cutoff: datetime) -> list[float]:
    bars = completed(candles_1h, cutoff)
    values: list[float] = []
    for index in range(14, len(bars)):
        value = atr(bars[: index + 1], 14)
        if value is not None and bars[index].close:
            values.append(value / bars[index].close)
    return values
