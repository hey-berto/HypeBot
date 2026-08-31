from datetime import timedelta

from hype_autopilot.config import load_yaml
from hype_autopilot.data.models import Candle
from hype_autopilot.snapshots.builder import SnapshotBuilder
from tests.helpers import populate_scoreable, memory_repo


def test_on_demand_snapshot_is_scoreable_and_idempotent():
    repo = memory_repo()
    at = populate_scoreable(repo)
    builder = SnapshotBuilder(repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml"))
    first = repo.save_snapshot(builder.build(at, available_at=at + timedelta(seconds=1)))
    second = repo.save_snapshot(builder.build(at, available_at=at + timedelta(seconds=1)))
    assert first.data_quality.scoreable
    assert first.snapshot_hash == second.snapshot_hash
    assert first.market.btc_features["regime"] is not None


def test_late_correction_cannot_rewrite_existing_snapshot():
    repo = memory_repo()
    at = populate_scoreable(repo)
    builder = SnapshotBuilder(repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml"))
    original = repo.save_snapshot(builder.build(at, available_at=at + timedelta(seconds=1)))
    prior = repo.candles("HYPE", "15m", at)[-1]
    correction = prior.model_copy(update={"close": prior.close + 10, "high": prior.high + 10,
                                          "received_at": at + timedelta(minutes=1)})
    repo.save_candles([correction])
    rebuilt = builder.build(at, available_at=at + timedelta(minutes=2))
    assert rebuilt.snapshot_hash != original.snapshot_hash
    try:
        repo.save_snapshot(rebuilt)
    except RuntimeError as exc:
        assert "immutable snapshot differs" in str(exc)
    else:
        raise AssertionError("late correction unexpectedly replaced immutable snapshot")
