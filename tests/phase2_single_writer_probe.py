from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from hype_autopilot.phase2.supervision import (
    ExclusiveProcessLease,
    LeaseAlreadyOwned,
    SingleWriterSupervisor,
    SupervisorEvent,
    lease_exit_code,
)


def append_json(path: Path, value: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def worker(root: Path) -> int:
    lease = ExclusiveProcessLease(
        root / "writer.lock", role="phase2-writer", epoch_id="NON_SCORED"
    )
    try:
        lease.acquire()
    except LeaseAlreadyOwned as exc:
        append_json(
            root / "worker-events.jsonl",
            {
                "event": "WRITER_LEASE_REJECTED",
                "pid": os.getpid(),
                "at": datetime.now(UTC).isoformat(),
                "error": str(exc),
            },
        )
        return lease_exit_code(exc)
    connection = sqlite3.connect(root / "NON_SCORED_SUPERVISOR.sqlite3")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS worker_events("
        "id INTEGER PRIMARY KEY,pid INTEGER,process_group INTEGER,event TEXT,at TEXT)"
    )
    connection.commit()
    event = "WORKER_STARTED"
    try:
        while True:
            with connection:
                connection.execute(
                    "INSERT INTO worker_events(pid,process_group,event,at) VALUES(?,?,?,?)",
                    (os.getpid(), os.getpgrp(), event, datetime.now(UTC).isoformat()),
                )
            event = "HEARTBEAT"
            time.sleep(0.05)
    finally:
        connection.close()
        lease.release()


def supervisor(root: Path) -> None:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": os.environ["PYTHONPATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def sink(event: SupervisorEvent) -> None:
        append_json(root / "supervisor-events.jsonl", asdict(event))

    instance = SingleWriterSupervisor(
        command=(sys.executable, str(Path(__file__).resolve()), "worker", str(root)),
        cwd=root,
        supervisor_lease=ExclusiveProcessLease(
            root / "supervisor.lock", role="phase2-supervisor", epoch_id="NON_SCORED"
        ),
        event_sink=sink,
        environment=environment,
        restart_delay_seconds=0.05,
    )
    instance.run()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("worker", "supervisor"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    if args.mode == "worker":
        raise SystemExit(worker(args.root.resolve()))
    supervisor(args.root.resolve())


if __name__ == "__main__":
    main()
