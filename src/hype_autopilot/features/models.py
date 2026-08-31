from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FeatureSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    last_15m_close: float
    return_15m: float | None = None
    return_1h: float | None = None
    return_4h: float | None = None
    return_24h: float | None = None
    ema20_1h: float | None = None
    ema50_1h: float | None = None
    ema200_1h: float | None = None
    ema20_1h_lag6: float | None = None
    distance_ema20_1h: float | None = None
    distance_ema50_1h: float | None = None
    distance_ema200_1h: float | None = None
    vwap_24h: float | None = None
    rsi14_1h: float | None = None
    atr14_1h: float | None = None
    atr_pct_1h: float | None = None
    realised_vol_1h: float | None = None
    donchian_high_1h: float | None = None
    donchian_low_1h: float | None = None
    distance_donchian_high_1h: float | None = None
    distance_donchian_low_1h: float | None = None
    swing_high_20_1h: float | None = None
    swing_low_20_1h: float | None = None
    volume_vs_mean_20_1h: float | None = None
    volume_vs_median_20_1h: float | None = None
    funding_rate: float | None = None
    funding_mean: float | None = None
    funding_std: float | None = None
    funding_zscore: float | None = None
    open_interest: float | None = None
    oi_change_15m: float | None = None
    oi_change_1h: float | None = None
    oi_change_4h: float | None = None
    oi_change_24h: float | None = None
    price_oi_state_1h: str | None = None
    mark_oracle_basis_bps: float | None = None
    spread_bps: float | None = None
    bbo_imbalance: float | None = None
