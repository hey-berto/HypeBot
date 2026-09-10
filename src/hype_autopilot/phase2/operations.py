from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.supervision import read_lease_owner


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve(strict=True)
    db = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _process_alive(owner: dict[str, object] | None) -> bool:
    if owner is None or not isinstance(owner.get("pid"), int):
        return False
    try:
        os.kill(int(owner["pid"]), 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def phase2_operational_health(
    database: str | Path,
    *,
    worker_lease: str | Path,
    supervisor_lease: str | Path,
    now: datetime | None = None,
    maximum_boundary_age_minutes: float = 20.0,
) -> dict[str, Any]:
    """Return sanitized operational health without performance or outcome fields."""
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    db = _read_only(database)
    try:
        latest = db.execute(
            "SELECT scheduled_at,status FROM research_cycles "
            "ORDER BY scheduled_at DESC LIMIT 1"
        ).fetchone()
        boundary_age = None
        if latest is not None:
            boundary_age = max(
                0.0,
                (
                    observed_at
                    - datetime.fromisoformat(latest["scheduled_at"]).astimezone(UTC)
                ).total_seconds()
                / 60,
            )
        duplicate_queries = {
            "cycles": "SELECT COUNT(*) FROM (SELECT cycle_id,COUNT(*) n FROM research_cycles GROUP BY cycle_id HAVING n>1)",
            "snapshots": "SELECT COUNT(*) FROM (SELECT snapshot_id,COUNT(*) n FROM decision_snapshots GROUP BY snapshot_id HAVING n>1)",
            "strategy_decisions": "SELECT COUNT(*) FROM (SELECT decision_id,COUNT(*) n FROM strategy_decisions GROUP BY decision_id HAVING n>1)",
            "llm_attempts": "SELECT COUNT(*) FROM (SELECT input_snapshot_hash,attempt,COUNT(*) n FROM llm_invocation_attempts GROUP BY input_snapshot_hash,attempt HAVING n>1)",
        }
        worker = read_lease_owner(worker_lease)
        supervisor = read_lease_owner(supervisor_lease)
        result = {
            "telemetry_scope": "OPERATIONAL_ONLY_NO_PERFORMANCE_FIELDS",
            "observed_at": observed_at.isoformat(),
            "latest_boundary": latest["scheduled_at"] if latest else None,
            "latest_cycle_status": latest["status"] if latest else None,
            "latest_boundary_age_minutes": boundary_age,
            "database_integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "duplicates": {
                name: db.execute(query).fetchone()[0]
                for name, query in duplicate_queries.items()
            },
            "open_collection_gaps": db.execute(
                "SELECT COUNT(*) FROM collection_gaps WHERE status='OPEN'"
            ).fetchone()[0],
            "worker": {"alive": _process_alive(worker), "metadata": worker},
            "supervisor": {"alive": _process_alive(supervisor), "metadata": supervisor},
        }
        result["healthy"] = bool(
            result["database_integrity"] == "ok"
            and result["foreign_key_violations"] == 0
            and not any(result["duplicates"].values())
            and result["worker"]["alive"]
            and result["supervisor"]["alive"]
            and boundary_age is not None
            and boundary_age <= maximum_boundary_age_minutes
            and latest["status"] in {"COMPLETE", "REJECTED"}
        )
        return result
    finally:
        db.close()


def sqlite_consistent_backup(source: str | Path, destination: str | Path) -> dict[str, Any]:
    source_path = Path(source).resolve(strict=True)
    destination_path = Path(destination).resolve()
    if destination_path.exists():
        raise FileExistsError("backup destination already exists")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_db = _read_only(source_path)
    destination_db = sqlite3.connect(destination_path)
    try:
        source_db.backup(destination_db)
    finally:
        destination_db.close()
        source_db.close()
    with _read_only(destination_path) as check:
        identity = {
            "backup_version": "PHASE2_SQLITE_BACKUP_V1",
            "source_name": source_path.name,
            "destination": str(destination_path),
            "size_bytes": destination_path.stat().st_size,
            "sha256": file_sha256(destination_path),
            "integrity": check.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(check.execute("PRAGMA foreign_key_check").fetchall()),
            "created_at": datetime.now(UTC).isoformat(),
        }
    return {**identity, "manifest_hash": sha256_canonical(identity)}


def verify_backup(path: str | Path, expected_sha256: str) -> dict[str, Any]:
    target = Path(path).resolve(strict=True)
    observed = file_sha256(target)
    if observed != expected_sha256:
        raise ValueError("backup SHA-256 mismatch")
    with _read_only(target) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())
    if integrity != "ok" or foreign_keys:
        raise ValueError("backup SQLite integrity validation failed")
    return {
        "path": str(target),
        "sha256": observed,
        "integrity": integrity,
        "foreign_key_violations": foreign_keys,
    }
