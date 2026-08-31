from datetime import UTC, datetime

from hype_autopilot.features.models import FeatureSet
from hype_autopilot.regimes.models import Regime, TrendRegime, VolatilityRegime
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from hype_autopilot.snapshots.models import DataQuality, DecisionSnapshot, MarketSnapshot
from hype_autopilot.strategies.base import Decision
from hype_autopilot.strategies.quant_mean_reversion_v1 import QuantMeanReversionV1
from hype_autopilot.strategies.quant_trend_v1 import QuantTrendV1


def snapshot(features: FeatureSet):
    at = datetime(2026, 1, 1, tzinfo=UTC)
    return freeze_snapshot(DecisionSnapshot(
        snapshot_id="strategy-test", snapshot_timestamp=at, created_at=at, epoch_id="epoch_001",
        market=MarketSnapshot(hype_features=features),
        regime=Regime(trend=TrendRegime.UP, volatility=VolatilityRegime.NORMAL, combined="UP_NORMAL"),
        data_quality=DataQuality(required_sources_present=True, scoreable=True),
        source_cutoffs={"hype": at},
    ))


def test_trend_breakout_is_deterministic():
    snap = snapshot(FeatureSet(last_15m_close=110, donchian_high_1h=109,
                               donchian_low_1h=90, atr14_1h=2))
    decision = QuantTrendV1().evaluate(snap)
    assert decision.decision == Decision.LONG
    assert decision.stop_reference == 104


def test_mean_reversion_rejects_invalid_target():
    snap = snapshot(FeatureSet(last_15m_close=100, funding_zscore=2.2, rsi14_1h=75,
                               atr14_1h=3, ema20_1h=101))
    decision = QuantMeanReversionV1().evaluate(snap)
    assert decision.decision == Decision.NO_TRADE
    assert decision.reason_codes == ("INVALID_TARGET_AT_SIGNAL",)

