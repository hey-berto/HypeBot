from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.data.repository import Repository
from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.models import InvocationAttempt, LLMDecisionRecord
from hype_autopilot.storage.schema import SCHEMA as PHASE1_SCHEMA

PHASE2_SCHEMA = """
CREATE TABLE IF NOT EXISTS phase2_manifests (
  manifest_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, phase2_epoch_id TEXT NOT NULL,
  activation_timestamp TEXT NOT NULL, git_commit_hash TEXT NOT NULL, config_hash TEXT NOT NULL,
  prompt_hash TEXT NOT NULL, output_schema_hash TEXT NOT NULL, manifest_hash TEXT NOT NULL UNIQUE,
  database_schema_hash TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_invocation_attempts (
  attempt_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, phase2_epoch_id TEXT NOT NULL,
  input_snapshot_hash TEXT NOT NULL REFERENCES decision_snapshots(snapshot_hash),
  attempt INTEGER NOT NULL, started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
  provider_status TEXT NOT NULL, error_code TEXT, tool_calls_count INTEGER NOT NULL,
  raw_output_hash TEXT, raw_output_plaintext TEXT,
  raw_capture_status TEXT NOT NULL CHECK (
    raw_capture_status IN ('NOT_AVAILABLE', 'CAPTURED', 'WITHHELD_SENSITIVE')
  ),
  payload_json TEXT NOT NULL, integrity_hash TEXT NOT NULL UNIQUE,
  CHECK (
    (raw_capture_status = 'CAPTURED' AND raw_output_plaintext IS NOT NULL AND raw_output_hash IS NOT NULL)
    OR (raw_capture_status != 'CAPTURED' AND raw_output_plaintext IS NULL)
  ),
  UNIQUE(experiment_id, phase2_epoch_id, input_snapshot_hash, attempt)
);
CREATE TABLE IF NOT EXISTS llm_decisions (
  decision_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, phase2_epoch_id TEXT NOT NULL,
  input_snapshot_hash TEXT NOT NULL REFERENCES decision_snapshots(snapshot_hash),
  strategy_version TEXT NOT NULL, decision TEXT NOT NULL, runner_status TEXT NOT NULL,
  reason_code TEXT NOT NULL, timestamp TEXT NOT NULL, payload_json TEXT NOT NULL,
  integrity_hash TEXT NOT NULL UNIQUE,
  UNIQUE(experiment_id, phase2_epoch_id, input_snapshot_hash, strategy_version)
);
CREATE TABLE IF NOT EXISTS phase2_pair_outcomes (
  pair_id TEXT NOT NULL, input_snapshot_hash TEXT NOT NULL REFERENCES decision_snapshots(snapshot_hash),
  eligibility_status TEXT NOT NULL, outcome_status TEXT NOT NULL, payload_json TEXT NOT NULL,
  integrity_hash TEXT NOT NULL UNIQUE, PRIMARY KEY(pair_id, input_snapshot_hash)
);
CREATE TRIGGER IF NOT EXISTS immutable_phase2_manifests_update BEFORE UPDATE ON phase2_manifests
BEGIN SELECT RAISE(ABORT, 'phase2 manifests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_manifests_delete BEFORE DELETE ON phase2_manifests
BEGIN SELECT RAISE(ABORT, 'phase2 manifests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_llm_attempts_update BEFORE UPDATE ON llm_invocation_attempts
BEGIN SELECT RAISE(ABORT, 'llm invocation attempts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_llm_attempts_delete BEFORE DELETE ON llm_invocation_attempts
BEGIN SELECT RAISE(ABORT, 'llm invocation attempts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_llm_decisions_update BEFORE UPDATE ON llm_decisions
BEGIN SELECT RAISE(ABORT, 'llm decisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_llm_decisions_delete BEFORE DELETE ON llm_decisions
BEGIN SELECT RAISE(ABORT, 'llm decisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_pair_outcomes_update BEFORE UPDATE ON phase2_pair_outcomes
BEGIN SELECT RAISE(ABORT, 'phase2 pair outcomes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_pair_outcomes_delete BEFORE DELETE ON phase2_pair_outcomes
BEGIN SELECT RAISE(ABORT, 'phase2 pair outcomes are immutable'); END;
"""


def phase2_database_schema_hash() -> str:
    return sha256_canonical(
        {"phase1_schema": PHASE1_SCHEMA, "phase2_schema": PHASE2_SCHEMA}
    )


