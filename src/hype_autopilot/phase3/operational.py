from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hype_autopilot.clock import ensure_utc, next_quarter_hour
from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.storage import (
    FRESH_START_ONLY_EPOCHS,
    INITIAL_EVIDENCE_WINDOW_RULE,
    OPERATIONAL_RESET_RULE,
)

PAIR_STRATEGIES = {
    "LLM_V1__vs__QUANT_TREND_V1": ("LLM_V1", "QUANT_TREND"),
    "LLM_V1__vs__QUANT_MR_V1": ("LLM_V1", "QUANT_MR"),
    "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1": (
        "HYBRID_TREND_LLM_V1",
        "QUANT_TREND",
    ),
    "HYBRID_MR_LLM_V1__vs__QUANT_MR_V1": (
        "HYBRID_MR_LLM_V1",
        "QUANT_MR",
    ),
}


def _read_only_connection(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _missing_boundaries(
    values: list[datetime], *, evidence_start: datetime, observation_cutoff: datetime
) -> list[str]:
    observed = {ensure_utc(value) for value in values}
    missing: list[str] = []
    cursor = ensure_utc(evidence_start)
    cutoff = ensure_utc(observation_cutoff)
    while cursor <= cutoff:
        if cursor not in observed:
            missing.append(cursor.isoformat())
        cursor += timedelta(minutes=15)
    return missing


def _startup_boundary_acceptance(
    cycles: list[sqlite3.Row], *, evidence_start: datetime
) -> dict[str, Any]:
    required = [evidence_start + timedelta(minutes=15 * index) for index in range(4)]
    rows = {ensure_utc(datetime.fromisoformat(row["scheduled_at"])): row for row in cycles}
    results: list[dict[str, Any]] = []
    accepted = True
    for boundary in required:
        row = rows.get(boundary)
        details: dict[str, Any] = {}
        if row is not None:
            try:
                details = json.loads(row["details_json"])
            except (TypeError, json.JSONDecodeError):
                details = {}
        passed = bool(
            row is not None
            and row["status"] == "COMPLETE"
            and details.get("scoreable") is True
        )
        accepted = accepted and passed
        results.append(
            {
                "scheduled_at": boundary.isoformat(),
                "status": row["status"] if row is not None else "ABSENT",
                "scoreable": details.get("scoreable") if row is not None else None,
                "passed": passed,
            }
        )
    return {
        "accepted": accepted,
        "required_boundaries": [value.isoformat() for value in required],
        "results": results,
    }


def _supervisor_relaunches(path: str | Path | None, activation: datetime) -> int:
    if path is None or not Path(path).is_file():
        return 0
    starts = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            timestamp = datetime.fromisoformat(row["timestamp"]).astimezone(UTC)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if timestamp >= activation and row.get("event") == "SCHEDULER_PROCESS_START":
            starts += 1
    return max(0, starts - 1)


def _validated_evidence_window(
    db: sqlite3.Connection, *, phase2_epoch_id: str
) -> tuple[datetime, bool]:
    """Independently verify the immutable Phase 2 evidence-clock anchor."""
    fields = (
        "window_id",
        "phase2_epoch_id",
        "first_eligible_boundary",
        "deployment_id",
        "prospective_start_integrity_hash",
        "established_at",
        "rule_version",
        "reason_code",
    )
    rows = db.execute(
        f"SELECT {','.join(fields)},payload_json,integrity_hash "
        "FROM phase2_evidence_windows WHERE phase2_epoch_id=?",
        (phase2_epoch_id,),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("exactly one immutable Phase 2 evidence window is required")
    row = rows[0]
    try:
        payload = json.loads(row["payload_json"])
        boundary = datetime.fromisoformat(row["first_eligible_boundary"]).astimezone(UTC)
        established_at = datetime.fromisoformat(row["established_at"]).astimezone(UTC)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Phase 2 evidence-window serialization is invalid") from error
    expected_payload = {field: row[field] for field in fields}
    if (
        payload != expected_payload
        or sha256_canonical(payload) != row["integrity_hash"]
        or row["phase2_epoch_id"] != phase2_epoch_id
    ):
        raise ValueError("Phase 2 evidence-window integrity validation failed")

    identity = (row["reason_code"], row["rule_version"])
    if identity == ("INITIAL_PROSPECTIVE_START", INITIAL_EVIDENCE_WINDOW_RULE):
        if row["deployment_id"] is not None or not row["prospective_start_integrity_hash"]:
            raise ValueError("fresh-start evidence-window identity is invalid")
        anchors = db.execute(
            "SELECT phase2_epoch_id,event_type,source_identity,occurred_at,"
            "payload_json,integrity_hash FROM phase2_recovery_events "
            "WHERE integrity_hash=?",
            (row["prospective_start_integrity_hash"],),
        ).fetchall()
        if len(anchors) != 1:
            raise ValueError("prospective-start anchor is missing or ambiguous")
        anchor = anchors[0]
        try:
            anchor_payload = json.loads(anchor["payload_json"])
            anchor_time = datetime.fromisoformat(anchor["occurred_at"]).astimezone(UTC)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("prospective-start anchor serialization is invalid") from error
        expected_anchor = {
            "phase2_epoch_id": phase2_epoch_id,
            "event_type": "PROSPECTIVE_START_ESTABLISHED",
            "source_identity": phase2_epoch_id,
            "occurred_at": anchor["occurred_at"],
            "details": {"prospective_start": anchor["occurred_at"]},
        }
        if (
            anchor_payload != expected_anchor
            or sha256_canonical(anchor_payload) != anchor["integrity_hash"]
            or anchor["integrity_hash"] != row["prospective_start_integrity_hash"]
            or anchor["phase2_epoch_id"] != phase2_epoch_id
            or anchor["event_type"] != "PROSPECTIVE_START_ESTABLISHED"
            or anchor["source_identity"] != phase2_epoch_id
            or established_at != anchor_time
        ):
            raise ValueError("prospective-start anchor integrity validation failed")
        calculated = next_quarter_hour(anchor_time)
        if calculated <= anchor_time:
            calculated += timedelta(minutes=15)
        if boundary != calculated:
            raise ValueError("evidence-window boundary differs from prospective start")
        return boundary, False

    if identity == ("POST_OPERATIONAL_FIX_PROSPECTIVE_RESET", OPERATIONAL_RESET_RULE):
        if phase2_epoch_id in FRESH_START_ONLY_EPOCHS:
            raise ValueError(
                "fresh-start-only epoch cannot use an operational evidence-window reset"
            )
        if row["prospective_start_integrity_hash"] is not None or not row["deployment_id"]:
            raise ValueError("operational-reset evidence-window identity is invalid")
        deployment = db.execute(
            "SELECT deployment_id,phase2_epoch_id,base_manifest_hash,source_commit,"
            "database_schema_hash,classification,deployed_at,payload_json,integrity_hash "
            "FROM phase2_operational_deployments WHERE deployment_id=?",
            (row["deployment_id"],),
        ).fetchone()
        if deployment is None:
            raise ValueError("operational-reset deployment identity is invalid")
        deployment_payload = json.loads(deployment["payload_json"])
        expected_deployment = {
            field: deployment[field]
            for field in (
                "deployment_id",
                "phase2_epoch_id",
                "base_manifest_hash",
                "source_commit",
                "database_schema_hash",
                "classification",
                "deployed_at",
            )
        }
        if (
            deployment_payload != expected_deployment
            or sha256_canonical(deployment_payload) != deployment["integrity_hash"]
            or deployment["phase2_epoch_id"] != phase2_epoch_id
            or deployment["classification"] != "OPERATIONAL_ONLY"
            or deployment["deployed_at"] != row["established_at"]
        ):
            raise ValueError("operational-reset deployment identity is invalid")
        return boundary, True

    raise ValueError("unsupported evidence-window reason/rule combination")


def _coeligibility_by_comparison(
    db: sqlite3.Connection,
    *,
    evidence_start: datetime,
) -> dict[str, dict[str, Any]]:
    snapshots = [
        datetime.fromisoformat(row["snapshot_timestamp"]).astimezone(UTC)
        for row in db.execute(
            "SELECT snapshot_timestamp FROM decision_snapshots "
            "WHERE snapshot_timestamp>=? ORDER BY snapshot_timestamp",
            (evidence_start.isoformat(),),
        )
    ]
    intervals: dict[str, list[tuple[datetime, datetime | None]]] = defaultdict(list)
    terminal = {
        "CLOSED",
        "SUPPRESSED_INVALID_TARGET_AFTER_LATENCY",
        "SUPPRESSED_NO_ENTRY_DATA",
    }
    excluded = (
        {
            row[0]
            for row in db.execute(
                "SELECT paper_trade_id FROM phase2_outcome_exclusions"
            )
        }
        if _table_exists(db, "phase2_outcome_exclusions")
        else set()
    )
    for row in db.execute(
        "SELECT paper_trade_id,strategy_id, signal_time, exit_time, last_processed_at, status "
        "FROM paper_trades ORDER BY signal_time"
    ):
        if (
            row["paper_trade_id"] in excluded
            or row["status"] == "SUPPRESSED_POSITION_OPEN"
        ):
            continue
        start = datetime.fromisoformat(row["signal_time"]).astimezone(UTC)
        if start < evidence_start:
            continue
        end_text = row["exit_time"]
        if row["status"] in terminal and end_text is None:
            end_text = row["last_processed_at"]
        end = datetime.fromisoformat(end_text).astimezone(UTC) if end_text else None
        intervals[row["strategy_id"]].append((start, end))

    def flat(strategy: str, at: datetime) -> bool:
        return not any(
            start < at and (end is None or end > at)
            for start, end in intervals.get(strategy, ())
        )

    result: dict[str, dict[str, Any]] = {}
    for pair_id, (treatment, control) in PAIR_STRATEGIES.items():
        grouped: dict[str, list[bool]] = defaultdict(list)
        for at in snapshots:
            grouped[at.date().isoformat()].append(
                flat(treatment, at) and flat(control, at)
            )
        result[pair_id] = {
            "daily": [
                {
                    "day": day,
                    "boundaries": len(values),
                    "coeligible": sum(values),
                    "rate": sum(values) / len(values),
                }
                for day, values in sorted(grouped.items())
            ]
        }
    return result


def collect_operational_telemetry(
    database_path: str | Path,
    *,
    supervisor_event_log: str | Path | None = None,
    observation_cutoff: datetime | None = None,
) -> dict[str, Any]:
    """Read operational metadata only; never load returns, PnL, or trade outcomes."""
    db = _read_only_connection(database_path)
    try:
        manifest_row = db.execute(
            "SELECT activation_timestamp, payload_json FROM phase2_manifests "
            "ORDER BY activation_timestamp DESC LIMIT 1"
        ).fetchone()
        if manifest_row is None:
            raise ValueError("Phase 2 manifest is required for operational telemetry")
        manifest = json.loads(manifest_row["payload_json"])
        activation = datetime.fromisoformat(
            manifest_row["activation_timestamp"]
        ).astimezone(UTC)
        if not _table_exists(db, "phase2_evidence_windows"):
            raise ValueError("immutable Phase 2 evidence window is required")
        evidence_start, evidence_clock_reset_applied = _validated_evidence_window(
            db, phase2_epoch_id=manifest["phase2_epoch_id"]
        )
        cutoff = ensure_utc(observation_cutoff or datetime.now(UTC))
        frozen = manifest["frozen_contract"]
        cycles = db.execute(
            "SELECT scheduled_at,status,details_json FROM research_cycles "
            "WHERE scheduled_at>=? AND scheduled_at<=? ORDER BY scheduled_at",
            (evidence_start.isoformat(), cutoff.isoformat()),
        ).fetchall()
        boundaries = [
            datetime.fromisoformat(row["scheduled_at"]).astimezone(UTC)
            for row in cycles
        ]
        attempt_rows = db.execute(
            "SELECT attempt, provider_status, error_code FROM llm_invocation_attempts"
        ).fetchall()
        retry_reasons = Counter(
            (row["error_code"] or row["provider_status"])
            for row in attempt_rows
            if row["provider_status"] != "VALID"
        )
        costs_by_day: dict[str, float] = defaultdict(float)
        identities: Counter[tuple[str, str]] = Counter()
        for row in db.execute("SELECT timestamp, payload_json FROM llm_decisions"):
            payload = json.loads(row["payload_json"])
            day = (
                datetime.fromisoformat(row["timestamp"])
                .astimezone(UTC)
                .date()
                .isoformat()
            )
            costs_by_day[day] += float(payload.get("model_cost_usd", 0.0))
            identities[
                (str(payload.get("model")), str(payload.get("model_version")))
            ] += 1
        expected_identity = (str(frozen["model"]), str(frozen["model_version"]))
        coeligible = _coeligibility_by_comparison(db, evidence_start=evidence_start)
        gaps = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN recovered_at IS NULL THEN 1 ELSE 0 END) AS open "
            "FROM collection_gaps"
        ).fetchone()
        duplicate_queries = {
            "cycles": "SELECT COUNT(*) FROM (SELECT scheduled_at, COUNT(*) n FROM research_cycles GROUP BY scheduled_at HAVING n>1)",
            "snapshots": "SELECT COUNT(*) FROM (SELECT snapshot_timestamp, COUNT(*) n FROM decision_snapshots GROUP BY snapshot_timestamp HAVING n>1)",
            "strategies": "SELECT COUNT(*) FROM (SELECT snapshot_hash,strategy_version,COUNT(*) n FROM strategy_decisions GROUP BY snapshot_hash,strategy_version HAVING n>1)",
            "llm": "SELECT COUNT(*) FROM (SELECT input_snapshot_hash,strategy_version,COUNT(*) n FROM llm_decisions GROUP BY input_snapshot_hash,strategy_version HAVING n>1)",
            "attempts": "SELECT COUNT(*) FROM (SELECT input_snapshot_hash,attempt,COUNT(*) n FROM llm_invocation_attempts GROUP BY input_snapshot_hash,attempt HAVING n>1)",
        }
        downtime_boundaries = []
        for row in cycles:
            try:
                details = json.loads(row["details_json"])
            except (TypeError, json.JSONDecodeError):
                details = {}
            if row["status"] == "REJECTED" and details.get("reason") == "PROCESS_DOWNTIME":
                downtime_boundaries.append(row["scheduled_at"])
        return {
            "telemetry_scope": "OPERATIONAL_ONLY_NO_PERFORMANCE_FIELDS",
            "activation_timestamp": activation.isoformat(),
            "research_activation_timestamp": evidence_start.isoformat(),
            "effective_evidence_start": evidence_start.isoformat(),
            "phase3_calendar_floor_anchor": evidence_start.isoformat(),
            "evidence_clock_reset_applied": evidence_clock_reset_applied,
            "observation_cutoff": cutoff.isoformat(),
            "latest_boundary": max(boundaries).isoformat() if boundaries else None,
            "cycle_status_counts": dict(Counter(row["status"] for row in cycles)),
            "missing_boundaries": _missing_boundaries(
                boundaries,
                evidence_start=evidence_start,
                observation_cutoff=cutoff,
            ),
            "rejected_process_downtime_boundaries": downtime_boundaries,
            "first_four_boundary_acceptance": _startup_boundary_acceptance(
                cycles, evidence_start=evidence_start
            ),
            "collection_gaps": {"total": gaps["total"], "open": gaps["open"] or 0},
            "duplicates": {
                key: db.execute(query).fetchone()[0]
                for key, query in duplicate_queries.items()
            },
            "database_integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(
                db.execute("PRAGMA foreign_key_check").fetchall()
            ),
            "api_cost_usd_by_utc_day": dict(sorted(costs_by_day.items())),
            "api_budget_usd_per_day": float(
                frozen["resource_isolation"]["api_budget_usd_per_day"]
            ),
            "validation_retry_count": sum(row["attempt"] > 1 for row in attempt_rows),
            "validation_failure_reasons": dict(sorted(retry_reasons.items())),
            "provider_model_identities": {
                f"{model}/{version}": count
                for (model, version), count in sorted(identities.items())
            },
            "provider_model_identity_drift": any(
                identity != expected_identity for identity in identities
            ),
            "coeligibility_rate_by_comparison": coeligible,
            "coeligibility_basis": "READ_ONLY_POSITION_INTERVAL_RECONSTRUCTION",
            "supervisor_relaunch_count": _supervisor_relaunches(
                supervisor_event_log, activation
            ),
        }
    finally:
        db.close()
