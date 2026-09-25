from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import yaml

from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.storage import INITIAL_EVIDENCE_WINDOW_RULE
from hype_autopilot.phase3.gate import GateEvidence, PairEvidence, load_analysis_gate

EPOCH_ID = "phase2_epoch_006"
RESEARCH_SHA = "41e9e9acc261fd69d32c2d811e9ab4dd556c4620"
OPERATIONS_SHA = "e89a2f1eae58ec1f9a820de90d6e46713792c99f"
CONFIG_SHA = "c69cf18d642aec9bd6bbaa2c85d3a571f45550be7c8a252221708af403a7f24c"
PROMPT_SHA = "c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78"
OUTPUT_SCHEMA_SHA = "97318c27b3765780916efe010c3653fa8f8b097bdddd20ef711d40f41a5a1be4"
DATABASE_SCHEMA_SHA = "62b5f58020cbaf19338fbfcf8e81c6b4a8f66cc67b635d2fe622e8f6d286586a"
V2_SHA = "d5387568a639af105165a8e00705edef1509145706dce58519d2e56b1ff3e629"
V2_APPROVED_COMMIT = "fb16b9a5aa05671d098a6baf5e9fc3bfad8d8798"
ANCHOR = datetime(2026, 9, 24, 0, 11, 9, 998160, tzinfo=UTC)
FIRST_BOUNDARY = datetime(2026, 9, 24, 0, 15, tzinfo=UTC)
FORMAL_CUTOFF = datetime(2026, 11, 5, 0, 15, tzinfo=UTC)
QUANTUM = Decimal("0.0000000001")

EXPECTED_STRATEGIES: dict[str, str] = {
    "QUANT_TREND": "QUANT_TREND_V1",
    "QUANT_MR": "QUANT_MR_V1",
    "LLM_V1": "LLM_V1",
    "HYBRID_TREND_LLM_V1": "HYBRID_TREND_LLM_V1",
    "HYBRID_MR_LLM_V1": "HYBRID_MR_LLM_V1",
}
LLM_DEPENDENT = frozenset({"LLM_V1", "HYBRID_TREND_LLM_V1", "HYBRID_MR_LLM_V1"})
TERMINAL_SUPPRESSED = frozenset(
    {
        "SUPPRESSED_POSITION_OPEN",
        "SUPPRESSED_INVALID_TARGET_AFTER_LATENCY",
        "SUPPRESSED_NO_ENTRY_DATA",
    }
)
VALID_REGIMES = frozenset(
    f"{trend}_{volatility}"
    for trend in ("UP", "DOWN", "RANGE")
    for volatility in ("LOW", "NORMAL", "HIGH")
)


@dataclass(frozen=True)
class AdapterPaths:
    root: Path
    specification: Path
    canonical_hash_record: Path
    authorization: Path
    gate_config: Path
    evaluator: Path

    @classmethod
    def from_root(cls, root: str | Path) -> AdapterPaths:
        resolved = Path(root).resolve()
        return cls(
            root=resolved,
            specification=resolved / "config/phase3/epoch006_derivation_spec_v2.yaml",
            canonical_hash_record=resolved
            / "config/phase3/epoch006_derivation_spec_v2.canonical.sha256",
            authorization=resolved
            / "config/phase3/epoch006_derivation_spec_v2_freeze_authorization.yaml",
            gate_config=resolved / "config/phase3/analysis_gate_v1.yaml",
            evaluator=resolved / "src/hype_autopilot/phase3/gate.py",
        )


@dataclass(frozen=True)
class FrozenV2Binding:
    paths: AdapterPaths
    pairs: dict[str, tuple[str, str]]


def _parse_utc(value: object, *, field: str) -> datetime:
    try:
        parsed = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} is not a valid timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: object, *, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field} is missing or not numeric")
    try:
        numeric = float(value)
        result = Decimal(str(numeric))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} is not numeric") from error
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be finite")
    return result


def _quantize(value: Decimal) -> Decimal:
    result = value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    return abs(result) if result == 0 else result


def canonical_decimal(value: object, *, field: str = "value") -> str:
    """Return the frozen V2 fixed-10-place representation."""
    return format(_quantize(_decimal(value, field=field)), ".10f")


