from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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


def _missing_boundaries(values: list[datetime]) -> list[str]:
    if len(values) < 2:
        return []
    observed = set(values)
    missing: list[str] = []
    cursor = min(values)
    end = max(values)
    while cursor <= end:
        if cursor not in observed:
            missing.append(cursor.isoformat())
        cursor += timedelta(minutes=15)
    return missing


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
        evidence_start = activation
        if _table_exists(db, "phase2_evidence_windows"):
            row = db.execute(
                "SELECT first_eligible_boundary FROM phase2_evidence_windows "
                "ORDER BY first_eligible_boundary DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                evidence_start = datetime.fromisoformat(
                    row["first_eligible_boundary"]
                ).astimezone(UTC)
        frozen = manifest["frozen_contract"]
        cycles = db.execute(
            "SELECT scheduled_at, status FROM research_cycles "
            "WHERE scheduled_at>=? ORDER BY scheduled_at",
            (evidence_start.isoformat(),),
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
        return {
            "telemetry_scope": "OPERATIONAL_ONLY_NO_PERFORMANCE_FIELDS",
            "activation_timestamp": activation.isoformat(),
            "research_activation_timestamp": activation.isoformat(),
            "effective_evidence_start": evidence_start.isoformat(),
            "phase3_calendar_floor_anchor": evidence_start.isoformat(),
            "evidence_clock_reset_applied": evidence_start != activation,
            "latest_boundary": max(boundaries).isoformat() if boundaries else None,
            "cycle_status_counts": dict(Counter(row["status"] for row in cycles)),
            "missing_boundaries": _missing_boundaries(boundaries),
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
