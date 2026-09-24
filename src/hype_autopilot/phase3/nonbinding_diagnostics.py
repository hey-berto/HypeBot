"""Read-only, non-binding epoch006 research diagnostics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


NON_BINDING_DIAGNOSTIC = "NON_BINDING_DIAGNOSTIC"
DIAGNOSTIC_VERSION = "EPOCH006_NONBINDING_DIAGNOSTICS_V1"
PAIR_STRATEGIES = {
    "LLM_V1__vs__QUANT_TREND_V1": ("LLM_V1", "QUANT_TREND"),
    "LLM_V1__vs__QUANT_MR_V1": ("LLM_V1", "QUANT_MR"),
    "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1": (
        "HYBRID_TREND_LLM_V1",
        "QUANT_TREND",
    ),
    "HYBRID_MR_LLM_V1__vs__QUANT_MR_V1": ("HYBRID_MR_LLM_V1", "QUANT_MR"),
}
HYBRID_QUANT_PAIRS = {
    "HYBRID_TREND_LLM_V1": "QUANT_TREND",
    "HYBRID_MR_LLM_V1": "QUANT_MR",
}
_DESCRIPTOR_FIELDS = (
    "realised_vol_1h",
    "atr_pct_1h",
    "funding_rate",
    "funding_zscore",
)


def _read_only_connection(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _snapshot_rows(db: sqlite3.Connection, epoch_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT snapshot_hash,snapshot_timestamp,canonical_json FROM decision_snapshots "
        "WHERE epoch_id=? AND observation_class='SCORED_PROSPECTIVE' AND scoreable=1 "
        "ORDER BY snapshot_timestamp,snapshot_hash",
        (epoch_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        snapshot = json.loads(row["canonical_json"])
        features = snapshot.get("market", {}).get("hype_features", {})
        regime = snapshot.get("regime", {})
        timestamp = str(row["snapshot_timestamp"])
        result.append(
            {
                "snapshot_hash": str(row["snapshot_hash"]),
                "snapshot_timestamp": timestamp,
                "regime": str(regime.get("combined", "UNKNOWN")),
                "hour_utc": timestamp[11:13] if len(timestamp) >= 13 else "UNKNOWN",
                "descriptors": {
                    field: features.get(field) for field in _DESCRIPTOR_FIELDS
                },
            }
        )
    return result


def _decisions_by_snapshot(
    db: sqlite3.Connection, epoch_id: str
) -> dict[str, dict[str, str]]:
    rows = db.execute(
        "SELECT d.snapshot_hash,d.strategy_id,d.decision FROM strategy_decisions d "
        "JOIN decision_snapshots s ON s.snapshot_hash=d.snapshot_hash "
        "WHERE s.epoch_id=? AND s.observation_class='SCORED_PROSPECTIVE' "
        "AND s.scoreable=1",
        (epoch_id,),
    ).fetchall()
    result: dict[str, dict[str, str]] = defaultdict(dict)
    for row in rows:
        result[str(row["snapshot_hash"])][str(row["strategy_id"])] = str(
            row["decision"]
        )
    return result


def _distribution(values: list[str]) -> dict[str, dict[str, float | int]]:
    counts = Counter(values)
    total = len(values)
    return {
        key: {"count": count, "proportion": count / total if total else 0.0}
        for key, count in sorted(counts.items())
    }


def _numeric_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "minimum": None, "maximum": None}
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def hybrid_veto_value_decomposition(
    db: sqlite3.Connection, epoch_id: str
) -> dict[str, Any]:
    """Build deterministic, frequency-matched control assignments without outcomes."""
    snapshots = _snapshot_rows(db, epoch_id)
    decisions = _decisions_by_snapshot(db, epoch_id)
    diagnostics: dict[str, Any] = {}
    for hybrid_id, quant_id in HYBRID_QUANT_PAIRS.items():
        groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for snapshot in snapshots:
            decision_set = decisions.get(snapshot["snapshot_hash"], {})
            quant = decision_set.get(quant_id)
            hybrid = decision_set.get(hybrid_id)
            if quant in {None, "NO_TRADE"} or hybrid is None:
                continue
            groups[(quant, snapshot["regime"])].append(
                {
                    "snapshot_hash": snapshot["snapshot_hash"],
                    "hybrid_trade": str(hybrid != "NO_TRADE"),
                }
            )
        selected_hashes: list[str] = []
        strata: list[dict[str, Any]] = []
        for (direction, regime), members in sorted(groups.items()):
            retained = sum(item["hybrid_trade"] == "True" for item in members)
            selected = sorted(
                members,
                key=lambda item: hashlib.sha256(
                    f"{DIAGNOSTIC_VERSION}:{hybrid_id}:{item['snapshot_hash']}".encode()
                ).hexdigest(),
            )[:retained]
            selected_hashes.extend(item["snapshot_hash"] for item in selected)
            strata.append(
                {
                    "quant_direction": direction,
                    "regime": regime,
                    "quant_opportunities": len(members),
                    "hybrid_retained_trades": retained,
                    "hybrid_vetoes": len(members) - retained,
                    "matched_control_retained_trades": len(selected),
                }
            )
        diagnostics[hybrid_id] = {
            "quant_strategy": quant_id,
            "outcome_accessed": False,
            "control_assignment": "SHA256_SORTED_WITHIN_QUANT_DIRECTION_AND_REGIME",
            "strata": strata,
            "matched_control_trade_snapshot_hashes": sorted(selected_hashes),
        }
    return {"classification": NON_BINDING_DIAGNOSTIC, "strategies": diagnostics}


def fail_closed_selection_diagnostic(
    db: sqlite3.Connection, epoch_id: str
) -> dict[str, Any]:
    """Describe contemporaneous state by runner status; no causal claim."""
    snapshots = {item["snapshot_hash"]: item for item in _snapshot_rows(db, epoch_id)}
    rows = db.execute(
        "SELECT input_snapshot_hash,runner_status,reason_code FROM llm_decisions "
        "WHERE phase2_epoch_id=? ORDER BY timestamp,input_snapshot_hash",
        (epoch_id,),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        snapshot = snapshots.get(str(row["input_snapshot_hash"]))
        if snapshot is not None:
            status = str(row["runner_status"])
            grouped[status].append(snapshot)
            reasons[status][str(row["reason_code"])] += 1
    statuses: dict[str, Any] = {}
    for status in ("VALID", "FAIL_CLOSED"):
        members = grouped[status]
        statuses[status] = {
            "count": len(members),
            "reason_counts": dict(sorted(reasons[status].items())),
            "regime": _distribution([item["regime"] for item in members]),
            "hour_utc": _distribution([item["hour_utc"] for item in members]),
            "descriptors": {
                field: _numeric_summary(
                    [
                        float(item["descriptors"][field])
                        for item in members
                        if isinstance(item["descriptors"].get(field), (int, float))
                    ]
                )
                for field in _DESCRIPTOR_FIELDS
            },
        }
    return {
        "classification": NON_BINDING_DIAGNOSTIC,
        "interpretation": "DESCRIPTIVE_ASSOCIATION_ONLY_NO_CAUSAL_ATTRIBUTION",
        "statuses": statuses,
        "unavailable_descriptor_fields": [
            "market_stress_composite", "intrabar_range"
        ],
    }


def coeligibility_representativeness(
    db: sqlite3.Connection, epoch_id: str
) -> dict[str, Any]:
    """Compare scored-snapshot and canonical co-eligible regime composition."""
    snapshots = _snapshot_rows(db, epoch_id)
    snapshot_regime = {item["snapshot_hash"]: item["regime"] for item in snapshots}
    all_regimes = [item["regime"] for item in snapshots]
    result: dict[str, Any] = {}
    for pair_id in PAIR_STRATEGIES:
        rows = db.execute(
            "SELECT input_snapshot_hash,eligibility_status,outcome_status "
            "FROM phase2_pair_outcomes WHERE pair_id=?",
            (pair_id,),
        ).fetchall()
        present = {
            str(row["input_snapshot_hash"]): row
            for row in rows
            if str(row["input_snapshot_hash"]) in snapshot_regime
        }
        coeligible = [
            snapshot_regime[snapshot_hash]
            for snapshot_hash, row in present.items()
            if row["eligibility_status"] == "CO_ELIGIBLE"
        ]
        all_distribution = _distribution(all_regimes)
        coeligible_distribution = _distribution(coeligible)
        keys = set(all_distribution) | set(coeligible_distribution)
        distance = 0.5 * sum(
            abs(
                float(all_distribution.get(key, {}).get("proportion", 0.0))
                - float(coeligible_distribution.get(key, {}).get("proportion", 0.0))
            )
            for key in keys
        )
        result[pair_id] = {
            "all_scored_snapshot_count": len(snapshots),
            "pair_outcome_row_count": len(present),
            "missing_pair_outcome_count": len(snapshots) - len(present),
            "coeligible_count": len(coeligible),
            "excluded_or_suppressed_count": sum(
                row["eligibility_status"] != "CO_ELIGIBLE" for row in present.values()
            ),
            "outcome_status_counts": dict(
                sorted(Counter(str(row["outcome_status"]) for row in present.values()).items())
            ),
            "all_scored_regime_distribution": all_distribution,
            "coeligible_regime_distribution": coeligible_distribution,
            "total_variation_distance": distance,
        }
    return {"classification": NON_BINDING_DIAGNOSTIC, "pairs": result}


def return_component_decomposition(
    db: sqlite3.Connection, epoch_id: str
) -> dict[str, Any]:
    """Report stored closed-trade accounting components; never replace net PnL."""
    rows = db.execute(
        "SELECT t.paper_trade_id,t.strategy_id,t.snapshot_hash,t.gross_pnl,t.funding_cost,"
        "t.fees,t.slippage_cost,t.net_pnl FROM paper_trades t "
        "JOIN decision_snapshots s ON s.snapshot_hash=t.snapshot_hash "
        "WHERE s.epoch_id=? AND s.observation_class='SCORED_PROSPECTIVE' "
        "AND s.scoreable=1 AND t.status='CLOSED' ORDER BY t.exit_time,t.paper_trade_id",
        (epoch_id,),
    ).fetchall()
    components = [
        {
            "paper_trade_id": str(row["paper_trade_id"]),
            "strategy_id": str(row["strategy_id"]),
            "snapshot_hash": str(row["snapshot_hash"]),
            "price_pnl": float(row["gross_pnl"]),
            "funding": float(row["funding_cost"]),
            "entry_exit_fees": float(row["fees"]),
            "simulator_slippage": float(row["slippage_cost"]),
            "primary_net_pnl_unchanged": float(row["net_pnl"]),
        }
        for row in rows
    ]
    totals = {
        field: sum(float(item[field]) for item in components)
        for field in (
            "price_pnl",
            "funding",
            "entry_exit_fees",
            "simulator_slippage",
            "primary_net_pnl_unchanged",
        )
    }
    return {
        "classification": NON_BINDING_DIAGNOSTIC,
        "closed_trade_count": len(components),
        "components": components,
        "totals": totals,
    }


def collect_nonbinding_diagnostics(
    database_path: str | Path, *, phase2_epoch_id: str = "phase2_epoch_006"
) -> dict[str, Any]:
    """Produce deterministic read-only shadow diagnostics for an epoch006 source."""
    db = _read_only_connection(database_path)
    try:
        return {
            "classification": NON_BINDING_DIAGNOSTIC,
            "diagnostic_version": DIAGNOSTIC_VERSION,
            "phase2_epoch_id": phase2_epoch_id,
            "binding_effect": "NONE_NOT_AN_INPUT_TO_PHASE3_ANALYSIS_GATE_V1",
            "hybrid_veto_value": hybrid_veto_value_decomposition(db, phase2_epoch_id),
            "fail_closed_selection": fail_closed_selection_diagnostic(db, phase2_epoch_id),
            "coeligibility_representativeness": coeligibility_representativeness(
                db, phase2_epoch_id
            ),
            "return_component_decomposition": return_component_decomposition(db, phase2_epoch_id),
        }
    finally:
        db.close()