def _read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path.name} must contain a mapping")
    return value


def load_frozen_v2_binding(root: str | Path) -> FrozenV2Binding:
    paths = AdapterPaths.from_root(root)
    spec = _read_yaml(paths.specification)
    authorization = _read_yaml(paths.authorization)
    actual_spec_sha = sha256_canonical(spec)
    if actual_spec_sha != V2_SHA or authorization.get("canonical_sha256") != V2_SHA:
        raise ValueError("frozen V2 canonical identity mismatch")
    if not paths.canonical_hash_record.read_text(encoding="utf-8").startswith(
        f"{V2_SHA}  epoch006_derivation_spec_v2.yaml "
    ):
        raise ValueError("frozen V2 canonical hash record mismatch")
    if (
        authorization.get("approved_commit") != V2_APPROVED_COMMIT
        or authorization.get("canonical_spec_path")
        != "config/phase3/epoch006_derivation_spec_v2.yaml"
    ):
        raise ValueError("frozen V2 approval identity mismatch")
    if (
        authorization.get("specification_version")
        != "PHASE3_EPOCH006_DERIVATION_SPEC_V2"
    ):
        raise ValueError("frozen V2 specification version mismatch")
    if (
        authorization.get("reviewer_disposition")
        != "SECONDARY_V2_REVIEW_APPROVED_WITH_MINOR_NONBLOCKING_NOTES"
    ):
        raise ValueError("frozen V2 independent approval is absent")
    implementation = authorization.get("implementation_gate", {})
    if (
        implementation.get("status") != "AUTHORIZED_ONLY_AGAINST_FROZEN_V2"
        or implementation.get("mismatch_behavior") != "FAIL_CLOSED"
    ):
        raise ValueError("frozen V2 implementation authorization is invalid")
    if (
        implementation.get("evaluation_authorized") is not False
        or implementation.get("live_runtime_change_authorized") is not False
    ):
        raise ValueError(
            "freeze authorization improperly permits evaluation or runtime changes"
        )
    if authorization.get("no_outcome_access", {}).get("attested") is not True:
        raise ValueError("frozen V2 outcome-access attestation is absent")

    window = spec.get("evidence_window", {})
    if window.get("epoch_id") != EPOCH_ID:
        raise ValueError("frozen V2 epoch identity mismatch")
    if (
        _parse_utc(window.get("prospective_anchor"), field="V2 prospective anchor")
        != ANCHOR
    ):
        raise ValueError("frozen V2 prospective anchor mismatch")
    if (
        _parse_utc(window.get("first_eligible_boundary"), field="V2 evidence start")
        != FIRST_BOUNDARY
    ):
        raise ValueError("frozen V2 evidence start mismatch")
    if (
        _parse_utc(window.get("formal_cutoff"), field="V2 formal cutoff")
        != FORMAL_CUTOFF
    ):
        raise ValueError("frozen V2 formal cutoff mismatch")

    dependencies = authorization.get("frozen_dependencies", {})
    if _file_sha256(paths.evaluator) != dependencies.get("evaluator_sha256"):
        raise ValueError("frozen evaluator SHA mismatch")
    if _file_sha256(paths.gate_config) != dependencies.get("gate_config_sha256"):
        raise ValueError("frozen gate config SHA mismatch")
    gate = load_analysis_gate(paths.gate_config)
    if (
        gate.phase2_epoch_id != EPOCH_ID
        or gate.activation_timestamp != FIRST_BOUNDARY
        or gate.earliest_formal_checkpoint != FORMAL_CUTOFF
    ):
        raise ValueError("frozen evaluator/gate contract identity mismatch")
    expected_versions = set(EXPECTED_STRATEGIES.values())
    if set(gate.triggered_trade_minimums) != expected_versions:
        raise ValueError("frozen gate strategy identities mismatch")
    pairs = {
        pair_id: (rule.treatment, rule.control)
        for pair_id, rule in gate.pair_rules.items()
    }
    if {value for pair in pairs.values() for value in pair} - expected_versions:
        raise ValueError("frozen gate pair identity mismatch")
    return FrozenV2Binding(paths=paths, pairs=pairs)


