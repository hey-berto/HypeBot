from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from hype_autopilot.data.repository import Repository


def detector_proxy_report(repository: Repository) -> dict[str, Any]:
    rows = repository.db.execute(
        "SELECT d.snapshot_hash, d.trigger, s.decision, s.strategy_id, p.net_pnl, "
        "p.direction, snap.canonical_json FROM detector_decisions d "
        "JOIN strategy_decisions s ON s.snapshot_hash = d.snapshot_hash "
        "JOIN decision_snapshots snap ON snap.snapshot_hash = d.snapshot_hash "
        "LEFT JOIN paper_trades p ON p.strategy_decision_id = s.decision_id"
    ).fetchall()
    snapshot_triggers: dict[str, bool] = {}
    quant_signals = captured_signals = profitable = captured_profitable = missed_profitable = 0
    misses: dict[str, int] = defaultdict(int)
    for row in rows:
        triggered = row["trigger"] != "NO_TRIGGER"
        snapshot_triggers[row["snapshot_hash"]] = triggered
        if row["decision"] != "NO_TRADE":
            quant_signals += 1
            captured_signals += int(triggered)
        if row["net_pnl"] is not None and row["net_pnl"] > 0:
            profitable += 1
            captured_profitable += int(triggered)
            if not triggered:
                missed_profitable += 1
                snapshot = json.loads(row["canonical_json"])
                regime = snapshot["regime"]["combined"]
                misses[f"{regime}:{row['direction']}"] += 1
    total_snapshots = len(snapshot_triggers)
    triggered_snapshots = sum(snapshot_triggers.values())
    return {
        "label": "PHASE_1_PROVISIONAL_PROXY_DIAGNOSTICS_NOT_FINAL_LLM_GATING_PRECISION_RECALL",
        "snapshots": total_snapshots,
        "triggered_snapshots": triggered_snapshots,
        "trigger_rate": triggered_snapshots / total_snapshots if total_snapshots else None,
        "quant_signals": quant_signals,
        "quant_signal_capture_rate": captured_signals / quant_signals if quant_signals else None,
        "profitable_quant_trades": profitable,
        "profitable_quant_trade_capture_rate": captured_profitable / profitable if profitable else None,
        "missed_profitable_quant_trade_count": missed_profitable,
        "missed_profitable_by_regime_direction": dict(sorted(misses.items())),
    }


def operational_status(repository: Repository) -> dict[str, Any]:
    tables = ["raw_candles", "raw_market_observations", "raw_bbo_observations",
              "raw_funding_observations", "decision_snapshots", "strategy_decisions",
              "detector_decisions", "paper_trades", "paper_fills", "research_cycles",
              "collection_gaps", "data_quality_events", "health_events", "epochs"]
    tables.append("epoch_configurations")
    tables.append("paper_orders")
    counts = {table: repository.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in tables}
    recent_cycles = [dict(row) for row in repository.db.execute(
        "SELECT scheduled_at, observation_class, status, snapshot_hash, details_json "
        "FROM research_cycles ORDER BY scheduled_at DESC LIMIT 8"
    ).fetchall()]
    open_gaps = [dict(row) for row in repository.db.execute(
        "SELECT symbol, interval, gap_start, gap_end, detected_at FROM collection_gaps "
        "WHERE status = 'OPEN' ORDER BY gap_start"
    ).fetchall()]
    active_trades = [trade.model_dump(mode="json") for trade in repository.active_trades()]
    rejected = repository.db.execute(
        "SELECT COUNT(*) FROM decision_snapshots WHERE scoreable = 0"
    ).fetchone()[0]
    last_health = [dict(row) for row in repository.db.execute(
        "SELECT occurred_at, component, status, details_json FROM health_events "
        "ORDER BY id DESC LIMIT 12"
    ).fetchall()]
    cycle_times = [row[0] for row in repository.db.execute(
        "SELECT scheduled_at FROM research_cycles WHERE observation_class = 'SOAK' ORDER BY scheduled_at"
    ).fetchall()]
    missed_cycles: list[str] = []
    if len(cycle_times) >= 2:
        current = datetime.fromisoformat(cycle_times[0]) + timedelta(minutes=15)
        end = datetime.fromisoformat(cycle_times[-1])
        known = set(cycle_times)
        while current < end:
            if current.isoformat() not in known:
                missed_cycles.append(current.isoformat())
            current += timedelta(minutes=15)
    return {"counts": counts, "recent_cycles": recent_cycles, "open_gaps": open_gaps,
            "active_trades": active_trades, "rejected_snapshots": rejected,
            "recent_health": last_health, "missed_soak_cycle_boundaries": missed_cycles,
            "detector_proxy": detector_proxy_report(repository)}


def trace_trade(repository: Repository, trade_id: str) -> dict[str, Any]:
    trade = repository.db.execute(
        "SELECT * FROM paper_trades WHERE paper_trade_id = ?", (trade_id,)
    ).fetchone()
    if trade is None:
        raise KeyError(trade_id)
    decision = repository.db.execute(
        "SELECT * FROM strategy_decisions WHERE decision_id = ?", (trade["strategy_decision_id"],)
    ).fetchone()
    snapshot = repository.db.execute(
        "SELECT * FROM decision_snapshots WHERE snapshot_hash = ?", (trade["snapshot_hash"],)
    ).fetchone()
    refs = [dict(row) for row in repository.db.execute(
        "SELECT source_name, source_timestamp FROM snapshot_source_references WHERE snapshot_hash = ? "
        "ORDER BY source_name", (trade["snapshot_hash"],)
    ).fetchall()]
    fills = [dict(row) for row in repository.db.execute(
        "SELECT fill_time, fill_type, price, fee, slippage_cost, details_json FROM paper_fills "
        "WHERE paper_trade_id = ? ORDER BY fill_time", (trade_id,)
    ).fetchall()]
    return {"paper_trade": dict(trade), "strategy_decision": dict(decision) if decision else None,
            "immutable_snapshot": dict(snapshot) if snapshot else None,
            "snapshot_source_references": refs, "paper_fills": fills}
