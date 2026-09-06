from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from hype_autopilot.phase3.risk import (
    AccountState,
    RiskDisposition,
    RiskReason,
    RiskReceiptRepository,
    TradeIntent,
    connect_risk_store,
    evaluate_risk,
    load_risk_policy,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "phase3" / "risk_policy_v1.yaml"
NOW = datetime(2026, 9, 6, 1, 2, 3, tzinfo=UTC)


def policy():
    return load_risk_policy(POLICY_PATH)


def intent(**updates):
    payload = {
        "trade_intent_id": "intent-001",
        "timestamp": NOW,
        "strategy_id": "SYNTHETIC_STRATEGY",
        "strategy_version": "SYNTHETIC_V1",
        "snapshot_hash": "a" * 64,
        "decision_hash": "b" * 64,
        "asset": "HYPE",
        "side": "LONG",
        "proposed_notional_usd": 500.0,
        "proposed_leverage": 2.0,
        "entry_price": 50.0,
        "stop_price": 48.0,
        "target_price": 54.0,
        "ttl_seconds": 3600,
    }
    payload.update(updates)
    return TradeIntent(**payload)


def account(**updates):
    payload = {
        "observed_at": NOW - timedelta(seconds=2),
        "equity_usd": 1_000.0,
        "available_margin_usd": 900.0,
        "used_margin_usd": 100.0,
        "asset_position_notional_usd": 0.0,
        "realized_pnl_utc_day_usd": 0.0,
        "drawdown_fraction": 0.0,
        "trades_utc_day": 0,
        "orders_last_minute": 0,
    }
    payload.update(updates)
    return AccountState(**payload)


def evaluate(trade=None, state=None, **updates):
    frozen = policy()
    payload = {
        "supplied_policy_hash": frozen.policy_hash,
        "now": NOW,
        "kill_switch_state": False,
        "protective_exit_available": True,
        "validation_latency_us": 0,
    }
    payload.update(updates)
    return evaluate_risk(trade or intent(), state or account(), frozen, **payload)


def test_same_inputs_and_policy_produce_identical_receipt():
    first = evaluate()
    second = evaluate()
    assert first == second
    assert first.disposition == RiskDisposition.APPROVED
    assert len(first.receipt_hash) == 64
    assert [gate.sequence for gate in first.ordered_gate_results] == list(
        range(1, len(first.ordered_gate_results) + 1)
    )


def test_limits_can_modify_notional_and_leverage_deterministically():
    receipt = evaluate(
        trade=intent(proposed_notional_usd=1_200, proposed_leverage=3),
        state=account(asset_position_notional_usd=200),
    )
    assert receipt.disposition == RiskDisposition.MODIFIED
    assert receipt.allowed_notional_usd == 800
    assert receipt.allowed_leverage == 2
    actions = {gate.reason_code: gate.action for gate in receipt.ordered_gate_results}
    assert actions[RiskReason.MAX_POSITION] == "MODIFY"
    assert actions[RiskReason.MAX_LEVERAGE] == "MODIFY"


def test_every_rejection_is_explainable_from_persisted_inputs():
    receipt = evaluate(
        state=account(
            observed_at=NOW - timedelta(seconds=30),
            realized_pnl_utc_day_usd=-100,
            drawdown_fraction=0.10,
            trades_utc_day=12,
            orders_last_minute=6,
        ),
        protective_exit_available=False,
    )
    assert receipt.disposition == RiskDisposition.REJECTED
    assert receipt.allowed_notional_usd == receipt.allowed_leverage == 0
    assert set(receipt.rejection_reason_codes) == {
        RiskReason.STALE_DATA,
        RiskReason.DAILY_LOSS_LIMIT,
        RiskReason.MAX_DRAWDOWN,
        RiskReason.MAX_TRADES_DAY,
        RiskReason.ORDER_RATE_LIMIT,
        RiskReason.PROTECTIVE_EXIT_UNAVAILABLE,
    }
    rejected_gates = {
        gate.reason_code: gate
        for gate in receipt.ordered_gate_results
        if gate.action == "REJECT"
    }
    assert set(rejected_gates) == set(receipt.rejection_reason_codes)


def test_policy_hash_mismatch_and_default_kill_switch_fail_closed():
    frozen = policy()
    receipt = evaluate_risk(
        intent(),
        account(),
        frozen,
        supplied_policy_hash="0" * 64,
        now=NOW,
        protective_exit_available=True,
    )
    assert receipt.disposition == RiskDisposition.REJECTED
    assert RiskReason.POLICY_HASH_MISMATCH in receipt.rejection_reason_codes
    assert RiskReason.KILL_SWITCH in receipt.rejection_reason_codes


def test_missing_and_malformed_account_state_fail_closed():
    missing = evaluate_risk(
        intent(),
        None,
        policy(),
        supplied_policy_hash=policy().policy_hash,
        now=NOW,
        kill_switch_state=False,
        protective_exit_available=True,
    )
    malformed = evaluate_risk(
        intent(),
        {"observed_at": NOW, "equity_usd": -1},
        policy(),
        supplied_policy_hash=policy().policy_hash,
        now=NOW,
        kill_switch_state=False,
        protective_exit_available=True,
    )
    assert missing.rejection_reason_codes[0] == RiskReason.MISSING_ACCOUNT_STATE
    assert malformed.rejection_reason_codes[0] == RiskReason.MALFORMED_ACCOUNT_STATE


def test_llm_cannot_add_an_override_or_bypass_field():
    with pytest.raises(ValidationError):
        intent(llm_override=True)
    signature = inspect.signature(evaluate_risk)
    assert "override" not in signature.parameters
    assert "llm" not in signature.parameters


def test_risk_policy_and_engine_have_no_wallet_withdrawal_or_order_capability():
    frozen = policy()
    assert frozen.network_access is False
    assert frozen.wallet_access is False
    assert frozen.withdrawal_capability is False
    assert frozen.order_submission_capability is False
    public = " ".join(name.lower() for name in dir(RiskReceiptRepository))
    assert "withdraw" not in public
    assert "submit_order" not in public


def test_risk_receipt_store_is_isolated_append_only_and_hash_checked(tmp_path):
    root = tmp_path / "phase3"
    database = root / "data" / "phase3_risk" / "risk.sqlite3"
    db = connect_risk_store(database, root)
    repository = RiskReceiptRepository(db)
    frozen = policy()
    repository.register_policy(frozen)
    receipt = evaluate()
    repository.save_receipt(receipt)
    assert repository.load_receipt(receipt.receipt_hash) == receipt
    assert repository.integrity() == ("ok", 0)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE risk_receipts SET disposition='APPROVED'")
    db.rollback()
    altered = receipt.model_copy(update={"validation_latency_us": 1})
    with pytest.raises(ValueError, match="receipt hash"):
        repository.save_receipt(altered)
    with pytest.raises(ValueError, match="production evidence"):
        connect_risk_store(
            root / "data" / "phase3_risk" / "phase2_epoch_002.sqlite3", root
        )
    db.close()


def test_invalid_geometry_fails_before_risk_evaluation():
    with pytest.raises(ValidationError, match="stop < entry < target"):
        intent(stop_price=51)