def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve(strict=True)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _one(
    db: sqlite3.Connection, query: str, params: tuple[Any, ...], *, label: str
) -> sqlite3.Row:
    rows = db.execute(query, params).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{label} requires exactly one row; observed {len(rows)}")
    return rows[0]


def _validate_json_integrity(
    row: sqlite3.Row, *, fields: tuple[str, ...], label: str
) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} payload is malformed") from error
    if not isinstance(payload, dict):
        raise TypeError(f"{label} payload must be a mapping")
    for field in fields:
        left, right = payload.get(field), row[field]
        if field in {
            "occurred_at",
            "established_at",
            "first_eligible_boundary",
            "timestamp",
        }:
            matches = _parse_utc(left, field=f"{label} {field}") == _parse_utc(
                right, field=f"{label} column {field}"
            )
        else:
            matches = left == right
        if not matches:
            raise ValueError(f"{label} payload/column mismatch for {field}")
    if "integrity_hash" in row and sha256_canonical(payload) != row["integrity_hash"]:
        raise ValueError(f"{label} integrity hash mismatch")
    return payload


def _validate_source_identity(
    db: sqlite3.Connection, *, operations_commit: str
) -> dict[str, Any]:
    if operations_commit != OPERATIONS_SHA:
        raise ValueError("installed operations commit mismatch")
    manifest = _one(
        db,
        "SELECT * FROM phase2_manifests WHERE phase2_epoch_id=?",
        (EPOCH_ID,),
        label="epoch006 manifest",
    )
    try:
        payload = json.loads(manifest["payload_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("epoch006 manifest payload is malformed") from error
    column_expectations = {
        "phase2_epoch_id": EPOCH_ID,
        "git_commit_hash": RESEARCH_SHA,
        "config_hash": CONFIG_SHA,
        "prompt_hash": PROMPT_SHA,
        "output_schema_hash": OUTPUT_SCHEMA_SHA,
        "database_schema_hash": DATABASE_SCHEMA_SHA,
    }
    for field, expected in column_expectations.items():
        if manifest[field] != expected or payload.get(field) != expected:
            raise ValueError(f"epoch006 manifest {field} mismatch")
    if (
        payload.get("manifest_hash") != manifest["manifest_hash"]
        or payload.get("manifest_id") != manifest["manifest_id"]
    ):
        raise ValueError("epoch006 manifest identity columns mismatch")
    body = {
        key: value
        for key, value in payload.items()
        if key not in {"manifest_id", "manifest_hash"}
    }
    if sha256_canonical(body) != manifest["manifest_hash"]:
        raise ValueError("epoch006 manifest canonical hash mismatch")
    frozen = payload.get("frozen_contract")
    if not isinstance(frozen, dict):
        raise TypeError("epoch006 frozen contract is absent")
    expected_contract = {
        "snapshot_schema_version": "1.0.0",
        "feature_schema_version": "1.0.0",
        "quant_trend_version": "QUANT_TREND_V1",
        "quant_mean_reversion_version": "QUANT_MR_V1",
        "detector_version": "SETUP_DETECTOR_V1",
        "regime_version": "REGIME_V1",
        "llm_strategy_version": "LLM_V1",
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "model_version": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "prompt_version": "LLM_PROMPT_V2",
        "output_schema_version": "LLM_OUTPUT_V2",
        "hybrid_trend_version": "HYBRID_TREND_LLM_V1",
        "hybrid_mr_version": "HYBRID_MR_LLM_V1",
        "simulator_version": "SIMULATOR_V1",
        "llm_geometry_adapter_version": "LLM_GEOMETRY_ADAPTER_V1",
    }
    for field, expected in expected_contract.items():
        if frozen.get(field) != expected:
            raise ValueError(f"epoch006 frozen contract {field} mismatch")
    return payload


def _validate_evidence_clock(db: sqlite3.Connection) -> None:
    anchor = _one(
        db,
        "SELECT * FROM phase2_recovery_events WHERE phase2_epoch_id=? AND event_type='PROSPECTIVE_START_ESTABLISHED'",
        (EPOCH_ID,),
        label="epoch006 prospective anchor",
    )
    anchor_payload = _validate_json_integrity(
        anchor,
        fields=("phase2_epoch_id", "event_type", "source_identity", "occurred_at"),
        label="epoch006 prospective anchor",
    )
    if (
        anchor["source_identity"] != EPOCH_ID
        or _parse_utc(anchor["occurred_at"], field="prospective anchor") != ANCHOR
    ):
        raise ValueError("epoch006 prospective anchor mismatch")
    if anchor_payload.get("details") != {"prospective_start": anchor["occurred_at"]}:
        raise ValueError("epoch006 prospective anchor details mismatch")
    window = _one(
        db,
        "SELECT * FROM phase2_evidence_windows WHERE phase2_epoch_id=?",
        (EPOCH_ID,),
        label="epoch006 evidence window",
    )
    _validate_json_integrity(
        window,
        fields=(
            "window_id",
            "phase2_epoch_id",
            "first_eligible_boundary",
            "deployment_id",
            "prospective_start_integrity_hash",
            "established_at",
            "rule_version",
            "reason_code",
        ),
        label="epoch006 evidence window",
    )
    if (
        _parse_utc(window["first_eligible_boundary"], field="evidence-window start")
        != FIRST_BOUNDARY
        or _parse_utc(window["established_at"], field="evidence-window establishment")
        != ANCHOR
        or window["prospective_start_integrity_hash"] != anchor["integrity_hash"]
        or window["deployment_id"] is not None
        or window["rule_version"] != INITIAL_EVIDENCE_WINDOW_RULE
        or window["reason_code"] != "INITIAL_PROSPECTIVE_START"
    ):
        raise ValueError("epoch006 evidence-window identity mismatch")


def _snapshot_payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["canonical_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("snapshot canonical payload is malformed") from error
    if (
        not isinstance(payload, dict)
        or sha256_canonical(payload) != row["snapshot_hash"]
    ):
        raise ValueError("snapshot canonical hash mismatch")
    expected = {
        "snapshot_id": row["snapshot_id"],
        "epoch_id": row["epoch_id"],
        "observation_class": row["observation_class"],
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise ValueError(f"snapshot payload/column mismatch for {field}")
    if _parse_utc(
        payload.get("snapshot_timestamp"), field="snapshot timestamp"
    ) != _parse_utc(row["snapshot_timestamp"], field="snapshot column timestamp"):
        raise ValueError("snapshot timestamp payload/column mismatch")
    if bool(payload.get("data_quality", {}).get("scoreable")) != bool(row["scoreable"]):
        raise ValueError("snapshot scoreability payload/column mismatch")
    return payload


def _load_decisions(
    db: sqlite3.Connection, snapshot_hash: str
) -> dict[str, sqlite3.Row]:
    rows = db.execute(
        "SELECT * FROM strategy_decisions WHERE snapshot_hash=? ORDER BY strategy_version",
        (snapshot_hash,),
    ).fetchall()
    by_version: dict[str, sqlite3.Row] = {}
    for row in rows:
        expected_version = EXPECTED_STRATEGIES.get(row["strategy_id"])
        if expected_version is None or row["strategy_version"] != expected_version:
            raise ValueError("unexpected frozen strategy identity")
        if row["strategy_version"] in by_version:
            raise ValueError("duplicate frozen strategy version")
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("strategy-decision payload is malformed") from error
        for field in (
            "decision_id",
            "snapshot_hash",
            "strategy_id",
            "strategy_version",
            "decision",
        ):
            if payload.get(field) != row[field]:
                raise ValueError(
                    f"strategy-decision payload/column mismatch for {field}"
                )
        if row["decision"] not in {"LONG", "SHORT", "NO_TRADE"}:
            raise ValueError("invalid strategy decision state")
        by_version[row["strategy_version"]] = row
    if set(by_version) != set(EXPECTED_STRATEGIES.values()):
        raise ValueError(
            "scoreable snapshot lacks the exact five-strategy decision set"
        )
    return by_version


def _load_trade(
    db: sqlite3.Connection, decision: sqlite3.Row, cutoff: datetime
) -> sqlite3.Row | None:
    rows = db.execute(
        "SELECT t.paper_trade_id,t.strategy_decision_id,t.strategy_id,t.snapshot_hash,t.signal_time,t.entry_time,t.exit_time,t.status,t.last_processed_at,"
        "CASE WHEN t.exit_time IS NOT NULL AND t.exit_time<=? THEN t.return_pct END eligible_return_pct,"
        "CASE WHEN t.entry_time IS NOT NULL AND t.entry_time<=? THEN t.entry_price END eligible_entry_price "
        "FROM paper_trades t LEFT JOIN phase2_outcome_exclusions x ON x.paper_trade_id=t.paper_trade_id "
        "WHERE t.strategy_decision_id=? AND x.paper_trade_id IS NULL",
        (cutoff.isoformat(), cutoff.isoformat(), decision["decision_id"]),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("strategy decision has multiple non-excluded paper trades")
    trade = rows[0] if rows else None
    if decision["decision"] == "NO_TRADE" and trade is not None:
        raise ValueError("NO_TRADE decision unexpectedly has a paper trade")
    if decision["decision"] != "NO_TRADE" and trade is None:
        raise ValueError("directional decision lacks paper-trade lineage")
    if trade is not None and (
        trade["strategy_id"] != decision["strategy_id"]
        or trade["snapshot_hash"] != decision["snapshot_hash"]
    ):
        raise ValueError("paper-trade decision/snapshot lineage mismatch")
    return trade


def _trade_interval(trade: sqlite3.Row) -> tuple[datetime, datetime | None] | None:
    if trade["status"] == "SUPPRESSED_POSITION_OPEN":
        return None
    start = _parse_utc(trade["signal_time"], field="trade signal time")
    end_value = trade["exit_time"]
    if end_value is None and trade["status"] in TERMINAL_SUPPRESSED:
        end_value = trade["last_processed_at"]
    end = _parse_utc(end_value, field="trade interval end") if end_value else None
    if end is not None and end < start:
        raise ValueError("paper-trade interval ends before it begins")
    return start, end


def _active_at(
    intervals: dict[str, list[tuple[datetime, datetime | None]]],
    version: str,
    boundary: datetime,
) -> bool:
    return any(
        start < boundary and (end is None or end > boundary)
        for start, end in intervals[version]
    )


def snapshot_order_key(
    snapshot_timestamp: datetime, snapshot_hash: str
) -> tuple[datetime, str]:
    """Frozen total order, including the synthetic equal-timestamp tiebreak."""
    return snapshot_timestamp.astimezone(UTC), snapshot_hash


def _state_at_cutoff(
    trade: sqlite3.Row | None, *, decision: str, cutoff: datetime
) -> str:
    if decision == "NO_TRADE":
        return "COMPLETE_ZERO"
    assert trade is not None
    if trade["status"] in TERMINAL_SUPPRESSED:
        terminal = _parse_utc(
            trade["last_processed_at"], field="suppression terminal time"
        )
        return "COMPLETE_ZERO" if terminal <= cutoff else "PENDING"
    entry = (
        _parse_utc(trade["entry_time"], field="entry time")
        if trade["entry_time"]
        else None
    )
    if entry is None or entry > cutoff:
        return "PENDING"
    exit_at = (
        _parse_utc(trade["exit_time"], field="exit time")
        if trade["exit_time"]
        else None
    )
    if exit_at is None or exit_at > cutoff:
        return "OPEN"
    if trade["status"] != "CLOSED" or trade["eligible_return_pct"] is None:
        raise ValueError("terminal trade lacks a cutoff-eligible closed return")
    return "CLOSED"


def _regime(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    regime = snapshot.get("regime")
    if not isinstance(regime, dict):
        raise TypeError("scoreable snapshot regime is absent")
    trend, volatility, combined = (
        regime.get("trend"),
        regime.get("volatility"),
        regime.get("combined"),
    )
    if not all(isinstance(value, str) for value in (trend, volatility, combined)):
        raise ValueError("scoreable snapshot regime is incomplete")
    if combined != f"{trend}_{volatility}" or combined not in VALID_REGIMES:
        raise ValueError("scoreable snapshot regime is invalid or inconsistent")
    return trend, volatility, combined


def _snapshot_price(snapshot: dict[str, Any]) -> Decimal:
    try:
        value = snapshot["market"]["hype_features"]["last_15m_close"]
    except (KeyError, TypeError) as error:
        raise ValueError("snapshot one-HYPE reference price is absent") from error
    result = _decimal(value, field="snapshot one-HYPE reference price")
    if result <= 0:
        raise ValueError("snapshot one-HYPE reference price must be positive")
    return result


def _llm_cost(db: sqlite3.Connection, snapshot_hash: str) -> Decimal:
    row = _one(
        db,
        "SELECT * FROM llm_decisions WHERE phase2_epoch_id=? AND input_snapshot_hash=? AND strategy_version='LLM_V1'",
        (EPOCH_ID, snapshot_hash),
        label="shared LLM decision",
    )
    payload = _validate_json_integrity(
        row,
        fields=(
            "experiment_id",
            "phase2_epoch_id",
            "input_snapshot_hash",
            "decision",
            "runner_status",
            "reason_code",
            "timestamp",
        ),
        label="shared LLM decision",
    )
    for field, expected in {
        "model": "gpt-5.6-terra",
        "model_version": "gpt-5.6-terra",
        "prompt_version": "LLM_PROMPT_V2",
        "output_schema_version": "LLM_OUTPUT_V2",
    }.items():
        if payload.get(field) != expected:
            raise ValueError(f"shared LLM decision {field} mismatch")
    if (
        payload.get("tool_calls_count") != 0
        or payload.get("tool_integrity_ok") is not True
    ):
        raise ValueError("shared LLM decision tool contract mismatch")
    cost = _decimal(payload.get("model_cost_usd"), field="shared LLM model cost")
    if cost < 0:
        raise ValueError("shared LLM model cost cannot be negative")
    return cost


def _return_and_cost(
    *,
    version: str,
    decision: sqlite3.Row,
    trade: sqlite3.Row | None,
    state: str,
    snapshot_price: Decimal,
    llm_cost: Decimal,
) -> tuple[Decimal, Decimal]:
    if state == "CLOSED":
        assert trade is not None
        returned = _decimal(trade["eligible_return_pct"], field="stored return_pct")
    elif state == "COMPLETE_ZERO":
        returned = Decimal(0)
    else:
        raise ValueError("right-censored legs do not have numeric returns")
    returned = _quantize(returned)
    if version not in LLM_DEPENDENT:
        return returned, Decimal(0)
    if trade is not None and trade["eligible_entry_price"] is not None:
        denominator = _decimal(
            trade["eligible_entry_price"], field="one-HYPE entry notional"
        )
    else:
        denominator = snapshot_price
    if denominator <= 0:
        raise ValueError("API-cost denominator must be positive")
    return returned, _quantize(llm_cost / denominator)


def derive_gate_evidence_v2(
    database_path: str | Path,
    *,
    repository_root: str | Path,
    installed_operations_commit: str,
) -> GateEvidence:
    """Derive frozen V2 GateEvidence from a read-only, identity-bound DB.

    The function never invokes the formal evaluator and never writes to SQLite.
    """
    binding = load_frozen_v2_binding(repository_root)
    db = _connect_read_only(database_path)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("source database integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("source database has foreign-key violations")
        _validate_source_identity(db, operations_commit=installed_operations_commit)
        _validate_evidence_clock(db)
        post_cutoff = db.execute(
            "SELECT 1 FROM decision_snapshots WHERE epoch_id=? AND observation_class='SCORED_PROSPECTIVE' AND snapshot_timestamp>? LIMIT 1",
            (EPOCH_ID, FORMAL_CUTOFF.isoformat()),
        ).fetchone()
        if post_cutoff is not None:
            raise ValueError("post-cutoff scored snapshot contamination")
        future_cycle = db.execute(
            "SELECT 1 FROM research_cycles WHERE observation_class='SCORED_PROSPECTIVE' AND scheduled_at>? LIMIT 1",
            (FORMAL_CUTOFF.isoformat(),),
        ).fetchone()
        if future_cycle is not None:
            raise ValueError("post-cutoff scored cycle contamination")

        cycles = db.execute(
            "SELECT * FROM research_cycles WHERE observation_class='SCORED_PROSPECTIVE' AND scheduled_at>=? AND scheduled_at<=? ORDER BY scheduled_at,cycle_id",
            (FIRST_BOUNDARY.isoformat(), FORMAL_CUTOFF.isoformat()),
        ).fetchall()
        if not cycles:
            raise ValueError("frozen evidence window contains no prospective cycles")
        cutoff_cycles = [
            row
            for row in cycles
            if _parse_utc(row["scheduled_at"], field="cycle scheduled_at")
            == FORMAL_CUTOFF
        ]
        if len(cutoff_cycles) != 1 or cutoff_cycles[0]["status"] not in {
            "COMPLETE",
            "REJECTED",
        }:
            raise ValueError("formal cutoff requires exactly one terminal scored cycle")
        snapshots: list[
            tuple[
                datetime,
                str,
                sqlite3.Row,
                dict[str, Any],
                dict[str, sqlite3.Row],
                dict[str, sqlite3.Row | None],
                Decimal,
            ]
        ] = []
        seen_cycle_times: set[datetime] = set()
        for cycle in cycles:
            scheduled = _parse_utc(cycle["scheduled_at"], field="cycle scheduled_at")
            if scheduled in seen_cycle_times:
                raise ValueError("duplicate scored cycle boundary")
            seen_cycle_times.add(scheduled)
            if cycle["status"] == "REJECTED":
                continue
            if (
                cycle["status"] != "COMPLETE"
                or cycle["completed_at"] is None
                or cycle["snapshot_hash"] is None
            ):
                raise ValueError(
                    "in-window scored cycle is non-terminal or lacks snapshot lineage"
                )
            snapshot_row = _one(
                db,
                "SELECT * FROM decision_snapshots WHERE snapshot_hash=?",
                (cycle["snapshot_hash"],),
                label="cycle snapshot",
            )
            if (
                snapshot_row["epoch_id"] != EPOCH_ID
                or snapshot_row["observation_class"] != "SCORED_PROSPECTIVE"
            ):
                raise ValueError("cycle snapshot epoch/class mismatch")
            if (
                _parse_utc(
                    snapshot_row["snapshot_timestamp"], field="snapshot timestamp"
                )
                != scheduled
            ):
                raise ValueError(
                    "cycle snapshot is not aligned to its scheduled boundary"
                )
            snapshot = _snapshot_payload(snapshot_row)
            if not bool(snapshot_row["scoreable"]):
                continue
            _regime(snapshot)
            decisions = _load_decisions(db, snapshot_row["snapshot_hash"])
            trades = {
                version: _load_trade(db, decision, FORMAL_CUTOFF)
                for version, decision in decisions.items()
            }
            cost = _llm_cost(db, snapshot_row["snapshot_hash"])
            snapshots.append(
                (
                    scheduled,
                    snapshot_row["snapshot_hash"],
                    snapshot_row,
                    snapshot,
                    decisions,
                    trades,
                    cost,
                )
            )

        orphan = db.execute(
            "SELECT 1 FROM decision_snapshots s LEFT JOIN research_cycles c ON c.snapshot_hash=s.snapshot_hash AND c.observation_class='SCORED_PROSPECTIVE' "
            "WHERE s.epoch_id=? AND s.observation_class='SCORED_PROSPECTIVE' AND s.scoreable=1 AND s.snapshot_timestamp>=? AND s.snapshot_timestamp<=? AND c.cycle_id IS NULL LIMIT 1",
            (EPOCH_ID, FIRST_BOUNDARY.isoformat(), FORMAL_CUTOFF.isoformat()),
        ).fetchone()
        if orphan is not None:
            raise ValueError("scoreable snapshot lacks research-cycle lineage")

        intervals: dict[str, list[tuple[datetime, datetime | None]]] = {
            version: [] for version in EXPECTED_STRATEGIES.values()
        }
        for _, _, _, _, decisions, trades, _ in snapshots:
            for version, trade in trades.items():
                if trade is None:
                    continue
                interval = _trade_interval(trade)
                if interval is not None:
                    intervals[version].append(interval)

        triggered = {version: 0 for version in EXPECTED_STRATEGIES.values()}
        for _, _, _, _, decisions, trades, _ in snapshots:
            for version, trade in trades.items():
                if (
                    trade is None
                    or trade["status"] not in {"OPEN", "CLOSED"}
                    or trade["entry_time"] is None
                ):
                    continue
                if _parse_utc(trade["entry_time"], field="entry time") <= FORMAL_CUTOFF:
                    triggered[version] += 1

        pair_values: dict[str, dict[str, list[Any]]] = {
            pair_id: {
                "differences": [],
                "adjusted": [],
                "trend": [],
                "volatility": [],
                "regime": [],
                "censored": [],
            }
            for pair_id in binding.pairs
        }
        for boundary, snapshot_hash, _, snapshot, decisions, trades, llm_cost in sorted(
            snapshots, key=lambda item: snapshot_order_key(item[0], item[1])
        ):
            trend, volatility, regime = _regime(snapshot)
            reference_price = _snapshot_price(snapshot)
            for pair_id, (treatment, control) in binding.pairs.items():
                if _active_at(intervals, treatment, boundary) or _active_at(
                    intervals, control, boundary
                ):
                    continue
                treatment_state = _state_at_cutoff(
                    trades[treatment],
                    decision=decisions[treatment]["decision"],
                    cutoff=FORMAL_CUTOFF,
                )
                control_state = _state_at_cutoff(
                    trades[control],
                    decision=decisions[control]["decision"],
                    cutoff=FORMAL_CUTOFF,
                )
                values = pair_values[pair_id]
                if treatment_state in {"PENDING", "OPEN"} or control_state in {
                    "PENDING",
                    "OPEN",
                }:
                    values["censored"].append(
                        float((FORMAL_CUTOFF - boundary).total_seconds())
                    )
                    continue
                treatment_return, treatment_cost = _return_and_cost(
                    version=treatment,
                    decision=decisions[treatment],
                    trade=trades[treatment],
                    state=treatment_state,
                    snapshot_price=reference_price,
                    llm_cost=llm_cost,
                )
                control_return, control_cost = _return_and_cost(
                    version=control,
                    decision=decisions[control],
                    trade=trades[control],
                    state=control_state,
                    snapshot_price=reference_price,
                    llm_cost=llm_cost,
                )
                values["differences"].append(
                    float(_quantize(treatment_return - control_return))
                )
                values["adjusted"].append(
                    float(
                        _quantize(
                            (treatment_return - treatment_cost)
                            - (control_return - control_cost)
                        )
                    )
                )
                values["trend"].append(trend)
                values["volatility"].append(volatility)
                values["regime"].append(regime)

        pairs = {
            pair_id: PairEvidence(
                pair_id=pair_id,
                paired_differences=tuple(values["differences"]),
                cost_adjusted_differences=tuple(values["adjusted"]),
                trend_states=tuple(values["trend"]),
                volatility_states=tuple(values["volatility"]),
                regime_buckets=tuple(values["regime"]),
                open_position_count=len(values["censored"]),
                open_position_durations_seconds=tuple(values["censored"]),
            )
            for pair_id, values in pair_values.items()
        }
        for pair in pairs.values():
            pair.validate()
        return GateEvidence(
            phase2_epoch_id=EPOCH_ID,
            as_of=FORMAL_CUTOFF,
            triggered_trade_counts=triggered,
            pairs=pairs,
            evidence_source=f"FROZEN_DB_V2:{V2_SHA}",
        )
    finally:
        db.close()


def canonical_gate_evidence_payload(evidence: GateEvidence) -> dict[str, Any]:
    """Stable review/test representation; does not invoke the evaluator."""
    return {
        "phase2_epoch_id": evidence.phase2_epoch_id,
        "as_of": evidence.as_of.astimezone(UTC).isoformat(),
        "triggered_trade_counts": dict(sorted(evidence.triggered_trade_counts.items())),
        "pairs": {
            pair_id: {
                "paired_differences": [
                    canonical_decimal(value) for value in pair.paired_differences
                ],
                "cost_adjusted_differences": [
                    canonical_decimal(value) for value in pair.cost_adjusted_differences
                ],
                "trend_states": list(pair.trend_states),
                "volatility_states": list(pair.volatility_states),
                "regime_buckets": list(pair.regime_buckets),
                "open_position_count": pair.open_position_count,
                "open_position_durations_seconds": [
                    canonical_decimal(value)
                    for value in pair.open_position_durations_seconds
                ],
            }
            for pair_id, pair in sorted(evidence.pairs.items())
        },
        "evidence_source": evidence.evidence_source,
    }
