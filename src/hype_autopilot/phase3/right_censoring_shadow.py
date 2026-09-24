"""Read-only, non-binding terminal-MTM sensitivity for the epoch006 cutoff.

This module is deliberately separate from ``gate.py``.  It preserves the
formal right-censoring treatment and only describes what a frozen-economics
close at the cutoff would have looked like for positions still open then.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CLASSIFICATION = "NON_BINDING_RIGHT_CENSORING_SENSITIVITY"
DIAGNOSTIC_VERSION = "EPOCH006_DAY42_RIGHT_CENSORING_SHADOW_V1"
EPOCH006_CUTOFF = datetime(2026, 11, 5, 0, 15, tzinfo=UTC)


@dataclass(frozen=True)
class FrozenExitEconomics:
    """Values copied from the already-frozen simulator configuration."""

    fee_bps_per_side: float
    slippage_bps_per_side: float


@dataclass(frozen=True)
class CutoffPosition:
    paper_trade_id: str
    strategy_id: str
    direction: str
    signal_time: datetime
    entry_time: datetime | None
    entry_price: float | None
    fees: float
    slippage_cost: float
    funding_cost: float
    status_at_cutoff: str
    regime: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _sign(direction: str) -> int:
    if direction == "LONG":
        return 1
    if direction == "SHORT":
        return -1
    raise ValueError(f"unsupported position direction: {direction}")


def _regime(canonical_json: str) -> str | None:
    payload = json.loads(canonical_json)
    value = payload.get("regime", {}).get("combined")
    return str(value) if value is not None else None


def shadow_terminal_value(
    position: CutoffPosition,
    *,
    cutoff_price: float,
    economics: FrozenExitEconomics,
    cutoff: datetime = EPOCH006_CUTOFF,
) -> dict[str, Any]:
    """Apply precisely the simulator's existing exit-price and cost algebra."""
    if position.status_at_cutoff != "OPEN":
        raise ValueError("terminal value is defined only for an open position")
    if position.entry_time is None or position.entry_price is None:
        raise ValueError("open position requires entry_time and entry_price")
    cutoff = _utc(cutoff)
    if _utc(position.entry_time) > cutoff:
        raise ValueError("open position entry cannot be after the cutoff")
    if cutoff_price <= 0:
        raise ValueError("cutoff price must be positive")
    sign = _sign(position.direction)
    exit_price = cutoff_price * (1 - sign * economics.slippage_bps_per_side / 10_000)
    exit_fee = exit_price * economics.fee_bps_per_side / 10_000
    exit_slippage = abs(exit_price - cutoff_price)
    gross_price_pnl = sign * (cutoff_price - position.entry_price)
    total_fees = position.fees + exit_fee
    total_slippage = position.slippage_cost + exit_slippage
    terminal_net_pnl = gross_price_pnl - total_fees - total_slippage - position.funding_cost
    return {
        "paper_trade_id": position.paper_trade_id,
        "strategy_id": position.strategy_id,
        "direction": position.direction,
        "regime": position.regime,
        "entry_time": _iso(position.entry_time),
        "entry_price": position.entry_price,
        "cutoff": _iso(cutoff),
        "cutoff_raw_price": cutoff_price,
        "shadow_exit_price": exit_price,
        "gross_price_pnl": gross_price_pnl,
        "already_accrued_funding_cost": position.funding_cost,
        "stored_entry_exit_fees_before_shadow_exit": position.fees,
        "shadow_exit_fee": exit_fee,
        "stored_entry_exit_slippage_before_shadow_exit": position.slippage_cost,
        "shadow_exit_slippage": exit_slippage,
        "hypothetical_terminal_net_pnl": terminal_net_pnl,
        "position_age_seconds": (_utc(cutoff) - _utc(position.entry_time)).total_seconds(),
        "unrealized_pnl_sign": "WIN" if terminal_net_pnl > 0 else "LOSS" if terminal_net_pnl < 0 else "FLAT",
    }


