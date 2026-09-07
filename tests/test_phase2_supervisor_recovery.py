"""Process acceptance witness, intentionally distinct from runtime/LLM tests.

Passing this regression test means the observed result has been reproduced and
recorded correctly. It does NOT mean the acceptance gate passed: the pinned
supervisor currently leaves its detached worker alive after SIGTERM, and a new
supervisor starts an overlapping worker. No production database is opened.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.phase2_supervisor_recovery_probe import (
    DATABASE_NAME,
    PINNED_SUPERVISOR_SHA256,
    assert_isolated_database,
)

SOURCE = Path(
    "/Users/berto/Library/Application Support/HYPE Autopilot/phase2-supervisor.py"
)
PROBE = Path(__file__).with_name("phase2_supervisor_recovery_probe.py")


def read_rows(database: Path) -> list[tuple[int, str, int, int, str, str]]:
    if not database.exists():
        return []
    with sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=10
    ) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE name='probe_worker_events'"
        ).fetchall()
        if not tables:
            return []
        return connection.execute(
            "SELECT * FROM probe_worker_events ORDER BY event_id"
        ).fetchall()


def wait_for(predicate, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for isolated process evidence")


def events(root: Path) -> list[dict]:
    path = root / "original-supervisor-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def record_after(rows, worker_pid: int, event_id: int) -> bool:
    return any(row[2] == worker_pid and row[0] > event_id for row in rows)


@pytest.mark.skipif(
    sys.platform != "darwin" or not SOURCE.exists(),
    reason="Pinned macOS supervisor required",
)
def test_pinned_supervisor_restart_orphan_overlap_witness():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == PINNED_SUPERVISOR_SHA256
    root = Path(
        tempfile.mkdtemp(prefix="hype-non-scored-supervisor-", dir="/tmp")
    ).resolve()
    database = root / DATABASE_NAME
    assert_isolated_database(database)
    supervisors: list[subprocess.Popen] = []
    groups: set[int] = set()
    evidence = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_class": "NON_SCORED_SUPERVISOR_PROCESS_FIXTURE",
        "scope": "Original pinned supervisor main loop; fixture identity and heartbeat worker only."
        " Does not claim actual Phase 2 scheduler, LLM or paper-state acceptance.",
        "source": str(SOURCE),
        "source_sha256": PINNED_SUPERVISOR_SHA256,
        "fixture_database": str(database),
        "production_database_opened": False,
        "production_activation_possible": False,
        "network_or_provider_calls": 0,
    }
    # Intentionally exclude user credentials and all activation environment vars.
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def start_supervisor():
        process = subprocess.Popen(
            [
                sys.executable,
                str(PROBE),
                "--mode",
                "acceptance",
                "--database",
                str(database),
                "--probe-supervisor",
                "--source-supervisor",
                str(SOURCE),
                "--restart-delay-seconds",
                "0.1",
            ],
            env=environment,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        supervisors.append(process)
        return process

    def worker_starts():
        rows = read_rows(database)
        for row in rows:
            if row[4] == "WORKER_STARTED":
                groups.add(row[3])
        return [row for row in rows if row[4] == "WORKER_STARTED"]

    try:
        first = start_supervisor()
        initial = wait_for(lambda: worker_starts())[-1]
        initial_worker, initial_group = initial[2:4]
        assert os.getpgid(initial_worker) == initial_group
        assert initial_group != os.getpgid(first.pid)

        # Crash only the exact worker launched by this test. The original loop
        # should observe its exit and create a successor under the same supervisor.
        os.kill(initial_worker, signal.SIGKILL)
        restarted = wait_for(
            lambda: worker_starts() if len(worker_starts()) >= 2 else None
        )[-1]
        pending_worker, pending_group = restarted[2:4]
        assert pending_worker != initial_worker
        assert first.poll() is None
        assert any(
            event["event"] == "SCHEDULER_PROCESS_EXIT"
            and event["child_pid"] == initial_group
            for event in events(root)
        )
        evidence["worker_crash_restart"] = {
            "passed": True,
            "supervisor_pid": first.pid,
            "old_worker_pid": initial_worker,
            "new_worker_pid": pending_worker,
            "new_process_group": pending_group,
        }

        # A normal process manager stop sends SIGTERM to the supervisor. The
        # absence of a cleanup handler means the detached worker survives it.
        first.terminate()
        assert first.wait(timeout=10) == -signal.SIGTERM
        cut = read_rows(database)[-1][0]
        wait_for(lambda: record_after(read_rows(database), pending_worker, cut))
        evidence["supervisor_sigterm"] = {
            "supervisor_exited": True,
            "worker_survived_and_committed_after_parent_exit": True,
            "worker_pid": pending_worker,
            "last_event_at_parent_exit": cut,
        }

        second = start_supervisor()
        second_start = wait_for(
            lambda: worker_starts() if len(worker_starts()) >= 3 else None
        )[-1]
        second_worker, second_group = second_start[2:4]
        cut = read_rows(database)[-1][0]
        wait_for(lambda: record_after(read_rows(database), pending_worker, cut))
        wait_for(lambda: record_after(read_rows(database), second_worker, cut))
        assert second.poll() is None
        assert pending_group != second_group
        evidence["supervisor_restart"] = {
            "new_supervisor_pid": second.pid,
            "orphan_worker_pid": pending_worker,
            "new_worker_pid": second_worker,
            "both_workers_committed_after_same_event_id": cut,
            "overlapping_authoritative_worker_candidates": 2,
            "passed_single_worker_gate": False,
        }
        evidence["acceptance_disposition"] = "PHASE_2_SIMULATOR_ACCEPTANCE_BLOCKED"
        evidence["blocker"] = (
            "Pinned external supervisor has no SIGTERM child-group cleanup or singleton lock."
            " Its detached worker survives supervisor termination; restart starts another worker."
            " SQL uniqueness alone cannot establish one authoritative execution history."
        )
    finally:
        # No name-based kill, production PID, broad process pattern, or external
        # process is ever targeted. Each group was observed from our fixture DB.
        for supervisor in supervisors:
            if supervisor.poll() is None:
                supervisor.terminate()
            try:
                supervisor.wait(timeout=10)
            except subprocess.TimeoutExpired:
                supervisor.kill()
                supervisor.wait(timeout=10)
        worker_starts()
        for group in groups:
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(0.15)
        residual = []
        for row in worker_starts():
            try:
                os.kill(row[2], 0)
            except ProcessLookupError:
                continue
            residual.append(row[2])
        evidence["cleanup"] = {
            "supervisors_exited": True,
            "residual_fixture_worker_pids": residual,
        }
        if database.exists():
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                evidence["sqlite_integrity"] = connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                evidence["foreign_key_violations"] = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                evidence["fixture_event_count"] = connection.execute(
                    "SELECT COUNT(*) FROM probe_worker_events"
                ).fetchone()[0]
                evidence["duplicate_fixture_event_ids"] = connection.execute(
                    "SELECT event_id,COUNT(*) FROM probe_worker_events GROUP BY event_id HAVING COUNT(*)>1"
                ).fetchall()
            evidence["database_sha256"] = hashlib.sha256(
                database.read_bytes()
            ).hexdigest()
        event_log = root / "original-supervisor-events.jsonl"
        if event_log.exists():
            evidence["supervisor_event_log_sha256"] = hashlib.sha256(
                event_log.read_bytes()
            ).hexdigest()
            evidence["supervisor_events"] = events(root)
        path = root / "supervisor-recovery-evidence.json"
        path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(f"Supervisor recovery evidence: {path}")
        assert not residual, f"Test-created fixture workers require cleanup: {residual}"

    assert evidence["sqlite_integrity"] == "ok"
    assert not evidence["foreign_key_violations"]
    assert not evidence["duplicate_fixture_event_ids"]
    assert evidence["acceptance_disposition"] == "PHASE_2_SIMULATOR_ACCEPTANCE_BLOCKED"


def test_supervisor_probe_rejects_any_production_database():
    with pytest.raises(ValueError, match="NON_SCORED"):
        assert_isolated_database(
            Path("/tmp/hype-non-scored-supervisor-x/phase2_epoch_002.sqlite3")
        )
    with pytest.raises(ValueError, match="uniquely created"):
        assert_isolated_database(Path("/tmp/production") / DATABASE_NAME)
    with pytest.raises(ValueError, match="system temporary"):
        assert_isolated_database(
            Path("/var/hype-non-scored-supervisor-x") / DATABASE_NAME
        )
