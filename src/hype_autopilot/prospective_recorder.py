"""Separate-host, append-only raw market-data recorder; no trading capability."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hype_autopilot.hashing import canonical_json

CLASSIFICATION = "FUTURE_RESEARCH_DATA — NOT EPOCH006 EVIDENCE"
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS recorder_sessions (
 session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, started_monotonic_ns INTEGER NOT NULL,
 ended_at TEXT, reconnect_of TEXT, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recorder_clock_health (
 observation_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, observed_at TEXT NOT NULL,
 monotonic_ns INTEGER NOT NULL, status TEXT NOT NULL, source TEXT, offset_seconds REAL, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recorder_raw_events (
 event_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES recorder_sessions(session_id),
 stream TEXT NOT NULL, source_timestamp TEXT, received_at TEXT NOT NULL,
 received_monotonic_ns INTEGER NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
 source_key TEXT, out_of_order INTEGER NOT NULL DEFAULT 0,
 UNIQUE(session_id, stream, payload_sha256)
);
CREATE TABLE IF NOT EXISTS recorder_stream_state (
 stream TEXT PRIMARY KEY, last_source_timestamp TEXT, last_received_at TEXT,
 last_source_key TEXT, session_id TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recorder_gap_events (
 gap_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, stream TEXT NOT NULL,
 started_at TEXT NOT NULL, ended_at TEXT, reason TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recorder_connection_events (
 event_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, event TEXT NOT NULL,
 occurred_at TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recorder_raw_stream_time ON recorder_raw_events(stream, source_timestamp);
CREATE INDEX IF NOT EXISTS recorder_raw_received ON recorder_raw_events(received_at);
CREATE TRIGGER IF NOT EXISTS recorder_raw_immutable_update BEFORE UPDATE ON recorder_raw_events
BEGIN SELECT RAISE(ABORT, 'raw recorder evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS recorder_raw_immutable_delete BEFORE DELETE ON recorder_raw_events
BEGIN SELECT RAISE(ABORT, 'raw recorder evidence is append-only'); END;
"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class ProspectiveRecorder:
    """Storage core for WebSocket/poll adapters; deliberately has no order API."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.db = sqlite3.connect(self.database)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def start_session(self, *, reconnect_of: str | None = None, metadata: dict | None = None) -> str:
        session_id = str(uuid4())
        now = _utc_now()
        self.db.execute(
            "INSERT INTO recorder_sessions VALUES (?,?,?,?,?,?)",
            (session_id, _iso(now), time.monotonic_ns(), None, reconnect_of, canonical_json({"classification": CLASSIFICATION, "git_sha": os.environ.get("HYPE_RECORDER_GIT_SHA", "UNSET"), "schema_version": "RECORDER_SCHEMA_V2", "config_identity": os.environ.get("HYPE_RECORDER_CONFIG_ID", "UNSET"), **(metadata or {})})),
        )
        self.connection_event(session_id, "SESSION_STARTED", {"subscriptions": ["trades", "l2Book", "activeAssetCtx", "funding"]})
        self.db.commit()
        return session_id

    def record_clock_health(self, session_id: str, *, status: str, source: str | None, offset_seconds: float | None, raw: dict) -> None:
        now = _utc_now()
        self.db.execute("INSERT INTO recorder_clock_health(session_id,observed_at,monotonic_ns,status,source,offset_seconds,raw_json) VALUES (?,?,?,?,?,?,?)", (session_id, _iso(now), time.monotonic_ns(), status, source, offset_seconds, canonical_json(raw)))
        self.connection_event(session_id, "CLOCK_HEALTH_" + status, {"source": source, "offset_seconds": offset_seconds})
        self.db.commit()

    def observe_timedatectl(self, session_id: str) -> None:
        try:
            result = subprocess.run(["timedatectl", "show", "--property=NTPSynchronized", "--property=NTPService", "--property=SystemClockSynchronized"], text=True, capture_output=True, check=False, timeout=5)
            raw = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in result.stdout.splitlines() if "=" in line}
            healthy = raw.get("NTPSynchronized") == "yes" or raw.get("SystemClockSynchronized") == "yes"
            self.record_clock_health(session_id, status="HEALTHY" if healthy else "UNCERTAIN", source=raw.get("NTPService"), offset_seconds=None, raw=raw)
        except Exception as exc:
            self.record_clock_health(session_id, status="UNCERTAIN", source=None, offset_seconds=None, raw={"error": repr(exc)})

    def connection_event(self, session_id: str, event: str, metadata: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO recorder_connection_events(session_id,event,occurred_at,metadata_json) VALUES (?,?,?,?)",
            (session_id, event, _iso(_utc_now()), canonical_json(metadata or {})),
        )

    def start_gap(self, session_id: str, stream: str, reason: str, metadata: dict | None = None) -> str:
        gap_id = str(uuid4())
        self.db.execute(
            "INSERT INTO recorder_gap_events VALUES (?,?,?,?,?,?,?)",
            (gap_id, session_id, stream, _iso(_utc_now()), None, reason, canonical_json(metadata or {})),
        )
        self.db.commit()
        return gap_id

    def end_gap(self, gap_id: str, metadata: dict | None = None) -> None:
        self.db.execute("UPDATE recorder_gap_events SET ended_at=?,metadata_json=? WHERE gap_id=? AND ended_at IS NULL", (_iso(_utc_now()), canonical_json(metadata or {}), gap_id))
        self.db.commit()

    def ingest(self, *, session_id: str, stream: str, payload: dict, source_timestamp: datetime | None, source_key: str | None = None, received_at: datetime | None = None, monotonic_ns: int | None = None) -> str:
        received = received_at or _utc_now()
        source = _iso(source_timestamp) if source_timestamp else None
        digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        prior = self.db.execute("SELECT last_source_timestamp FROM recorder_stream_state WHERE stream=?", (stream,)).fetchone()
        out_of_order = bool(prior and source and prior[0] and source < prior[0])
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO recorder_raw_events(session_id,stream,source_timestamp,received_at,received_monotonic_ns,payload_json,payload_sha256,source_key,out_of_order) VALUES (?,?,?,?,?,?,?,?,?)",
            (session_id, stream, source, _iso(received), monotonic_ns or time.monotonic_ns(), canonical_json(payload), digest, source_key, int(out_of_order)),
        )
        if cursor.rowcount:
            self.db.execute(
                "INSERT INTO recorder_stream_state VALUES (?,?,?,?,?,?) ON CONFLICT(stream) DO UPDATE SET last_source_timestamp=excluded.last_source_timestamp,last_received_at=excluded.last_received_at,last_source_key=excluded.last_source_key,session_id=excluded.session_id,updated_at=excluded.updated_at",
                (stream, source, _iso(received), source_key, session_id, _iso(received)),
            )
        else:
            self.connection_event(session_id, "DUPLICATE", {"stream": stream, "payload_sha256": digest})
        self.db.commit()
        return "DUPLICATE" if not cursor.rowcount else ("OUT_OF_ORDER" if out_of_order else "RECORDED")

    def health(self) -> dict:
        integrity = self.db.execute("PRAGMA integrity_check").fetchone()[0]
        open_gaps = self.db.execute("SELECT COUNT(*) FROM recorder_gap_events WHERE ended_at IS NULL").fetchone()[0]
        rows = self.db.execute("SELECT stream,COUNT(*) n,MAX(received_at) last_received,SUM(out_of_order) out_of_order FROM recorder_raw_events GROUP BY stream").fetchall()
        duplicates = self.db.execute("SELECT COUNT(*) FROM recorder_connection_events WHERE event='DUPLICATE'").fetchone()[0]
        clock = self.db.execute("SELECT status FROM recorder_clock_health ORDER BY observation_id DESC LIMIT 1").fetchone()
        now = _utc_now()
        streams = {row["stream"]: {"events": row["n"], "last_received": row["last_received"], "out_of_order": row["out_of_order"], "staleness_seconds": (now - datetime.fromisoformat(row["last_received"])).total_seconds()} for row in rows}
        bearing = {"integrity": integrity, "open_gaps": open_gaps, "duplicate_events": duplicates, "clock_health": clock["status"] if clock else "UNOBSERVED", "reconnect_count": self.db.execute("SELECT COUNT(*) FROM recorder_connection_events WHERE event='DISCONNECTED'").fetchone()[0]}
        return {"classification": CLASSIFICATION, "integrity_bearing": bearing, **bearing, "capacity": {"db_bytes": self.database.stat().st_size if self.database.exists() else 0, "wal_bytes": (self.database.with_name(self.database.name + '-wal')).stat().st_size if self.database.with_name(self.database.name + '-wal').exists() else 0}, "events": self.db.execute("SELECT COUNT(*) FROM recorder_raw_events").fetchone()[0], "streams": streams}

    def seal_archive(self, archive_dir: str | Path, day: str) -> dict:
        archive = Path(archive_dir) / f"hype-raw-{day}.sqlite3"
        archive.parent.mkdir(parents=True, exist_ok=True)
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target = sqlite3.connect(archive)
        self.db.backup(target)
        target.close()
        integrity = sqlite3.connect(archive).execute("PRAGMA integrity_check").fetchone()[0]
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        return {"archive": str(archive), "integrity": integrity, "sha256": digest}

    def rotate_live(self, archive_dir: str | Path, day: str) -> dict:
        """Seal this active DB by atomic rename after draining/checkpointing."""
        archive = Path(archive_dir) / f"hype-raw-{day}.sqlite3"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            raise FileExistsError(f"sealed archive already exists: {archive}")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        integrity = self.db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("refusing rotation: active database integrity check failed")
        self.db.close()
        os.replace(self.database, archive)
        # A successful truncate checkpoint means these sidecars contain no
        # unsealed events.  Do not let an old WAL/SHM pair attach to the new
        # database created at the active path.
        for sidecar in (self.database.with_name(self.database.name + "-wal"), self.database.with_name(self.database.name + "-shm")):
            if sidecar.exists():
                sidecar.unlink()
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        # The recorder never reopens a sealed archive for writing.  The mode is
        # an additional OS-level guard; the SHA-256 is the content identity.
        archive.chmod(0o444)
        return {"archive": str(archive), "integrity": integrity, "sha256": digest}


