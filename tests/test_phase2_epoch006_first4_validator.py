"""Regression coverage for the standalone read-only epoch006 validator."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path


SOURCE = (
    Path(__file__).parents[1]
    / "deploy/operations/phase2_epoch006_first4_validator.py"
)


def validator_module():
    spec = importlib.util.spec_from_file_location("epoch006_first4_validator", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_timestamp_spelling_is_not_a_boundary_identity() -> None:
    validator = validator_module()
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE research_cycles (cycle_id TEXT, scheduled_at TEXT, observation_class TEXT)"
    )
    db.execute(
        "INSERT INTO research_cycles VALUES (?,?,?)",
        ("first", "2026-09-24T00:15:00+00:00", "SCORED_PROSPECTIVE"),
    )
    rows = validator.cycle_rows_at_boundary(db, "2026-09-24T00:15:00Z")
    assert [row["cycle_id"] for row in rows] == ["first"]
    assert validator.instant("2026-09-24T00:15:00Z") == validator.instant(
        "2026-09-24T00:15:00+00:00"
    )


def test_event_log_uses_instants_and_actual_worker_started_name(tmp_path: Path) -> None:
    validator = validator_module()
    event_log = tmp_path / "supervisor.jsonl"
    event_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-24T00:11:07.100000+00:00",
                "event": "WORKER_STARTED",
            }
        )
        + "\n"
    )
    events = validator.event_log(str(event_log), "2026-09-24T00:11:07Z")
    assert [event["event"] for event in events] == ["WORKER_STARTED"]


def test_authoritative_writer_pid_excludes_supervisor_argv_match() -> None:
    worker_pid = 209458
    processes = [
        " 209333 1 supervisor --worker-executable phase2_epoch006_runtime_worker.py",
        " 209458 209333 python phase2_epoch006_runtime_worker.py",
    ]
    actual = [
        line
        for line in processes
        if str(worker_pid) == line.split(maxsplit=1)[0]
        and "phase2_epoch006_runtime_worker.py" in line
    ]
    assert actual == [processes[1]]
