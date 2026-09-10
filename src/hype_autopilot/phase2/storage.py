from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.data.repository import Repository
from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.models import InvocationAttempt, LLMDecisionRecord
from hype_autopilot.simulation.models import PaperTrade
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
CREATE TABLE IF NOT EXISTS phase2_recovery_events (
  recovery_event_id TEXT PRIMARY KEY, phase2_epoch_id TEXT NOT NULL,
  event_type TEXT NOT NULL, source_identity TEXT NOT NULL, occurred_at TEXT NOT NULL,
  payload_json TEXT NOT NULL, integrity_hash TEXT NOT NULL UNIQUE,
  UNIQUE(phase2_epoch_id, event_type, source_identity)
);
CREATE TABLE IF NOT EXISTS phase2_outcome_exclusions (
  exclusion_id TEXT PRIMARY KEY,
  paper_trade_id TEXT NOT NULL UNIQUE REFERENCES paper_trades(paper_trade_id),
  phase2_epoch_id TEXT NOT NULL, excluded_at TEXT NOT NULL, reason_code TEXT NOT NULL,
  source_identity TEXT NOT NULL, payload_json TEXT NOT NULL,
  integrity_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS phase2_operational_deployments (
  deployment_id TEXT PRIMARY KEY, phase2_epoch_id TEXT NOT NULL,
  base_manifest_hash TEXT NOT NULL REFERENCES phase2_manifests(manifest_hash),
  source_commit TEXT NOT NULL, database_schema_hash TEXT NOT NULL,
  classification TEXT NOT NULL CHECK(classification='OPERATIONAL_ONLY'),
  deployed_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  integrity_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS phase2_evidence_windows (
  window_id TEXT PRIMARY KEY, phase2_epoch_id TEXT NOT NULL,
  first_eligible_boundary TEXT NOT NULL, deployment_id TEXT NOT NULL
    REFERENCES phase2_operational_deployments(deployment_id),
  reason_code TEXT NOT NULL, payload_json TEXT NOT NULL,
  integrity_hash TEXT NOT NULL UNIQUE,
  UNIQUE(phase2_epoch_id, first_eligible_boundary)
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
CREATE TRIGGER IF NOT EXISTS immutable_phase2_recovery_events_update BEFORE UPDATE ON phase2_recovery_events
BEGIN SELECT RAISE(ABORT, 'phase2 recovery events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_recovery_events_delete BEFORE DELETE ON phase2_recovery_events
BEGIN SELECT RAISE(ABORT, 'phase2 recovery events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_outcome_exclusions_update BEFORE UPDATE ON phase2_outcome_exclusions
BEGIN SELECT RAISE(ABORT, 'phase2 outcome exclusions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_outcome_exclusions_delete BEFORE DELETE ON phase2_outcome_exclusions
BEGIN SELECT RAISE(ABORT, 'phase2 outcome exclusions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_operational_deployments_update BEFORE UPDATE ON phase2_operational_deployments
BEGIN SELECT RAISE(ABORT, 'phase2 operational deployments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_operational_deployments_delete BEFORE DELETE ON phase2_operational_deployments
BEGIN SELECT RAISE(ABORT, 'phase2 operational deployments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_evidence_windows_update BEFORE UPDATE ON phase2_evidence_windows
BEGIN SELECT RAISE(ABORT, 'phase2 evidence windows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_phase2_evidence_windows_delete BEFORE DELETE ON phase2_evidence_windows
BEGIN SELECT RAISE(ABORT, 'phase2 evidence windows are immutable'); END;
CREATE VIEW IF NOT EXISTS phase2_evidence_eligible_trades AS
SELECT t.* FROM paper_trades t
JOIN decision_snapshots s ON s.snapshot_hash=t.snapshot_hash
WHERE NOT EXISTS (
  SELECT 1 FROM phase2_outcome_exclusions x
  WHERE x.paper_trade_id=t.paper_trade_id
)
AND (
  NOT EXISTS (
    SELECT 1 FROM phase2_evidence_windows w
    WHERE w.phase2_epoch_id=s.epoch_id
  )
  OR t.signal_time >= (
    SELECT MAX(w.first_eligible_boundary) FROM phase2_evidence_windows w
    WHERE w.phase2_epoch_id=s.epoch_id
  )
);
CREATE VIEW IF NOT EXISTS phase2_evidence_eligible_pair_outcomes AS
SELECT p.* FROM phase2_pair_outcomes p
JOIN decision_snapshots s ON s.snapshot_hash=p.input_snapshot_hash
WHERE NOT EXISTS (
  SELECT 1 FROM phase2_outcome_exclusions x
  JOIN paper_trades t ON t.paper_trade_id=x.paper_trade_id
  WHERE t.snapshot_hash=p.input_snapshot_hash
)
AND (
  NOT EXISTS (
    SELECT 1 FROM phase2_evidence_windows w
    WHERE w.phase2_epoch_id=s.epoch_id
  )
  OR s.snapshot_timestamp >= (
    SELECT MAX(w.first_eligible_boundary) FROM phase2_evidence_windows w
    WHERE w.phase2_epoch_id=s.epoch_id
  )
);
"""


def phase2_database_schema_hash() -> str:
    return sha256_canonical(
        {"phase1_schema": PHASE1_SCHEMA, "phase2_schema": PHASE2_SCHEMA}
    )


class Phase2CoreRepository(Repository):
    """Core repository view that never progresses outcome-excluded trades."""

    def active_trades(self) -> list[PaperTrade]:
        rows = self.db.execute(
            "SELECT t.payload_json FROM paper_trades t "
            "LEFT JOIN phase2_outcome_exclusions x "
            "ON x.paper_trade_id=t.paper_trade_id "
            "WHERE t.status IN ('PENDING_ENTRY','OPEN') "
            "AND x.paper_trade_id IS NULL ORDER BY t.signal_time"
        ).fetchall()
        return [
            PaperTrade.model_validate(json.loads(row["payload_json"])) for row in rows
        ]


class Phase2Repository:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.core = Phase2CoreRepository(db)

    def atomic(self):
        factory = getattr(self.db, "atomic", None)
        return factory() if factory is not None else nullcontext()

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

    def load_attempts(
        self,
        experiment_id: str,
        phase2_epoch_id: str,
        snapshot_hash: str,
    ) -> tuple[InvocationAttempt, ...]:
        rows = self.db.execute(
            "SELECT payload_json FROM llm_invocation_attempts "
            "WHERE experiment_id=? AND phase2_epoch_id=? AND input_snapshot_hash=? "
            "ORDER BY attempt",
            (experiment_id, phase2_epoch_id, snapshot_hash),
        ).fetchall()
        return tuple(
            InvocationAttempt.model_validate(json.loads(row["payload_json"]))
            for row in rows
        )

    def record_recovery_event(
        self,
        *,
        phase2_epoch_id: str,
        event_type: str,
        source_identity: str,
        payload: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> str:
        body = {
            "phase2_epoch_id": phase2_epoch_id,
            "event_type": event_type,
            "source_identity": source_identity,
            "occurred_at": (occurred_at or datetime.now(UTC))
            .astimezone(UTC)
            .isoformat(),
            "details": payload,
        }
        integrity = sha256_canonical(body)
        event_id = str(uuid5(NAMESPACE_URL, f"phase2-recovery:{integrity}"))
        existing = self.db.execute(
            "SELECT recovery_event_id,payload_json FROM phase2_recovery_events "
            "WHERE phase2_epoch_id=? AND event_type=? AND source_identity=?",
            (phase2_epoch_id, event_type, source_identity),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != canonical_json(body):
                raise RuntimeError("immutable Phase 2 recovery event conflict")
            return str(existing["recovery_event_id"])
        try:
            self.db.execute(
                "INSERT INTO phase2_recovery_events VALUES (?,?,?,?,?,?,?)",
                (
                    event_id,
                    phase2_epoch_id,
                    event_type,
                    source_identity,
                    body["occurred_at"],
                    canonical_json(body),
                    integrity,
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT recovery_event_id FROM phase2_recovery_events "
                "WHERE phase2_epoch_id=? AND event_type=? AND source_identity=?",
                (phase2_epoch_id, event_type, source_identity),
            ).fetchone()
            if row is None:
                raise
            event_id = str(row[0])
        return event_id

    def exclude_trade_outcome(
        self,
        *,
        paper_trade_id: str,
        phase2_epoch_id: str,
        reason_code: str,
        source_identity: str,
        excluded_at: datetime | None = None,
    ) -> str:
        excluded = (excluded_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        body = {
            "paper_trade_id": paper_trade_id,
            "phase2_epoch_id": phase2_epoch_id,
            "excluded_at": excluded,
            "reason_code": reason_code,
            "source_identity": source_identity,
            "decision_status": "VALID_DECISION",
            "outcome_status": "OUTCOME_EXCLUDED",
        }
        integrity = sha256_canonical(body)
        exclusion_id = str(uuid5(NAMESPACE_URL, f"phase2-exclusion:{integrity}"))
        existing = self.db.execute(
            "SELECT exclusion_id,payload_json FROM phase2_outcome_exclusions "
            "WHERE paper_trade_id=?",
            (paper_trade_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != canonical_json(body):
                raise RuntimeError("immutable Phase 2 outcome exclusion conflict")
            return str(existing["exclusion_id"])
        try:
            self.db.execute(
                "INSERT INTO phase2_outcome_exclusions VALUES (?,?,?,?,?,?,?,?)",
                (
                    exclusion_id,
                    paper_trade_id,
                    phase2_epoch_id,
                    excluded,
                    reason_code,
                    source_identity,
                    canonical_json(body),
                    integrity,
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT exclusion_id,payload_json FROM phase2_outcome_exclusions "
                "WHERE paper_trade_id=?",
                (paper_trade_id,),
            ).fetchone()
            if row is None or json.loads(row["payload_json"]) != body:
                raise RuntimeError("immutable Phase 2 outcome exclusion conflict")
            exclusion_id = str(row["exclusion_id"])
        return exclusion_id

    def freeze_predeployment_nonterminal_exclusions(
        self,
        *,
        phase2_epoch_id: str,
        before: datetime,
        source_identity: str,
    ) -> tuple[str, ...]:
        before_text = before.astimezone(UTC).isoformat()
        rows = self.db.execute(
            "SELECT paper_trade_id FROM paper_trades "
            "WHERE status IN ('PENDING_ENTRY','OPEN') AND signal_time < ? "
            "ORDER BY paper_trade_id",
            (before_text,),
        ).fetchall()
        return tuple(
            self.exclude_trade_outcome(
                paper_trade_id=row["paper_trade_id"],
                phase2_epoch_id=phase2_epoch_id,
                reason_code="PRE_OPERATIONAL_FIX_NONTERMINAL",
                source_identity=source_identity,
                excluded_at=before,
            )
            for row in rows
        )

    def record_operational_deployment(
        self,
        *,
        deployment_id: str,
        phase2_epoch_id: str,
        base_manifest_hash: str,
        source_commit: str,
        database_schema_hash: str,
        deployed_at: datetime,
    ) -> None:
        body = {
            "deployment_id": deployment_id,
            "phase2_epoch_id": phase2_epoch_id,
            "base_manifest_hash": base_manifest_hash,
            "source_commit": source_commit,
            "database_schema_hash": database_schema_hash,
            "classification": "OPERATIONAL_ONLY",
            "deployed_at": deployed_at.astimezone(UTC).isoformat(),
        }
        integrity = sha256_canonical(body)
        self.db.execute(
            "INSERT INTO phase2_operational_deployments VALUES (?,?,?,?,?,?,?,?,?)",
            (
                deployment_id,
                phase2_epoch_id,
                base_manifest_hash,
                source_commit,
                database_schema_hash,
                "OPERATIONAL_ONLY",
                body["deployed_at"],
                canonical_json(body),
                integrity,
            ),
        )
        self.db.commit()

    def authorize_runtime_identity(
        self,
        *,
        manifest: Phase2Manifest,
        source_commit: str,
        database_schema_hash: str,
    ) -> bool:
        if (
            manifest.git_commit_hash == source_commit
            and manifest.database_schema_hash == database_schema_hash
        ):
            return True
        row = self.db.execute(
            "SELECT 1 FROM phase2_operational_deployments "
            "WHERE phase2_epoch_id=? AND base_manifest_hash=? AND source_commit=? "
            "AND database_schema_hash=? AND classification='OPERATIONAL_ONLY' "
            "ORDER BY deployed_at DESC LIMIT 1",
            (
                manifest.phase2_epoch_id,
                manifest.manifest_hash,
                source_commit,
                database_schema_hash,
            ),
        ).fetchone()
        return row is not None

    def set_evidence_window_start(
        self,
        *,
        window_id: str,
        phase2_epoch_id: str,
        first_eligible_boundary: datetime,
        deployment_id: str,
    ) -> None:
        boundary = first_eligible_boundary.astimezone(UTC)
        if boundary.minute % 15 or boundary.second or boundary.microsecond:
            raise ValueError("Phase 2 evidence window must start on a UTC quarter-hour")
        body = {
            "window_id": window_id,
            "phase2_epoch_id": phase2_epoch_id,
            "first_eligible_boundary": boundary.isoformat(),
            "deployment_id": deployment_id,
            "reason_code": "POST_OPERATIONAL_FIX_PROSPECTIVE_RESET",
        }
        self.db.execute(
            "INSERT INTO phase2_evidence_windows VALUES (?,?,?,?,?,?,?)",
            (
                window_id,
                phase2_epoch_id,
                boundary.isoformat(),
                deployment_id,
                body["reason_code"],
                canonical_json(body),
                sha256_canonical(body),
            ),
        )
        self.db.commit()

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
