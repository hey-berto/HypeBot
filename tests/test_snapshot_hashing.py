from datetime import UTC, datetime

from hype_autopilot.features.models import FeatureSet
from hype_autopilot.hashing import canonical_json
from hype_autopilot.regimes.models import Regime, TrendRegime, VolatilityRegime
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from hype_autopilot.snapshots.models import DataQuality, DecisionSnapshot, MarketSnapshot


def make_snapshot(close: float = 12.34567890125) -> DecisionSnapshot:
    at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return DecisionSnapshot(
        snapshot_id="fixed", snapshot_timestamp=at, created_at=at, epoch_id="epoch_001",
        market=MarketSnapshot(hype_features=FeatureSet(last_15m_close=close)),
        regime=Regime(trend=TrendRegime.RANGE, volatility=VolatilityRegime.NORMAL,
                      combined="RANGE_NORMAL"),
        data_quality=DataQuality(required_sources_present=True, scoreable=True),
        source_cutoffs={"hype_15m": at},
    )


def test_hash_is_deterministic_and_sensitive():
    first = freeze_snapshot(make_snapshot())
    second = freeze_snapshot(make_snapshot())
    changed = freeze_snapshot(make_snapshot(12.34567890135))
    assert first.snapshot_hash == second.snapshot_hash
    assert first.snapshot_hash != changed.snapshot_hash


def test_round_half_even_and_negative_zero():
    assert canonical_json({"a": 1.23456789005, "b": -0.0}) == '{"a":1.2345678900,"b":0.0000000000}'

