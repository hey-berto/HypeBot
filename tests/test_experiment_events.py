from __future__ import annotations

import sqlite3

import pytest

from hype_autopilot.config import load_yaml
from hype_autopilot.experiments.registry import record_experiment_event
from tests.helpers import memory_repo


def test_experiment_event_records_code_and_config_immutably():
    repo = memory_repo()
    epoch_config = load_yaml("config/epoch_001.yaml")
    event_id = record_experiment_event(
        repo.db, epoch_config, "phase1-soak", "SOAK_VERSION_FROZEN",
        details={"purpose": "test"},
    )
    row = repo.db.execute(
        "SELECT * FROM experiment_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    assert row["git_commit_hash"]
    assert len(row["git_commit_hash"]) == 40
    assert len(row["config_hash"]) == 64
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        repo.db.execute(
            "UPDATE experiment_events SET event_type = 'CHANGED' WHERE event_id = ?", (event_id,)
        )
