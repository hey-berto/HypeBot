import json
import sqlite3
from datetime import timedelta

import pytest

from hype_autopilot.phase2.storage import Phase2Repository
from hype_autopilot.phase3.right_censoring_shadow import (
    CLASSIFICATION,
    EPOCH006_CUTOFF,
    CutoffPosition,
    FrozenExitEconomics,
    build_right_censoring_shadow,
    collect_right_censoring_shadow,
)

ECONOMICS = FrozenExitEconomics(fee_bps_per_side=10.0, slippage_bps_per_side=20.0)
PRIMARY = {"project_verdict": "INCONCLUSIVE", "pair_reports": {"pair": "unchanged"}}


def _open(identifier: str, *, direction: str = "LONG", entry: float = 100.0, funding: float = 0.0) -> CutoffPosition:
    return CutoffPosition(
        paper_trade_id=identifier, strategy_id="QUANT_TREND", direction=direction,
        signal_time=EPOCH006_CUTOFF - timedelta(hours=2),
        entry_time=EPOCH006_CUTOFF - timedelta(hours=1), entry_price=entry,
        fees=0.1, slippage_cost=0.2, funding_cost=funding, status_at_cutoff="OPEN", regime="UP_HIGH",
    )


def _pending(identifier: str) -> CutoffPosition:
    return CutoffPosition(
        paper_trade_id=identifier, strategy_id="LLM_V1", direction="LONG",
        signal_time=EPOCH006_CUTOFF - timedelta(minutes=5), entry_time=None, entry_price=None,
        fees=0.0, slippage_cost=0.0, funding_cost=0.0, status_at_cutoff="PENDING_ENTRY", regime="DOWN_LOW",
    )


def test_no_open_positions_and_pending_never_receives_a_return():
    report = build_right_censoring_shadow([_pending("pending")], cutoff_price=None, economics=ECONOMICS, primary_phase3_result=PRIMARY)
    shadow = report[CLASSIFICATION]
    assert shadow["open_position_count"] == 0
    assert shadow["pending_position_count"] == 1
    assert shadow["pending_positions"][0]["treatment"] == "NO_ENTRY_NO_TERMINAL_RETURN_INVENTED"
    assert report["PRIMARY_PHASE3_RESULT"]["result"] == PRIMARY


def test_open_winner_loser_frozen_friction_funding_and_duration_are_deterministic():
    winner = _open("winner", entry=100.0, funding=0.3)
    loser = _open("loser", direction="SHORT", entry=100.0, funding=-0.2)
    first = build_right_censoring_shadow([winner, loser], cutoff_price=110.0, economics=ECONOMICS, primary_phase3_result=PRIMARY)
    second = build_right_censoring_shadow([winner, loser], cutoff_price=110.0, economics=ECONOMICS, primary_phase3_result=PRIMARY)
    assert first == second
    values = first[CLASSIFICATION]["open_position_valuations"]
    assert values[0]["shadow_exit_price"] == pytest.approx(109.78)
    assert values[0]["shadow_exit_fee"] == pytest.approx(0.10978)
    assert values[0]["already_accrued_funding_cost"] == 0.3
    assert values[0]["unrealized_pnl_sign"] == "WIN"
    assert values[1]["unrealized_pnl_sign"] == "LOSS"
    assert all(value["position_age_seconds"] == 3600 for value in values)
    assert first["PRIMARY_PHASE3_RESULT"]["binding_effect"] == "NONE_NO_PROMOTE_REJECT_INCONCLUSIVE_CHANGE"


def test_exact_cutoff_is_required_and_open_needs_price():
    with pytest.raises(ValueError, match="fixed to the Day-42 cutoff"):
        build_right_censoring_shadow([], cutoff_price=None, economics=ECONOMICS, primary_phase3_result=PRIMARY, cutoff=EPOCH006_CUTOFF + timedelta(seconds=1))
    with pytest.raises(ValueError, match="cutoff price"):
        build_right_censoring_shadow([_open("missing-price")], cutoff_price=None, economics=ECONOMICS, primary_phase3_result=PRIMARY)


def _reader_fixture(path):
    db = sqlite3.connect(path)
    Phase2Repository(db).initialize()
    snapshot = "snapshot"
    db.execute(
        "INSERT INTO decision_snapshots VALUES (?,?,?,?,?,?,?,?)",
        (snapshot, "snapshot-id", EPOCH006_CUTOFF.isoformat(), "phase2_epoch_006", "SCORED_PROSPECTIVE", 1,
         json.dumps({"regime": {"combined": "UP_HIGH"}}), EPOCH006_CUTOFF.isoformat()),
    )
    db.execute(
        "INSERT INTO strategy_decisions (decision_id,snapshot_hash,strategy_id,strategy_version,decision,payload_json) VALUES (?,?,?,?,?,?)",
        ("decision", snapshot, "QUANT_TREND", "QUANT_TREND_V1", "LONG", "{}"),
    )
    db.execute(
        "INSERT INTO paper_trades (paper_trade_id,strategy_decision_id,strategy_id,snapshot_hash,direction,signal_time,entry_time,entry_price,fees,slippage_cost,funding_cost,status,last_processed_at,flags_json,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("open", "decision", "QUANT_TREND", snapshot, "LONG", (EPOCH006_CUTOFF - timedelta(hours=2)).isoformat(),
         (EPOCH006_CUTOFF - timedelta(hours=1)).isoformat(), 100.0, 0.1, 0.2, 0.25, "OPEN", EPOCH006_CUTOFF.isoformat(), "[]", "{}"),
    )
    db.execute(
        "INSERT INTO raw_candles (symbol,interval,open_time,close_time,open,high,low,close,volume,received_at,observation_class,content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("HYPE", "1m", (EPOCH006_CUTOFF - timedelta(minutes=1)).isoformat(), EPOCH006_CUTOFF.isoformat(), 110.0, 110.0, 110.0, 110.0, 1.0, EPOCH006_CUTOFF.isoformat(), "SCORED_PROSPECTIVE", "candle"),
    )
    db.commit()
    db.close()


def test_read_only_reader_uses_evidence_available_cutoff_price_without_mutating(tmp_path):
    database = tmp_path / "synthetic.sqlite3"
    _reader_fixture(database)
    before = database.read_bytes()
    report = collect_right_censoring_shadow(database, economics=ECONOMICS, primary_phase3_result=PRIMARY)
    assert database.read_bytes() == before
    assert report["classification"] == CLASSIFICATION
    assert report[CLASSIFICATION]["cutoff_raw_price"] == 110.0
    assert report[CLASSIFICATION]["open_position_count"] == 1