def build_right_censoring_shadow(
    positions: Sequence[CutoffPosition],
    *,
    cutoff_price: float | None,
    economics: FrozenExitEconomics,
    primary_phase3_result: Mapping[str, Any],
    cutoff: datetime = EPOCH006_CUTOFF,
) -> dict[str, Any]:
    """Build deterministic explanatory output without evaluating any gate rule."""
    cutoff = _utc(cutoff)
    if cutoff != EPOCH006_CUTOFF:
        raise ValueError("epoch006 shadow diagnostic is fixed to the Day-42 cutoff")
    open_positions = [item for item in positions if item.status_at_cutoff == "OPEN"]
    pending_positions = [item for item in positions if item.status_at_cutoff == "PENDING_ENTRY"]
    unsupported = [item.status_at_cutoff for item in positions if item.status_at_cutoff not in {"OPEN", "PENDING_ENTRY"}]
    if unsupported:
        raise ValueError(f"unsupported cutoff statuses: {sorted(set(unsupported))}")
    if open_positions and cutoff_price is None:
        raise ValueError("an evidence-available cutoff price is required for open positions")
    valuations = [
        shadow_terminal_value(item, cutoff_price=float(cutoff_price), economics=economics, cutoff=cutoff)
        for item in open_positions
    ]
    pending = [
        {
            "paper_trade_id": item.paper_trade_id,
            "strategy_id": item.strategy_id,
            "direction": item.direction,
            "signal_time": _iso(item.signal_time),
            "regime": item.regime,
            "treatment": "NO_ENTRY_NO_TERMINAL_RETURN_INVENTED",
        }
        for item in pending_positions
    ]
    by_strategy: dict[str, dict[str, Any]] = {}
    for strategy_id in sorted({item.strategy_id for item in positions}):
        strategy_open = [value for value in valuations if value["strategy_id"] == strategy_id]
        strategy_pending = [value for value in pending if value["strategy_id"] == strategy_id]
        by_strategy[strategy_id] = {
            "open_position_count": len(strategy_open),
            "pending_position_count": len(strategy_pending),
            "terminal_unrealized_contribution": sum(value["hypothetical_terminal_net_pnl"] for value in strategy_open),
            "hypothetical_economic_effect_if_open_closed_at_cutoff": sum(value["hypothetical_terminal_net_pnl"] for value in strategy_open),
        }
    return {
        "classification": CLASSIFICATION,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "cutoff": _iso(cutoff),
        "PRIMARY_PHASE3_RESULT": {
            "result": dict(primary_phase3_result),
            "right_censoring": "UNCHANGED_OPEN_AND_PENDING_POSITIONS_REMAIN_EXCLUDED_FROM_PRIMARY_OUTCOMES",
            "binding_effect": "NONE_NO_PROMOTE_REJECT_INCONCLUSIVE_CHANGE",
        },
        "NON_BINDING_RIGHT_CENSORING_SENSITIVITY": {
            "cutoff_raw_price": cutoff_price,
            "open_position_count": len(valuations),
            "pending_position_count": len(pending),
            "terminal_unrealized_contribution": sum(value["hypothetical_terminal_net_pnl"] for value in valuations),
            "hypothetical_economic_effect_if_open_closed_at_cutoff": sum(value["hypothetical_terminal_net_pnl"] for value in valuations),
            "open_position_valuations": valuations,
            "pending_positions": pending,
            "by_strategy": by_strategy,
        },
        "OPEN_DURATION_DESCRIPTIVE": {
            "positions": [
                {
                    key: value[key]
                    for key in ("paper_trade_id", "strategy_id", "direction", "regime", "position_age_seconds", "unrealized_pnl_sign")
                }
                for value in valuations
            ],
            "interpretation": "DESCRIPTIVE_ONLY_NO_CONFIRMATORY_ASSOCIATION_TEST",
        },
    }


def _read_only_connection(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _cutoff_price(db: sqlite3.Connection, cutoff: datetime) -> float | None:
    """The latest 1m close that was itself received by the exact cutoff."""
    row = db.execute(
        "SELECT close FROM raw_candles WHERE symbol='HYPE' AND interval='1m' "
        "AND close_time <= ? AND received_at <= ? "
        "ORDER BY close_time DESC, received_at DESC, id DESC LIMIT 1",
        (_iso(cutoff), _iso(cutoff)),
    ).fetchone()
    return float(row["close"]) if row is not None else None


def _positions_at_cutoff(db: sqlite3.Connection, epoch_id: str, cutoff: datetime) -> list[CutoffPosition]:
    rows = db.execute(
        "SELECT t.paper_trade_id,t.strategy_id,t.direction,t.signal_time,t.entry_time,t.entry_price,"
        "t.fees,t.slippage_cost,t.funding_cost,t.status,s.canonical_json "
        "FROM paper_trades t JOIN decision_snapshots s ON s.snapshot_hash=t.snapshot_hash "
        "LEFT JOIN phase2_outcome_exclusions x ON x.paper_trade_id=t.paper_trade_id "
        "WHERE s.epoch_id=? AND s.observation_class='SCORED_PROSPECTIVE' AND s.scoreable=1 "
        "AND x.paper_trade_id IS NULL AND t.status IN ('PENDING_ENTRY','OPEN') AND t.signal_time <= ? "
        "AND ((t.status='PENDING_ENTRY' AND t.entry_time IS NULL) "
        "OR (t.entry_time IS NOT NULL AND t.entry_time <= ? "
        "AND (t.exit_time IS NULL OR t.exit_time > ?))) "
        "ORDER BY t.strategy_id,t.paper_trade_id",
        (epoch_id, _iso(cutoff), _iso(cutoff), _iso(cutoff)),
    ).fetchall()
    positions: list[CutoffPosition] = []
    for row in rows:
        entered = row["entry_time"] is not None
        positions.append(
            CutoffPosition(
                paper_trade_id=str(row["paper_trade_id"]), strategy_id=str(row["strategy_id"]),
                direction=str(row["direction"]), signal_time=datetime.fromisoformat(row["signal_time"]),
                entry_time=datetime.fromisoformat(row["entry_time"]) if entered else None,
                entry_price=float(row["entry_price"]) if entered else None,
                fees=float(row["fees"]), slippage_cost=float(row["slippage_cost"]),
                funding_cost=float(row["funding_cost"]),
                status_at_cutoff="OPEN" if entered else "PENDING_ENTRY", regime=_regime(str(row["canonical_json"])),
            )
        )
    return positions


def collect_right_censoring_shadow(
    database_path: str | Path,
    *,
    economics: FrozenExitEconomics,
    primary_phase3_result: Mapping[str, Any],
    phase2_epoch_id: str = "phase2_epoch_006",
    cutoff: datetime = EPOCH006_CUTOFF,
) -> dict[str, Any]:
    """Read a frozen-at-cutoff DB snapshot; this function never writes evidence."""
    cutoff = _utc(cutoff)
    db = _read_only_connection(database_path)
    try:
        return build_right_censoring_shadow(
            _positions_at_cutoff(db, phase2_epoch_id, cutoff), cutoff_price=_cutoff_price(db, cutoff),
            economics=economics, primary_phase3_result=primary_phase3_result, cutoff=cutoff,
        )
    finally:
        db.close()
