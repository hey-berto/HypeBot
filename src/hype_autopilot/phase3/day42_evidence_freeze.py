"""Day-42 read-safe evidence freeze; deliberately separate from Phase 3 gate."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.operations import file_sha256, sqlite_consistent_backup
from hype_autopilot.phase3.operational import (
    Phase3OperationalBinding,
    _validated_evidence_window,
    load_phase3_operational_binding,
)

FREEZE_VERSION = "EPOCH006_DAY42_EVIDENCE_FREEZE_V1"
EPOCH006_CUTOFF = datetime(2026, 11, 5, 0, 15, tzinfo=UTC)
EXPECTED_EVALUATOR_SHA256 = "e606da9c85a6752de2a0bf3b5ba7e53819d8d78fcf46c0b020cdc5a4c52ce78b"


@dataclass(frozen=True)
class FreezeRequest:
    source_database: Path
    package_directory: Path
    binding_path: Path
    frozen_config_path: Path
    tool_root: Path
    runtime_observation: dict[str, Any]
    freeze_timestamp: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("freeze timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _duplicates(db: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "research_cycles": "SELECT COUNT(*) FROM (SELECT scheduled_at,observation_class,COUNT(*) n FROM research_cycles GROUP BY scheduled_at,observation_class HAVING n>1)",
        "decision_snapshots": "SELECT COUNT(*) FROM (SELECT snapshot_hash,COUNT(*) n FROM decision_snapshots GROUP BY snapshot_hash HAVING n>1)",
        "llm_attempts": "SELECT COUNT(*) FROM (SELECT input_snapshot_hash,attempt,COUNT(*) n FROM llm_invocation_attempts GROUP BY input_snapshot_hash,attempt HAVING n>1)",
        "strategy_decisions": "SELECT COUNT(*) FROM (SELECT snapshot_hash,strategy_id,strategy_version,COUNT(*) n FROM strategy_decisions GROUP BY snapshot_hash,strategy_id,strategy_version HAVING n>1)",
        "paper_orders": "SELECT COUNT(*) FROM (SELECT strategy_decision_id,COUNT(*) n FROM paper_orders GROUP BY strategy_decision_id HAVING n>1)",
        "paper_fills": "SELECT COUNT(*) FROM (SELECT paper_trade_id,fill_type,fill_time,COUNT(*) n FROM paper_fills GROUP BY paper_trade_id,fill_type,fill_time HAVING n>1)",
    }
    return {name: int(db.execute(query).fetchone()[0]) for name, query in queries.items()}


def _source_facts(path: Path, binding: Phase3OperationalBinding) -> dict[str, Any]:
    db = _ro(path)
    try:
        manifests = db.execute("SELECT payload_json FROM phase2_manifests").fetchall()
        if len(manifests) != 1:
            raise ValueError("freeze requires exactly one Phase 2 manifest")
        manifest = json.loads(manifests[0]["payload_json"])
        if manifest.get("phase2_epoch_id") != binding.phase2_epoch_id:
            raise ValueError("source manifest epoch does not match epoch006 binding")
        if manifest.get("git_commit_hash") != binding.research_commit:
            raise ValueError("source research commit does not match epoch006 binding")
        first_boundary, reset = _validated_evidence_window(db, phase2_epoch_id=binding.phase2_epoch_id)
        anchors = db.execute(
            "SELECT occurred_at FROM phase2_recovery_events WHERE phase2_epoch_id=? AND event_type='PROSPECTIVE_START_ESTABLISHED'",
            (binding.phase2_epoch_id,),
        ).fetchall()
        if len(anchors) != 1:
            raise ValueError("freeze requires exactly one prospective-start anchor")
        anchor = datetime.fromisoformat(anchors[0]["occurred_at"]).astimezone(UTC)
        if reset or anchor != binding.prospective_anchor or first_boundary != binding.first_eligible_boundary:
            raise ValueError("source anchor/window does not match epoch006 binding")
        cutoff_rows = db.execute(
            "SELECT cycle_id,status,details_json,completed_at FROM research_cycles WHERE scheduled_at=? AND observation_class='SCORED_PROSPECTIVE'",
            (EPOCH006_CUTOFF.isoformat(),),
        ).fetchall()
        if len(cutoff_rows) != 1:
            raise ValueError("cutoff boundary must have exactly one research cycle")
        cutoff_cycle = cutoff_rows[0]
        if cutoff_cycle["status"] not in {"COMPLETE", "REJECTED"}:
            raise ValueError("cutoff cycle must be terminal before freeze")
        future_cycles = int(db.execute(
            "SELECT COUNT(*) FROM research_cycles WHERE observation_class='SCORED_PROSPECTIVE' AND scheduled_at>?",
            (EPOCH006_CUTOFF.isoformat(),),
        ).fetchone()[0])
        if future_cycles:
            raise ValueError("source contains post-cutoff scored cycles; exact cutoff state is no longer available")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())
        duplicates = _duplicates(db)
        if integrity != "ok" or foreign_keys or any(duplicates.values()):
            raise ValueError("source database integrity, foreign keys, or structural duplicates failed")
        window = db.execute(
            "SELECT window_id,integrity_hash FROM phase2_evidence_windows WHERE phase2_epoch_id=?",
            (binding.phase2_epoch_id,),
        ).fetchone()
        gaps = int(db.execute("SELECT COUNT(*) FROM collection_gaps WHERE status='OPEN'").fetchone()[0])
        return {
            "epoch_id": binding.phase2_epoch_id,
            "manifest": manifest,
            "evidence_window_id": window["window_id"],
            "evidence_window_integrity_hash": window["integrity_hash"],
            "prospective_anchor": anchor.isoformat(),
            "first_eligible_boundary": first_boundary.isoformat(),
            "cutoff_cycle": dict(cutoff_cycle),
            "open_collection_gaps": gaps,
            "database_integrity": integrity,
            "foreign_key_violations": foreign_keys,
            "duplicates": duplicates,
        }
    finally:
        db.close()


def _tool_hashes(tool_root: Path) -> dict[str, str]:
    evaluator = tool_root / "src/hype_autopilot/phase3/gate.py"
    reader = tool_root / "src/hype_autopilot/phase3/operational.py"
    shadow = tool_root / "src/hype_autopilot/phase3/right_censoring_shadow.py"
    values = {"phase3_evaluator_sha256": file_sha256(evaluator), "operational_reader_sha256": file_sha256(reader), "right_censoring_diagnostic_sha256": file_sha256(shadow)}
    if values["phase3_evaluator_sha256"] != EXPECTED_EVALUATOR_SHA256:
        raise ValueError("Phase 3 evaluator SHA-256 mismatch")
    return values


def freeze_day42_evidence(request: FreezeRequest) -> dict[str, Any]:
    """Make a consistent SQLite backup and immutable package without pausing writer."""
    freeze_at = _utc(request.freeze_timestamp)
    if freeze_at < EPOCH006_CUTOFF:
        raise ValueError("Day-42 freeze cannot occur before the formal cutoff")
    binding = load_phase3_operational_binding(request.binding_path)
    if binding.calendar_floor != EPOCH006_CUTOFF:
        raise ValueError("operational binding cutoff does not match formal Day-42 cutoff")
    if request.source_database.name != Path(binding.source_database).name:
        raise ValueError("source database filename does not match epoch006 binding")
    package = request.package_directory
    if package.exists():
        raise FileExistsError("evidence package directory already exists")
    facts = _source_facts(request.source_database, binding)
    hashes = _tool_hashes(request.tool_root)
    package.mkdir(parents=True)
    frozen_db = package / "epoch006-cutoff.sqlite3"
    config_copy = package / "epoch006-frozen-config.yaml"
    backup = sqlite_consistent_backup(request.source_database, frozen_db)
    shutil.copyfile(request.frozen_config_path, config_copy)
    with _ro(frozen_db) as frozen:
        if frozen.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or frozen.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("frozen SQLite backup integrity validation failed")
    frozen_db.chmod(0o444)
    config_copy.chmod(0o444)
    manifest = {
        "freeze_version": FREEZE_VERSION,
        "epoch_id": facts["epoch_id"],
        "evidence_window_id": facts["evidence_window_id"],
        "evidence_window_integrity_hash": facts["evidence_window_integrity_hash"],
        "prospective_anchor": facts["prospective_anchor"],
        "first_eligible_boundary": facts["first_eligible_boundary"],
        "final_cutoff": EPOCH006_CUTOFF.isoformat(),
        "freeze_timestamp": freeze_at.isoformat(),
        "source_database_path": str(request.source_database.resolve()),
        "source_database_filename": request.source_database.name,
        "frozen_database": {"filename": frozen_db.name, "sha256": backup["sha256"], "size_bytes": backup["size_bytes"], "schema_hash": facts["manifest"]["database_schema_hash"]},
        "frozen_config": {"filename": config_copy.name, "file_sha256": file_sha256(config_copy), "canonical_config_hash": facts["manifest"]["config_hash"]},
        "research_commit": facts["manifest"]["git_commit_hash"],
        "operations_commit": binding.operations_commit,
        "prompt_hash": facts["manifest"]["prompt_hash"],
        "output_schema_hash": facts["manifest"]["output_schema_hash"],
        "cutoff_cycle": facts["cutoff_cycle"],
        "source_validation": {key: facts[key] for key in ("database_integrity", "foreign_key_violations", "duplicates", "open_collection_gaps")},
        "runtime_observation": request.runtime_observation,
        "tool_versions": {**hashes, "right_censoring_diagnostic_commit": "9ed3f64a26e492de4b9ee6a6e1adc00e0341e1dd"},
    }
    manifest["manifest_hash"] = sha256_canonical(manifest)
    manifest_path = package / "manifest.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    manifest_path.chmod(0o444)
    return manifest
