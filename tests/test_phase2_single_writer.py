from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROBE = Path(__file__).with_name("phase2_single_writer_probe.py")
ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out waiting for process evidence")


def events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def worker_starts(root: Path) -> list[dict[str, object]]:
    return [
        row
        for row in events(root / "supervisor-events.jsonl")
        if row["event"] == "WORKER_STARTED"
    ]


def worker_rows(root: Path) -> list[tuple[int, int, int, str, str]]:
    database = root / "NON_SCORED_SUPERVISOR.sqlite3"
    if not database.exists():
        return []
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return connection.execute("SELECT * FROM worker_events ORDER BY id").fetchall()


def test_single_writer_lock_verified_group_death_and_supervisor_restart(tmp_path):
    root = tmp_path / "phase2-single-writer"
    root.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    supervisors: list[subprocess.Popen[bytes]] = []

    def start_supervisor() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, str(PROBE), "supervisor", str(root)],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        supervisors.append(process)
        return process

    try:
        first = start_supervisor()
        initial = wait_for(lambda: worker_starts(root))[-1]
        initial_pid = int(initial["details"]["worker_pid"])
        wait_for(lambda: len(worker_rows(root)) > 1)

        rogue = subprocess.run(
            [sys.executable, str(PROBE), "worker", str(root)],
            cwd=root,
            env=environment,
            timeout=10,
            check=False,
        )
        assert rogue.returncode != 0
        assert (
            events(root / "worker-events.jsonl")[-1]["event"] == "WRITER_LEASE_REJECTED"
        )
        assert {row[1] for row in worker_rows(root)} == {initial_pid}

        os.kill(initial_pid, signal.SIGKILL)
        restarted = wait_for(
            lambda: worker_starts(root) if len(worker_starts(root)) >= 2 else None
        )[-1]
        replacement_pid = int(restarted["details"]["worker_pid"])
        assert replacement_pid != initial_pid
        proof = [
            row
            for row in events(root / "supervisor-events.jsonl")
            if row["event"] == "OLD_WORKER_GROUP_CONFIRMED_DEAD"
        ]
        assert proof and proof[-1]["details"]["final_members"] == []

        first.terminate()
        assert first.wait(timeout=10) == 0
        cut = len(worker_rows(root))
        time.sleep(0.2)
        assert len(worker_rows(root)) == cut

        second = start_supervisor()
        third = wait_for(
            lambda: worker_starts(root) if len(worker_starts(root)) >= 3 else None
        )[-1]
        third_pid = int(third["details"]["worker_pid"])
        assert third_pid not in {initial_pid, replacement_pid}
        assert second.poll() is None
        live = []
        for pid in {initial_pid, replacement_pid, third_pid}:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            live.append(pid)
        assert live == [third_pid]
    finally:
        for supervisor in supervisors:
            if supervisor.poll() is None:
                supervisor.terminate()
            try:
                supervisor.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(supervisor.pid, signal.SIGKILL)
                supervisor.wait(timeout=10)

    database = root / "NON_SCORED_SUPERVISOR.sqlite3"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_new_supervisor_discovers_and_terminates_verified_hard_crash_orphan(tmp_path):
    root = tmp_path / "phase2-cross-instance-orphan"
    root.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    supervisors: list[subprocess.Popen[bytes]] = []

    def start_supervisor() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, str(PROBE), "supervisor", str(root)],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        supervisors.append(process)
        return process

    try:
        first = start_supervisor()
        initial = wait_for(lambda: worker_starts(root))[-1]
        orphan_pid = int(initial["details"]["worker_pid"])
        wait_for(lambda: len(worker_rows(root)) > 1)
        cut = len(worker_rows(root))

        first.kill()
        assert first.wait(timeout=10) == -signal.SIGKILL
        wait_for(lambda: len(worker_rows(root)) > cut)
        os.kill(orphan_pid, 0)

        second = start_supervisor()
        replacement = wait_for(
            lambda: worker_starts(root) if len(worker_starts(root)) >= 2 else None
        )[-1]
        replacement_pid = int(replacement["details"]["worker_pid"])
        assert replacement_pid != orphan_pid
        wait_for(lambda: any(row[1] == replacement_pid for row in worker_rows(root)))
        with pytest.raises(ProcessLookupError):
            os.kill(orphan_pid, 0)
        orphan_events = [
            row
            for row in events(root / "supervisor-events.jsonl")
            if row["event"] == "ORPHANED_WORKER_GROUP_TERMINATED"
        ]
        assert orphan_events
        assert orphan_events[-1]["details"]["prior_owner"]["pid"] == orphan_pid
        assert orphan_events[-1]["details"]["final_members"] == []
        assert second.poll() is None
    finally:
        for supervisor in supervisors:
            if supervisor.poll() is None:
                supervisor.terminate()
            try:
                supervisor.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(supervisor.pid, signal.SIGKILL)
                supervisor.wait(timeout=10)

    database = root / "NON_SCORED_SUPERVISOR.sqlite3"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        worker_pids = {
            row[0]
            for row in connection.execute("SELECT DISTINCT pid FROM worker_events")
        }
        assert worker_pids == {orphan_pid, replacement_pid}