@dataclass(frozen=True)
class QueueConfig:
    capacity: int = 10_000
    warning_occupancy: float = 0.70
    failure_occupancy: float = 0.90
    sustained_seconds: float = 60.0


class BufferedIngestion:
    """Bounded producer/writer boundary; overflow is explicit, never silent."""
    def __init__(self, recorder: ProspectiveRecorder, config: QueueConfig = QueueConfig()) -> None:
        self.recorder, self.config = recorder, config
        self.items: queue.Queue[dict] = queue.Queue(maxsize=config.capacity)
        self.max_depth = self.overflow_count = 0
        self.latencies: dict[str, list[float]] = {}
        self.over_warning_since: float | None = None
        self.recovery_seconds: list[float] = []
        self.writer_lock = threading.RLock()
        self.session_remap: dict[str, str] = {}
        self.last_rotation_gap_map: dict[str, str] = {}
        self.rotation_in_progress = False
        self.pending_overflow_evidence: list[dict] = []

    def submit(self, item: dict) -> str:
        try: self.items.put_nowait({**item, "enqueued_ns": time.monotonic_ns()})
        except queue.Full:
            self.overflow_count += 1
            evidence = {"stream": item["stream"], "capacity": self.config.capacity}
            if self.rotation_in_progress:
                # The old connection may already be closed.  Preserve the
                # failure indicator and write it as the first new-session
                # evidence rather than losing it with the rejected event.
                self.pending_overflow_evidence.append(evidence)
            else:
                self.recorder.connection_event(item["session_id"], "QUEUE_OVERFLOW", evidence)
            return "OVERFLOW_RECORDED_NOT_DURABLE"
        self.max_depth = max(self.max_depth, self.items.qsize())
        if self.items.qsize() >= self.config.warning_occupancy * self.config.capacity and self.over_warning_since is None: self.over_warning_since = time.monotonic()
        return "QUEUED"

    def drain(self, limit: int | None = None) -> int:
        """Single durable writer; producers may continue queueing during rotation."""
        with self.writer_lock:
            count = 0
            while not self.items.empty() and (limit is None or count < limit):
                item = self.items.get_nowait(); enqueued = item.pop("enqueued_ns")
                session_id = item["session_id"]
                while session_id in self.session_remap:
                    session_id = self.session_remap[session_id]
                item["session_id"] = session_id
                self.recorder.ingest(**item)
                self.latencies.setdefault(item["stream"], []).append((time.monotonic_ns() - enqueued) / 1e9)
                count += 1
            if self.over_warning_since is not None and self.items.qsize() < self.config.warning_occupancy * self.config.capacity:
                self.recovery_seconds.append(time.monotonic() - self.over_warning_since); self.over_warning_since = None
            return count

    def health(self) -> dict:
        percentile = lambda v, p: sorted(v)[int((len(v)-1)*p)] if v else None
        return {"queue_depth": self.items.qsize(), "max_queue_depth": self.max_depth, "overflow_count": self.overflow_count, "warning_sustained_seconds": (time.monotonic()-self.over_warning_since) if self.over_warning_since else 0.0, "recovery_seconds": self.recovery_seconds, "warning": self.items.qsize() >= self.config.warning_occupancy*self.config.capacity, "failure": self.items.qsize() >= self.config.failure_occupancy*self.config.capacity or self.overflow_count > 0, "write_latency_seconds": {s: {"count": len(v), "p50": percentile(v,.5), "p95": percentile(v,.95), "p99": percentile(v,.99), "max": max(v) if v else None} for s,v in self.latencies.items()}}

    def rotate(self, archive_dir: str | Path, day: str, prior_session_id: str, metadata: dict | None = None, rotation_hook=None) -> tuple[dict, str]:
        """Seal the old day and switch the sole durable writer to a fresh DB.

        Producers are intentionally not stopped: they can queue while the
        writer lock is held.  Every item carrying the prior session is remapped
        to the new session before it can be durably written in the new DB.
        """
        with self.writer_lock:
            self.drain()
            open_gaps = self.recorder.db.execute(
                "SELECT gap_id,stream,reason,metadata_json FROM recorder_gap_events WHERE ended_at IS NULL"
            ).fetchall()
            latest_clock = self.recorder.db.execute(
                "SELECT status,source,offset_seconds,raw_json FROM recorder_clock_health ORDER BY observation_id DESC LIMIT 1"
            ).fetchone()
            self.rotation_in_progress = True
            sealed = self.recorder.rotate_live(archive_dir, day)
            # Test/commissioning hook represents network arrivals while the
            # active path is being switched; ordinary producers use the queue.
            if rotation_hook is not None:
                rotation_hook()
            self.recorder = ProspectiveRecorder(self.recorder.database)
            new_session = self.recorder.start_session(
                reconnect_of=prior_session_id,
                metadata={"rotation_from_session": prior_session_id, **(metadata or {})},
            )
            self.session_remap[prior_session_id] = new_session
            self.last_rotation_gap_map = {}
            for gap in open_gaps:
                carried = self.recorder.start_gap(
                    new_session,
                    gap["stream"],
                    "CARRIED_OPEN_GAP_AFTER_ROTATION",
                    {"prior_gap_id": gap["gap_id"], "prior_reason": gap["reason"], "prior_metadata_json": gap["metadata_json"]},
                )
                self.last_rotation_gap_map[gap["gap_id"]] = carried
            self.recorder.connection_event(
                new_session,
                "DAILY_ROTATION_COMPLETE",
                {**sealed, "prior_session_id": prior_session_id, "open_gap_count": len(open_gaps),
                 "prior_clock_health": dict(latest_clock) if latest_clock else None},
            )
            for evidence in self.pending_overflow_evidence:
                self.recorder.connection_event(new_session, "QUEUE_OVERFLOW", evidence)
            self.pending_overflow_evidence.clear()
            self.recorder.observe_timedatectl(new_session)
            self.rotation_in_progress = False
            self.drain()
            return sealed, new_session
