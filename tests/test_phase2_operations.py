from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest

from hype_autopilot.phase2.operations import (
    phase2_operational_health,
    sqlite_consistent_backup,
    verify_backup,
)
from hype_autopilot.phase2.storage import Phase2Repository


def _database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    repository.initialize()
    db.execute(
        "INSERT INTO research_cycles(cycle_id,scheduled_at,observation_class,started_at,"
        "completed_at,status,details_json) VALUES(?,?,?,?,?,?,?)",
        (
            "cycle-1",
            datetime(2026, 9, 10, 12, 0, tzinfo=UTC).isoformat(),
            "NON_SCORED",
            datetime(2026, 9, 10, 12, 0, tzinfo=UTC).isoformat(),
            datetime(2026, 9, 10, 12, 0, 1, tzinfo=UTC).isoformat(),
            "COMPLETE",
            "{}",
        ),
    )
    db.commit()
    db.close()


def _lease(path, role):
    path.write_text(
        json.dumps({"pid": os.getpid(), "role": role, "epoch_id": "NON_SCORED"}),
        encoding="utf-8",
    )


def test_sanitized_health_contains_no_performance_fields(tmp_path):
    database = tmp_path / "NON_SCORED_phase2.sqlite3"
    worker = tmp_path / "worker.lock"
    supervisor = tmp_path / "supervisor.lock"
    _database(database)
    _lease(worker, "worker")
    _lease(supervisor, "supervisor")
    result = phase2_operational_health(
        database,
        worker_lease=worker,
        supervisor_lease=supervisor,
        now=datetime(2026, 9, 10, 12, 5, tzinfo=UTC),
    )
    assert result["healthy"]
    assert result["database_integrity"] == "ok"
    assert not any(result["duplicates"].values())
    serialized = json.dumps(result).lower()
    assert all(word not in serialized for word in ("pnl", "return", "win_rate"))


def test_consistent_backup_is_hash_verified_and_never_overwritten(tmp_path):
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    _database(source)
    manifest = sqlite_consistent_backup(source, backup)
    assert manifest["integrity"] == "ok"
    assert verify_backup(backup, manifest["sha256"])["foreign_key_violations"] == 0
    with pytest.raises(FileExistsError):
        sqlite_consistent_backup(source, backup)
    with pytest.raises(ValueError):
        verify_backup(backup, "0" * 64)
