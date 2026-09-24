from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hype_autopilot.phase2.storage import Phase2Repository
from hype_autopilot.phase3.nonbinding_diagnostics import (
    NON_BINDING_DIAGNOSTIC,
    collect_nonbinding_diagnostics,
)


def _snapshot(at: datetime, regime: str, volatility: float) -> str:
    trend, volatility_regime = regime.split("_", 1)
    return json.dumps(
        {
            "market": {
                "hype_features": {
                    "realised_vol_1h": volatility,
                    "atr_pct_1h": volatility / 2,
                    "funding_rate": 0.0001,
                    "funding_zscore": 0.5,
                }
            },
            "regime": {
                "combined": regime,
                "trend": trend,
                "volatility": volatility_regime,
            },
        }
    )


def _fixture_database(tmp_path: Path) -> Path:
    database = tmp_path / "phase2_epoch_006.sqlite3"
    db = sqlite3.connect(database)
    Phase2Repository(db).initialize()
    start = datetime(2026, 9, 24, 0, 15, tzinfo=UTC)
    for index, (regime, volatility) in enumerate(
        (("UP_HIGH", 0.04), ("DOWN_LOW", 0.01), ("UP_HIGH", 0.03))
    ):
        at = start + timedelta(minutes=15 * index)
        snapshot_hash = f"snapshot-{index}"
        db.execute(
            "INSERT INTO decision_snapshots VALUES (?,?,?,?,?,?,?,?)",
            (
                snapshot_hash,
                f"snapshot-id-{index}",
                at.isoformat(),
                "phase2_epoch_006",
                "SCORED_PROSPECTIVE",
                1,
                _snapshot(at, regime, volatility),
                at.isoformat(),
            ),
        )
        quant = "LONG" if index < 2 else "SHORT"
        hybrid = "LONG" if index == 0 else "NO_TRADE"
        llm_status = "FAIL_CLOSED" if index == 1 else "VALID"
        for strategy, decision in (
            ("QUANT_TREND", quant),
            ("HYBRID_TREND_LLM_V1", hybrid),
        ):
            db.execute(
                "INSERT INTO strategy_decisions "
                "(decision_id,snapshot_hash,strategy_id,strategy_version,decision,payload_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    f"{strategy}-{index}",
                    snapshot_hash,
                    strategy,
                    f"{strategy}_V1",
                    decision,
                    "{}",
                ),
            )
        db.execute(
            "INSERT INTO llm_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"llm-{index}",
                "phase2_epoch_006",
                "phase2_epoch_006",
                snapshot_hash,
                "LLM_V1",
                "NO_TRADE",
                llm_status,
                "TIMEOUT" if llm_status == "FAIL_CLOSED" else "NONE",
                at.isoformat(),
                "{}",
                f"llm-integrity-{index}",
            ),
        )
        db.execute(
            "INSERT INTO phase2_pair_outcomes VALUES (?,?,?,?,?,?)",
            (
                "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1",
                snapshot_hash,
                "CO_ELIGIBLE" if index != 1 else "NOT_CO_ELIGIBLE",
                "COMPLETE" if index != 1 else "EXCLUDED",
                "{}",
                f"pair-integrity-{index}",
            ),
        )
    db.execute(
        "INSERT INTO paper_trades "
        "(paper_trade_id,strategy_decision_id,strategy_id,snapshot_hash,direction,signal_time,"
        "exit_time,fees,slippage_cost,funding_cost,gross_pnl,net_pnl,status,last_processed_at,"
        "flags_json,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "closed-fixture-trade",
            "QUANT_TREND-0",
            "QUANT_TREND",
            "snapshot-0",
            "LONG",
            start.isoformat(),
            (start + timedelta(minutes=15)).isoformat(),
            1.0,
            0.5,
            0.25,
            10.0,
            8.25,
            "CLOSED",
            (start + timedelta(minutes=15)).isoformat(),
            "[]",
            "{}",
        ),
    )
    db.commit()
    db.close()
    return database


def test_nonbinding_diagnostics_are_read_only_and_deterministic(tmp_path):
    database = _fixture_database(tmp_path)
    before = database.read_bytes()
    first = collect_nonbinding_diagnostics(database)
    second = collect_nonbinding_diagnostics(database)

    assert database.read_bytes() == before
    assert first == second
    assert first["classification"] == NON_BINDING_DIAGNOSTIC
    assert first["binding_effect"] == "NONE_NOT_AN_INPUT_TO_PHASE3_ANALYSIS_GATE_V1"
    hybrid = first["hybrid_veto_value"]["strategies"]["HYBRID_TREND_LLM_V1"]
    assert first["hybrid_veto_value"]["classification"] == NON_BINDING_DIAGNOSTIC
    assert hybrid["outcome_accessed"] is False
    assert sum(item["hybrid_retained_trades"] for item in hybrid["strata"]) == sum(
        item["matched_control_retained_trades"] for item in hybrid["strata"]
    )
    selection = first["fail_closed_selection"]
    assert selection["statuses"]["VALID"]["count"] == 2
    assert selection["statuses"]["FAIL_CLOSED"]["reason_counts"] == {"TIMEOUT": 1}
    representation = first["coeligibility_representativeness"]["pairs"][
        "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1"
    ]
    assert representation["all_scored_snapshot_count"] == 3
    assert representation["coeligible_count"] == 2
    assert first["return_component_decomposition"]["closed_trade_count"] == 1


def test_nonbinding_diagnostics_require_a_readable_source(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        collect_nonbinding_diagnostics(tmp_path / "absent.sqlite3")
