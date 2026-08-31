from datetime import UTC, datetime

import pytest

from hype_autopilot.strategies.base import Decision, StrategyDecision
from tests.helpers import memory_repo, populate_scoreable
from hype_autopilot.config import load_yaml
from hype_autopilot.snapshots.builder import SnapshotBuilder


def test_duplicate_strategy_scoring_is_idempotent_but_conflicts_are_rejected():
    repo = memory_repo()
    at = populate_scoreable(repo)
    snapshot = repo.save_snapshot(SnapshotBuilder(
        repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml")
    ).build(at, available_at=at))
    decision = StrategyDecision(decision_id="same", snapshot_hash=snapshot.snapshot_hash,
        strategy_id="S", strategy_version="V", decision=Decision.NO_TRADE, created_at=at,
        trade_ttl_minutes=1, reason_codes=("A",))
    repo.save_strategy_decision(decision)
    assert repo.save_strategy_decision(decision) == decision
    changed = decision.model_copy(update={"decision_id": "different", "reason_codes": ("B",)})
    with pytest.raises(RuntimeError):
        repo.save_strategy_decision(changed)
