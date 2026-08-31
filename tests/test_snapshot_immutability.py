import sqlite3
from datetime import UTC, datetime

import pytest

from hype_autopilot.data.repository import Repository
from hype_autopilot.features.models import FeatureSet
from hype_autopilot.regimes.models import Regime, TrendRegime, VolatilityRegime
from hype_autopilot.snapshots.models import DataQuality, DecisionSnapshot, MarketSnapshot


def test_persisted_snapshot_cannot_be_updated():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    repo = Repository(db)
    repo.initialize()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    snap = DecisionSnapshot(snapshot_id="immutable", snapshot_timestamp=at, created_at=at,
        epoch_id="epoch_001", market=MarketSnapshot(hype_features=FeatureSet(last_15m_close=10)),
        regime=Regime(trend=TrendRegime.RANGE, volatility=VolatilityRegime.UNKNOWN,
                      combined="RANGE_UNKNOWN"),
        data_quality=DataQuality(required_sources_present=True, scoreable=True),
        source_cutoffs={"hype": at})
    frozen = repo.save_snapshot(snap)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE decision_snapshots SET scoreable = 0 WHERE snapshot_hash = ?", (frozen.snapshot_hash,))

