#!/usr/bin/env python3
"""Read-only first-four-boundary evidence validator for Phase 2 epoch006.

This program intentionally has no imports from the research runtime and never
opens SQLite other than ``mode=ro`` with ``query_only=ON``.  It is intended to
be run once from a reviewed operations checkout (or streamed to the runtime
host) after the fourth scheduled boundary is terminal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EPOCH = "phase2_epoch_006"
UTC = timezone.utc
BOUNDARIES = tuple(f"2026-09-24T{minute}:00Z" for minute in ("00:15", "00:30", "00:45", "01:00"))
FIRST4_WINDOW_END = "2026-09-24T01:15:00Z"
EXPECTED = {
    "research_commit": "41e9e9acc261fd69d32c2d811e9ab4dd556c4620",
    "operations_commit": "e89a2f1eae58ec1f9a820de90d6e46713792c99f",
    "config_hash": "c69cf18d642aec9bd6bbaa2c85d3a571f45550be7c8a252221708af403a7f24c",
    "prompt_hash": "c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78",
    "output_schema_hash": "97318c27b3765780916efe010c3653fa8f8b097bdddd20ef711d40f41a5a1be4",
    "database_schema_hash": "62b5f58020cbaf19338fbfcf8e81c6b4a8f66cc67b635d2fe622e8f6d286586a",
    "model": "gpt-5.6-terra", "reasoning": "medium",
    "anchor": "2026-09-24T00:11:09.998160+00:00",
    "first_boundary": "2026-09-24T00:15:00+00:00",
    "window_end": "2026-11-05T00:15:00+00:00",
}
TERMINAL = {"COMPLETE", "REJECTED"}


def cmd(*args: str) -> str:
    return subprocess.run(args, check=False, capture_output=True, text=True).stdout.strip()


def parse_json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def ro(path: str) -> sqlite3.Connection:
    resolved = Path(path).resolve(strict=True)
    db = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def scalar(db: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> Any:
    row = db.execute(query, params).fetchone()
    return row[0] if row else None


def duplicates(db: sqlite3.Connection, table: str, fields: str) -> int:
    return int(scalar(db, f"SELECT COUNT(*) FROM (SELECT {fields},COUNT(*) n FROM {table} GROUP BY {fields} HAVING n>1)") or 0)


def lease(path: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_text().strip()
        value = parse_json(raw)
        pid = value.get("pid")
        alive = isinstance(pid, int) and os.path.exists(f"/proc/{pid}")
        return {"path": path, "metadata": value, "alive": alive}
    except OSError as error:
        return {"path": path, "error": str(error), "alive": False}


def service_properties(unit: str, properties: list[str]) -> dict[str, str]:
    result = cmd("systemctl", "show", unit, *sum((["-p", p] for p in properties), []), "--no-pager")
    return dict(line.split("=", 1) for line in result.splitlines() if "=" in line)


def event_log(path: str, start: str, end: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in Path(path).read_text().splitlines():
            row = parse_json(line)
            timestamp = row.get("timestamp")
            if row and isinstance(timestamp, str) and instant(timestamp) >= instant(start) and (
                end is None or instant(timestamp) <= instant(end)
            ):
                rows.append(row)
    except OSError:
        pass
    return rows


def decision(payload: str | None) -> str | None:
    body = parse_json(payload)
    return str(body.get("decision")) if body.get("decision") is not None else None


def row_decision(row: sqlite3.Row | None) -> str | None:
    return decision(row["payload_json"] if row is not None else None)


def fresh(snapshot: sqlite3.Row | None, boundary: str) -> str:
    if snapshot is None:
        return "N/A"
    return "FRESH" if iso(snapshot["snapshot_timestamp"]) == iso(boundary) else "STALE"


def cycle_rows_at_boundary(db: sqlite3.Connection, boundary: str) -> list[sqlite3.Row]:
    """Compare SQLite timestamps as instants, never as storage spellings."""
    candidates = db.execute(
        "SELECT * FROM research_cycles WHERE observation_class='SCORED_PROSPECTIVE'"
    ).fetchall()
    wanted = instant(boundary)
    return [row for row in candidates if instant(row["scheduled_at"]) == wanted]


def cycle_row(db: sqlite3.Connection, boundary: str, failures: list[str], telemetry: list[dict[str, Any]]) -> dict[str, Any]:
    rows = cycle_rows_at_boundary(db, boundary)
    row: sqlite3.Row | None = rows[0] if len(rows) == 1 else None
    result: dict[str, Any] = {"boundary": boundary, "cycle_status": None, "scoreable": None, "snapshot_freshness": "N/A", "regime": None, "quant_trend": None, "quant_mr": None, "detector": None, "llm_attempts": 0, "llm_status": None, "hybrid_trend": None, "hybrid_mr": None, "suppression_coeligibility": "N/A", "mullvad_per_call": "N/A", "integrity": "PASS", "lineage": {}}
    if len(rows) != 1:
        failures.append(f"boundary {boundary}: expected exactly one prospective research cycle, observed {len(rows)}")
        result["integrity"] = "CONCERN"; return result
    result["cycle_status"] = row["status"]
    details = parse_json(row["details_json"])
    result["scoreable"] = details.get("scoreable")
    result["rejection_reason"] = details.get("reason_code") or details.get("reason")
    result["lineage"]["cycle_id"] = row["cycle_id"]
    if row["status"] not in TERMINAL or not row["completed_at"]:
        failures.append(f"boundary {boundary}: non-terminal cycle {row['status']}")
    if row["status"] == "REJECTED" and not result["rejection_reason"]:
        failures.append(f"boundary {boundary}: rejected cycle lacks reason code")
    if row["status"] not in TERMINAL:
        result["integrity"] = "CONCERN"; return result
    if not row["snapshot_hash"]:
        if row["status"] == "COMPLETE":
            failures.append(f"boundary {boundary}: complete cycle has no snapshot")
            result["integrity"] = "CONCERN"
        return result
    snap = db.execute("SELECT * FROM decision_snapshots WHERE snapshot_hash=?", (row["snapshot_hash"],)).fetchone()
    if not snap:
        failures.append(f"boundary {boundary}: cycle references absent snapshot")
        result["integrity"] = "CONCERN"; return result
    result["lineage"].update({"snapshot_hash": snap["snapshot_hash"], "snapshot_id": snap["snapshot_id"], "snapshot_timestamp": snap["snapshot_timestamp"]})
    result["snapshot_freshness"] = fresh(snap, boundary)
    if snap["epoch_id"] != EPOCH or snap["observation_class"] != "SCORED_PROSPECTIVE" or result["snapshot_freshness"] != "FRESH":
        failures.append(f"boundary {boundary}: invalid snapshot epoch/class/freshness")
    result["scoreable"] = bool(snap["scoreable"])
    decisions = db.execute("SELECT * FROM strategy_decisions WHERE snapshot_hash=?", (snap["snapshot_hash"],)).fetchall()
    by_id = {r["strategy_id"]: r for r in decisions}
    result["quant_trend"] = row_decision(by_id.get("QUANT_TREND_V1"))
    result["quant_mr"] = row_decision(by_id.get("QUANT_MR_V1"))
    result["hybrid_trend"] = row_decision(by_id.get("HYBRID_TREND_LLM_V1"))
    result["hybrid_mr"] = row_decision(by_id.get("HYBRID_MR_LLM_V1"))
    detector_row = db.execute("SELECT * FROM detector_decisions WHERE snapshot_hash=?", (snap["snapshot_hash"],)).fetchone()
    result["detector"] = detector_row["trigger"] if detector_row else None
    attempts = db.execute("SELECT * FROM llm_invocation_attempts WHERE phase2_epoch_id=? AND input_snapshot_hash=? ORDER BY attempt", (EPOCH, snap["snapshot_hash"])).fetchall()
    result["llm_attempts"] = len(attempts)
    llm = db.execute("SELECT * FROM llm_decisions WHERE phase2_epoch_id=? AND input_snapshot_hash=?", (EPOCH, snap["snapshot_hash"])).fetchone()
    result["llm_status"] = llm["runner_status"] if llm else None
    if result["scoreable"] and (not llm or len(attempts) > 2):
        failures.append(f"boundary {boundary}: missing LLM decision or attempt contract exceeded")
    for attempt in attempts:
        payload = parse_json(attempt["payload_json"]); raw = attempt["raw_output_plaintext"]
        expected_hash = attempt["raw_output_hash"]
        raw_ok = raw is None or hashlib.sha256(raw.encode()).hexdigest() == expected_hash
        if not raw_ok: failures.append(f"attempt {attempt['attempt_id']}: raw response SHA-256 mismatch")
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        model = payload.get("model"); reasoning = metadata.get("reasoning_effort")
        # The persisted attempt schema does not presently record request reasoning
        # effort.  Treat absent per-call evidence as ambiguous rather than inferring
        # it from the frozen manifest.
        if model != EXPECTED["model"] or reasoning != EXPECTED["reasoning"] or attempt["tool_calls_count"] != 0:
            failures.append(f"attempt {attempt['attempt_id']}: provider model/reasoning/tool-call invariant failed")
        telemetry.append({"attempt_id": attempt["attempt_id"], "started_at": attempt["started_at"], "ended_at": attempt["ended_at"], "raw_hash_ok": raw_ok, "model": model, "reasoning": reasoning, "tool_calls_count": attempt["tool_calls_count"]})
    # A position-open suppression must not also have a fresh trade/order for that decision.
    suppressed = db.execute("SELECT strategy_decision_id,status FROM paper_trades WHERE snapshot_hash=? AND status LIKE 'SUPPRESSED_%'", (snap["snapshot_hash"],)).fetchall()
    conflicts = 0
    for trade in suppressed:
        conflicts += int(scalar(db, "SELECT COUNT(*) FROM paper_orders WHERE strategy_decision_id=?", (trade["strategy_decision_id"],)) or 0)
    pairs = db.execute("SELECT * FROM phase2_pair_outcomes WHERE input_snapshot_hash=?", (snap["snapshot_hash"],)).fetchall()
    result["suppression_coeligibility"] = "PASS" if not conflicts else "CONCERN"
    result["lineage"]["pair_integrity_hashes"] = [r["integrity_hash"] for r in pairs]
    if conflicts:
        failures.append(f"boundary {boundary}: suppressed strategy has fresh paper order")
    result["mullvad_per_call"] = "PENDING_LOG_MATCH" if attempts else "N/A"
    result["lineage"]["attempt_integrity_hashes"] = [a["integrity_hash"] for a in attempts]
    if any(not f.startswith(f"boundary {boundary}") for f in []): pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--database", default="/var/lib/hypebot/phase2/phase2_epoch_006.sqlite3"); parser.add_argument("--research-repo", default="/opt/hypebot/phase2"); parser.add_argument("--operations-repo", default="/opt/hypebot/operations"); parser.add_argument("--event-log", default="/var/log/hypebot/phase2-supervisor.jsonl"); parser.add_argument("--network-log", default="/var/log/hypebot/phase2-runtime-network.jsonl"); parser.add_argument("--worker-lease", default="/run/lock/hypebot/phase2.writer.lock"); parser.add_argument("--supervisor-lease", default="/run/lock/hypebot/phase2.supervisor.lock"); args = parser.parse_args()
    failures: list[str] = []; attempts_telemetry: list[dict[str, Any]] = []
    db = ro(args.database)
    try:
        manifest_rows = db.execute("SELECT * FROM phase2_manifests WHERE phase2_epoch_id=?", (EPOCH,)).fetchall()
        manifest = manifest_rows[0] if len(manifest_rows) == 1 else None
        if not manifest: failures.append(f"activation manifest count is {len(manifest_rows)}, expected 1")
        observed_identity = {"research_commit": cmd("git", "-C", args.research_repo, "rev-parse", "HEAD"), "operations_commit": cmd("git", "-C", args.operations_repo, "rev-parse", "HEAD")}
        if manifest:
            observed_identity.update({"config_hash": manifest["config_hash"], "prompt_hash": manifest["prompt_hash"], "output_schema_hash": manifest["output_schema_hash"], "database_schema_hash": manifest["database_schema_hash"]})
            frozen = parse_json(manifest["payload_json"]).get("frozen_contract", {})
            observed_identity.update({"model": frozen.get("model"), "reasoning": frozen.get("reasoning_effort")})
        identity_mismatches = {k: {"expected": v, "observed": observed_identity.get(k)} for k, v in EXPECTED.items() if k in observed_identity and observed_identity.get(k) != v}
        if identity_mismatches: failures.append("PRIMARY runtime identity drift: " + json.dumps(identity_mismatches, sort_keys=True))
        integrity = {"pragma_integrity_check": scalar(db, "PRAGMA integrity_check"), "foreign_key_violations": len(db.execute("PRAGMA foreign_key_check").fetchall()), "duplicates": {"research_cycles": duplicates(db, "research_cycles", "scheduled_at,observation_class"), "decision_snapshots": duplicates(db, "decision_snapshots", "epoch_id,snapshot_timestamp,observation_class"), "llm_attempts": duplicates(db, "llm_invocation_attempts", "experiment_id,phase2_epoch_id,input_snapshot_hash,attempt"), "strategy_decisions": duplicates(db, "strategy_decisions", "snapshot_hash,strategy_id,strategy_version"), "paper_orders": duplicates(db, "paper_orders", "strategy_decision_id"), "paper_fills": duplicates(db, "paper_fills", "paper_trade_id,fill_type,fill_time")}, "open_collection_gaps": scalar(db, "SELECT COUNT(*) FROM collection_gaps WHERE status='OPEN'")}
        if integrity["pragma_integrity_check"] != "ok" or integrity["foreign_key_violations"] or any(integrity["duplicates"].values()) or integrity["open_collection_gaps"]:
            failures.append("database integrity/duplicate/gap invariant failed")
        anchor_rows = db.execute("SELECT * FROM phase2_recovery_events WHERE phase2_epoch_id=? AND event_type='PROSPECTIVE_START_ESTABLISHED'", (EPOCH,)).fetchall(); windows = db.execute("SELECT * FROM phase2_evidence_windows WHERE phase2_epoch_id=?", (EPOCH,)).fetchall()
        clock = {"anchor_count": len(anchor_rows), "window_count": len(windows), "anchor": anchor_rows[0]["occurred_at"] if len(anchor_rows)==1 else None, "first_boundary": windows[0]["first_eligible_boundary"] if len(windows)==1 else None}
        if clock["anchor_count"] != 1 or clock["window_count"] != 1 or iso(str(clock["anchor"])) != EXPECTED["anchor"] or iso(str(clock["first_boundary"])) != EXPECTED["first_boundary"]:
            failures.append("evidence clock anchor/window invariant failed")
        prospective_snapshots = db.execute("SELECT * FROM decision_snapshots WHERE epoch_id=? AND observation_class='SCORED_PROSPECTIVE'", (EPOCH,)).fetchall()
        pre = [row for row in prospective_snapshots if instant(row["snapshot_timestamp"]) < instant(EXPECTED["first_boundary"])]
        if pre: failures.append(f"pre-boundary scored prospective snapshots observed: {len(pre)}")
        rows = [cycle_row(db, boundary, failures, attempts_telemetry) for boundary in BOUNDARIES]
    finally: db.close()
    service = service_properties("hypebot-phase2.service", ["ActiveState", "UnitFileState", "MainPID", "NRestarts"]); timer = service_properties("hypebot-phase2-health.timer", ["ActiveState", "UnitFileState", "LastTriggerUSec", "NextElapseUSecMonotonic"])
    worker, supervisor = lease(args.worker_lease), lease(args.supervisor_lease)
    runtime = {"service": service, "worker_lease": worker, "supervisor_lease": supervisor}
    if service.get("ActiveState") != "active" or service.get("UnitFileState") != "enabled" or service.get("NRestarts") != "0" or not worker["alive"] or not supervisor["alive"] or worker.get("metadata", {}).get("epoch_id") != EPOCH or supervisor.get("metadata", {}).get("epoch_id") != EPOCH:
        failures.append("runtime continuity/lease invariant failed")
    events = event_log(args.event_log, "2026-09-24T00:11:07Z")
    starts = [x for x in events if x.get("event") == "WORKER_STARTED"]
    if len(starts) != 1: failures.append(f"supervisor event log worker relaunch count is {len(starts)}, expected 1")
    # The initially observed PIDs remain required when no preserved relaunch exists.
    if len(starts) == 1 and (service.get("MainPID") != "209333" or worker.get("metadata", {}).get("pid") != 209458):
        failures.append("runtime PID changed without an evidence-preserved worker relaunch")
    process_lines = cmd("ps", "-eo", "pid=,ppid=,args=").splitlines()
    expected_worker_pid = worker.get("metadata", {}).get("pid")
    worker_processes = [line for line in process_lines if line.split(maxsplit=1) and str(expected_worker_pid) == line.split(maxsplit=1)[0] and "phase2_epoch006_runtime_worker.py" in line]
    if len(worker_processes) != 1:
        failures.append(f"competing writer check observed {len(worker_processes)} epoch006 worker processes")
    health_journal = cmd("journalctl", "-u", "hypebot-phase2-health.service", "--since", "2026-09-24 00:11:07 UTC", "--until", "2026-09-24 01:15:00 UTC", "--no-pager", "-o", "json")
    health_records = [parse_json(line) for line in health_journal.splitlines() if line.strip()]
    health_successes = sum(1 for record in health_records if "Finished hypebot-phase2-health.service" in str(record.get("MESSAGE", "")))
    health_failures = sum(1 for record in health_records if "Main process exited" in str(record.get("MESSAGE", "")) and "status=1/FAILURE" in str(record.get("MESSAGE", "")))
    if timer.get("ActiveState") != "active" or timer.get("UnitFileState") != "enabled" or not timer.get("LastTriggerUSec") or timer.get("NextElapseUSecMonotonic", "").lower() in {"", "0", "infinity"} or health_successes < 2 or health_failures:
        failures.append("health timer invariant failed")
    network = event_log(args.network_log, "2026-09-24T00:15:00Z")
    actual_attempts = attempts_telemetry
    guard_pass = 0
    for attempt in actual_attempts:
        attempt_start = instant(attempt["started_at"])
        linked = [entry for entry in network if entry.get("component") == "phase2_epoch006_runtime_network_guard" and instant(str(entry["timestamp"])) <= attempt_start and (attempt_start - instant(str(entry["timestamp"]))).total_seconds() <= 60]
        if any(entry.get("status") == "BLOCKED" for entry in linked):
            failures.append(f"attempt {attempt['attempt_id']}: per-call Mullvad guard recorded BLOCKED")
        passed = [entry for entry in linked if entry.get("status") == "PASS" and entry.get("interface") == "wg0-mullvad" and entry.get("relay_country") == "sg"]
        if len(passed) != 1:
            failures.append(f"attempt {attempt['attempt_id']}: missing or ambiguous per-call Mullvad PASS evidence")
        else:
            guard_pass += 1
    status = "PHASE_2_EPOCH_006_FIRST4_CONCERN" if failures else "PHASE_2_EPOCH_006_FIRST4_VALIDATED"
    print(json.dumps({"status": status, "validated_at": datetime.now(UTC).isoformat(), "identity": {"expected": {k:v for k,v in EXPECTED.items() if k in observed_identity}, "observed": observed_identity, "mismatches": identity_mismatches}, "runtime": {**runtime, "worker_processes": worker_processes}, "timer": {**timer, "health_successes": health_successes, "health_failures": health_failures}, "database": integrity, "evidence_clock": clock, "boundaries": rows, "provider": {"attempts": attempts_telemetry, "raw_response_hash_verification": "recomputed for every stored plaintext response", "information_boundary": "tool_calls_count == 0 and the recorded invocation metadata demonstrate absence of the currently instrumented external-tool leakage mechanism; this is not an exhaustive proof against every hypothetical leakage mechanism."}, "supervisor_event_starts": len(starts), "mullvad": {"pass_records": guard_pass, "actual_invocations": len(actual_attempts)}, "failures": failures}, sort_keys=True, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
