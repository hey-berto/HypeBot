"""Isolated process probe for the pinned external Phase 2 supervisor.

This is NOT a scheduler, provider, scored run, or replacement supervisor.  It
retains the original supervision loop while substituting only its fixture
identity dependency and executable/log/worktree paths.  The fake worker's
append-only heartbeat journal makes detached-writer lifetime observable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PINNED_SUPERVISOR_SHA256 = (
    "598d329e361e1fc1c7248d6c136140ed117acde1f9fad4178c9e26bec2dfccb0"
)
DATABASE_NAME = "NON_SCORED_SUPERVISOR_ACCEPTANCE.sqlite3"


def assert_isolated_database(database: Path) -> Path:
    database = database.resolve()
    if database.name != DATABASE_NAME:
        raise ValueError("Only the explicitly NON_SCORED fixture database is permitted")
    if not database.parent.name.startswith("hype-non-scored-supervisor-"):
        raise ValueError(
            "Fixture database must be in the uniquely created test directory"
        )
    if database.parent.parent != Path("/tmp").resolve():
        raise ValueError(
            "Fixture database must be directly under the system temporary root"
        )
    return database


def fixture_worker(database: Path) -> None:
    database = assert_isolated_database(database)
    connection = sqlite3.connect(database, timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS probe_worker_events ("
        "event_id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, "
        "worker_pid INTEGER NOT NULL, process_group INTEGER NOT NULL, "
        "event TEXT NOT NULL, evidence_class TEXT NOT NULL "
        "CHECK(evidence_class='NON_SCORED_SUPERVISOR_PROCESS_FIXTURE'))"
    )
    for operation in ("UPDATE", "DELETE"):
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS probe_no_{operation.lower()} "
            f"BEFORE {operation} ON probe_worker_events "
            "BEGIN SELECT RAISE(ABORT,'append-only process evidence'); END"
        )
    connection.commit()
    event = "WORKER_STARTED"
    while True:
        with connection:
            connection.execute(
                "INSERT INTO probe_worker_events "
                "(timestamp,worker_pid,process_group,event,evidence_class) "
                "VALUES(?,?,?,?,?)",
                (
                    datetime.now(UTC).isoformat(),
                    os.getpid(),
                    os.getpgrp(),
                    event,
                    "NON_SCORED_SUPERVISOR_PROCESS_FIXTURE",
                ),
            )
        event = "HEARTBEAT"
        time.sleep(0.05)


def isolated_supervisor(source: Path, database: Path, restart_delay: float) -> None:
    database = assert_isolated_database(database)
    source = source.resolve(strict=True)
    if hashlib.sha256(source.read_bytes()).hexdigest() != PINNED_SUPERVISOR_SHA256:
        raise ValueError(
            "External supervisor source is not the pinned acceptance baseline"
        )
    spec = importlib.util.spec_from_file_location("pinned_phase2_supervisor", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Overrides are limited to fixture paths and the frozen-identity dependency.
    # In particular, main(), Popen(), signals, polling, and retry logic are intact.
    root = database.parent
    module.WORKTREE = root
    module.PYTHON = Path(sys.executable)
    module.WORKER = Path(__file__).resolve()
    module.EVENT_LOG = root / "original-supervisor-events.jsonl"
    module.STDOUT_LOG = root / "fixture-worker.stdout.log"
    module.STDERR_LOG = root / "fixture-worker.stderr.log"
    module.EXPECTED_DATABASE = root / "FORBIDDEN-production-database.sqlite3"

    def fixture_identity(mode: str) -> dict[str, object]:
        if mode != "acceptance":
            raise ValueError("Production invocation is forbidden by the isolated probe")
        assert_isolated_database(database)
        return {
            "mode": "acceptance",
            "evidence_class": "NON_SCORED_SUPERVISOR_PROCESS_FIXTURE",
            "identity_dependency": "ISOLATED_FIXTURE_NOT_RESEARCH_RUNTIME",
            "original_supervisor_sha256": PINNED_SUPERVISOR_SHA256,
            "fixture_database": str(database),
            "evidence_collection_enabled": False,
            "activation_authorized": False,
        }

    module.validate_identity = fixture_identity
    sys.argv = [
        str(source),
        "--mode",
        "acceptance",
        "--acceptance-database",
        str(database),
        "--restart-delay-seconds",
        str(restart_delay),
    ]
    module.main()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("acceptance",), required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--probe-supervisor", action="store_true")
    parser.add_argument("--source-supervisor", type=Path)
    parser.add_argument("--restart-delay-seconds", type=float, default=0.1)
    args = parser.parse_args()
    if args.probe_supervisor:
        if args.source_supervisor is None:
            parser.error("--source-supervisor is required")
        isolated_supervisor(
            args.source_supervisor, args.database, args.restart_delay_seconds
        )
    else:
        fixture_worker(args.database)


if __name__ == "__main__":
    main()