class Phase2Repository:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.core = Repository(db)

    def initialize(self) -> None:
        self.core.initialize()
        self.db.executescript(PHASE2_SCHEMA)
        self.db.commit()

    def save_manifest(self, manifest: Phase2Manifest) -> Phase2Manifest:
        payload = canonical_json(manifest)
        try:
            self.db.execute(
                "INSERT INTO phase2_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    manifest.manifest_id,
                    manifest.experiment_id,
                    manifest.phase2_epoch_id,
                    manifest.activation_timestamp.isoformat(),
                    manifest.git_commit_hash,
                    manifest.config_hash,
                    manifest.prompt_hash,
                    manifest.output_schema_hash,
                    manifest.manifest_hash,
                    manifest.database_schema_hash,
                    payload,
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json FROM phase2_manifests WHERE manifest_id = ?",
                (manifest.manifest_id,),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise RuntimeError("immutable Phase 2 manifest conflict")
        return manifest

    def save_attempt(self, attempt: InvocationAttempt) -> InvocationAttempt:
        payload = canonical_json(attempt)
        integrity = sha256_canonical(attempt)
        attempt_id = str(uuid5(NAMESPACE_URL, f"phase2-attempt:{integrity}"))
        try:
            self.db.execute(
                "INSERT INTO llm_invocation_attempts "
                "(attempt_id, experiment_id, phase2_epoch_id, input_snapshot_hash, attempt, "
                "started_at, ended_at, provider_status, error_code, tool_calls_count, "
                "raw_output_hash, raw_output_plaintext, raw_capture_status, payload_json, "
                "integrity_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    attempt.experiment_id,
                    attempt.phase2_epoch_id,
                    attempt.input_snapshot_hash,
                    attempt.attempt,
                    attempt.started_at.isoformat(),
                    attempt.ended_at.isoformat(),
                    attempt.provider_status,
                    attempt.error_code,
                    attempt.tool_calls_count,
                    attempt.raw_output_hash,
                    attempt.raw_output_plaintext,
                    attempt.raw_capture_status,
                    payload,
                    integrity,
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json FROM llm_invocation_attempts WHERE experiment_id=? AND phase2_epoch_id=? "
                "AND input_snapshot_hash=? AND attempt=?",
                (
                    attempt.experiment_id,
                    attempt.phase2_epoch_id,
                    attempt.input_snapshot_hash,
                    attempt.attempt,
                ),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise RuntimeError("immutable LLM invocation attempt conflict")
        return attempt

    def save_llm_decision(
        self, strategy_version: str, record: LLMDecisionRecord
    ) -> LLMDecisionRecord:
        payload = canonical_json(record)
        integrity = sha256_canonical(record)
        decision_id = str(
            uuid5(
                NAMESPACE_URL,
                f"phase2-llm:{record.experiment_id}:{record.phase2_epoch_id}:"
                f"{record.input_snapshot_hash}:{strategy_version}",
            )
        )
        try:
            self.db.execute(
                "INSERT INTO llm_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_id,
                    record.experiment_id,
                    record.phase2_epoch_id,
                    record.input_snapshot_hash,
                    strategy_version,
                    record.decision.value,
                    record.runner_status.value,
                    record.reason_code.value,
                    record.timestamp.isoformat(),
                    payload,
                    integrity,
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json FROM llm_decisions WHERE experiment_id=? AND phase2_epoch_id=? "
                "AND input_snapshot_hash=? AND strategy_version=?",
                (
                    record.experiment_id,
                    record.phase2_epoch_id,
                    record.input_snapshot_hash,
                    strategy_version,
                ),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise RuntimeError(
                    "duplicate LLM scoring differs from immutable original"
                )
        return record

    def load_llm_decision(
        self,
        experiment_id: str,
        phase2_epoch_id: str,
        snapshot_hash: str,
        strategy_version: str,
    ) -> LLMDecisionRecord | None:
        row = self.db.execute(
            "SELECT payload_json FROM llm_decisions WHERE experiment_id=? AND phase2_epoch_id=? "
            "AND input_snapshot_hash=? AND strategy_version=?",
            (experiment_id, phase2_epoch_id, snapshot_hash, strategy_version),
        ).fetchone()
        return (
            LLMDecisionRecord.model_validate(json.loads(row["payload_json"]))
            if row
            else None
        )

    def save_pair_outcome(
        self,
        *,
        pair_id: str,
        snapshot_hash: str,
        eligibility_status: str,
        outcome_status: str,
        payload: dict[str, Any],
    ) -> None:
        payload_json = canonical_json(payload)
        integrity = sha256_canonical(payload)
        self.db.execute(
            "INSERT INTO phase2_pair_outcomes VALUES (?, ?, ?, ?, ?, ?)",
            (
                pair_id,
                snapshot_hash,
                eligibility_status,
                outcome_status,
                payload_json,
                integrity,
            ),
        )
        self.db.commit()

    def integrity(self) -> tuple[str, int]:
        integrity = str(self.db.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(self.db.execute("PRAGMA foreign_key_check").fetchall())
        return integrity, foreign_keys

    def total_model_cost_usd(self) -> float:
        rows = self.db.execute("SELECT payload_json FROM llm_decisions").fetchall()
        return sum(
            LLMDecisionRecord.model_validate(
                json.loads(row["payload_json"])
            ).model_cost_usd
            for row in rows
        )
