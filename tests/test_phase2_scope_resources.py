from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hype_autopilot.config import load_yaml
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.phase2.config import load_phase2_config
from hype_autopilot.phase2.resources import Phase2ResourceGuard, ResourceBudgetExceeded
from hype_autopilot.phase2.snapshot import (
    assert_snapshot_parity,
    reconstruct_phase2_snapshot,
)
from hype_autopilot.phase2.storage import Phase2Repository
from tests.helpers import memory_repo, populate_scoreable

ROOT = Path(__file__).resolve().parents[1]


def test_phase2_source_contains_no_epoch1_database_or_trading_capability():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/hype_autopilot/phase2").glob("*.py"))
    ).lower()
    forbidden = (
        "epoch_001.sqlite3",
        "hype-phase1-acceptance.sqlite3",
        "private_key",
        "wallet",
        "mainnet",
        "place_order",
        "market_open",
    )
    assert all(token not in text for token in forbidden)


def test_resource_guard_enforces_disk_api_and_single_call_limits(tmp_path: Path):
    config, _ = load_phase2_config(ROOT / "config/phase2/phase2_epoch_001.yaml")
    database = tmp_path / "data/phase2/test.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"x")
    guard = Phase2ResourceGuard(config.resource_isolation, database)
    guard.assert_disk_budget()
    guard.assert_api_budget(0)
    with pytest.raises(ResourceBudgetExceeded):
        guard.assert_api_budget(config.resource_isolation.api_budget_usd_per_day)
    with guard.llm_slot(), pytest.raises(ResourceBudgetExceeded), guard.llm_slot():
        pass


def test_build_database_has_zero_manifests_or_scored_rows():
    config, _ = load_phase2_config(ROOT / "config/phase2/phase2_epoch_001.yaml")
    config.assert_build_only()
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    repository.initialize()
    assert db.execute("SELECT COUNT(*) FROM phase2_manifests").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM llm_decisions").fetchone()[0] == 0
    assert (
        db.execute(
            "SELECT COUNT(*) FROM decision_snapshots WHERE observation_class='SCORED_PROSPECTIVE'"
        ).fetchone()[0]
        == 0
    )


def test_two_independent_raw_repositories_reconstruct_identical_snapshot():
    left_repo, right_repo = memory_repo(), memory_repo()
    at = populate_scoreable(left_repo)
    assert populate_scoreable(right_repo, at) == at
    base = load_yaml(ROOT / "config/base.yaml")
    epoch = load_yaml(ROOT / "config/epoch_001.yaml")
    left = reconstruct_phase2_snapshot(
        left_repo,
        base_config=base,
        epoch_config=epoch,
        snapshot_at=at,
        available_at=at,
        observation_class=ObservationClass.SOAK,
    )
    right = reconstruct_phase2_snapshot(
        right_repo,
        base_config=base,
        epoch_config=epoch,
        snapshot_at=at,
        available_at=at,
        observation_class=ObservationClass.SOAK,
    )
    assert_snapshot_parity(left, right)
