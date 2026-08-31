from datetime import UTC, datetime, timedelta

from hype_autopilot.features.models import FeatureSet
from hype_autopilot.hashing import canonical_json
from hype_autopilot.regimes.classifier import classify_regime
from hype_autopilot.regimes.models import Regime, TrendRegime, VolatilityRegime
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from hype_autopilot.snapshots.models import DataQuality, DecisionSnapshot, MarketSnapshot
from hype_autopilot.strategies.quant_trend_v1 import QuantTrendV1


def snapshot(close=100.0, regime="RANGE_NORMAL"):
    at = datetime(2026, 1, 1, tzinfo=UTC)
    return freeze_snapshot(DecisionSnapshot(
        snapshot_id="section19", snapshot_timestamp=at, created_at=at, epoch_id="epoch_001",
        market=MarketSnapshot(hype_features=FeatureSet(
            last_15m_close=close, donchian_high_1h=99, donchian_low_1h=80, atr14_1h=2)),
        regime=Regime(trend=TrendRegime.RANGE, volatility=VolatilityRegime.NORMAL, combined=regime),
        data_quality=DataQuality(required_sources_present=True, scoreable=True),
        source_cutoffs={"hype": at},
    ))


def test_09_strategy_references_exact_snapshot_hash():
    frozen = snapshot()
    assert QuantTrendV1().evaluate(frozen).snapshot_hash == frozen.snapshot_hash


def test_08_each_economically_relevant_change_changes_hash():
    assert snapshot(100).snapshot_hash != snapshot(101).snapshot_hash
    assert snapshot(100, "RANGE_NORMAL").snapshot_hash != snapshot(100, "UP_NORMAL").snapshot_hash


def test_13_regime_uses_only_latest_1440_and_requires_720_observations():
    features = FeatureSet(last_15m_close=100, ema20_1h=101, ema50_1h=100,
                          ema20_1h_lag6=99, atr_pct_1h=.03)
    assert classify_regime(features, [.01] * 719).volatility == "UNKNOWN"
    baseline = classify_regime(features, [.01] * 1000 + [.02] * 440)
    with_ancient_outliers = classify_regime(features, [999.0] * 500 + [.01] * 1000 + [.02] * 440)
    assert baseline == with_ancient_outliers


def test_14_round_half_even_is_exact_and_repeatable():
    expected = '{"down":1.2345678900,"negative_zero":0.0000000000,"up":1.2345678902}'
    value = {"down": 1.23456789005, "negative_zero": -0.0, "up": 1.23456789015}
    assert canonical_json(value) == expected
    assert canonical_json(value) == expected
