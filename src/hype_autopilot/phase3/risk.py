from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hype_autopilot.hashing import canonical_json, sha256_canonical


class RiskDisposition(StrEnum):
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"


class GateAction(StrEnum):
    PASS = "PASS"
    MODIFY = "MODIFY"
    REJECT = "REJECT"


class RiskReason(StrEnum):
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    MISSING_ACCOUNT_STATE = "MISSING_ACCOUNT_STATE"
    MALFORMED_ACCOUNT_STATE = "MALFORMED_ACCOUNT_STATE"
    ASSET_NOT_ALLOWED = "ASSET_NOT_ALLOWED"
    KILL_SWITCH = "KILL_SWITCH"
    MAX_POSITION = "MAX_POSITION"
    MAX_LEVERAGE = "MAX_LEVERAGE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    MAX_TRADES_DAY = "MAX_TRADES_DAY"
    STALE_DATA = "STALE_DATA"
    MARGIN_LIMIT = "MARGIN_LIMIT"
    PROTECTIVE_EXIT_UNAVAILABLE = "PROTECTIVE_EXIT_UNAVAILABLE"
    ORDER_RATE_LIMIT = "ORDER_RATE_LIMIT"


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    policy_version: str
    asset_allowlist: tuple[str, ...]
    max_position_notional_usd: float = Field(gt=0)
    max_leverage: float = Field(gt=0)
    daily_loss_limit_usd: float = Field(gt=0)
    max_drawdown_fraction: float = Field(gt=0, lt=1)
    max_trades_per_utc_day: int = Field(gt=0)
    maximum_account_state_age_seconds: int = Field(gt=0)
    maximum_margin_usage_fraction: float = Field(gt=0, le=1)
    protective_exit_required: bool
    maximum_orders_per_minute: int = Field(gt=0)
    kill_switch_default: bool
    network_access: Literal[False]
    wallet_access: Literal[False]
    withdrawal_capability: Literal[False]
    order_submission_capability: Literal[False]

    @property
    def policy_hash(self) -> str:
        return sha256_canonical(self)


def load_risk_policy(path: str | Path) -> RiskPolicy:
    return RiskPolicy.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )


class TradeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_intent_id: str
    timestamp: datetime
    strategy_id: str
    strategy_version: str
    snapshot_hash: str | None = None
    decision_hash: str | None = None
    asset: str
    side: Literal["LONG", "SHORT"]
    proposed_notional_usd: float = Field(gt=0)
    proposed_leverage: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    ttl_seconds: int = Field(gt=0)
    research_metadata: Mapping[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_geometry(self) -> TradeIntent:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("trade intent timestamp must be UTC")
        if self.side == "LONG" and not (
            self.stop_price < self.entry_price < self.target_price
        ):
            raise ValueError("LONG geometry requires stop < entry < target")
        if self.side == "SHORT" and not (
            self.target_price < self.entry_price < self.stop_price
        ):
            raise ValueError("SHORT geometry requires target < entry < stop")
        return self


class AccountState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    equity_usd: float = Field(gt=0)
    available_margin_usd: float = Field(ge=0)
    used_margin_usd: float = Field(ge=0)
    asset_position_notional_usd: float = Field(ge=0)
    realized_pnl_utc_day_usd: float
    drawdown_fraction: float = Field(ge=0)
    trades_utc_day: int = Field(ge=0)
    orders_last_minute: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_account_state(self) -> AccountState:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(
            0
        ):
            raise ValueError("account state timestamp must be UTC")
        numeric = self.model_dump(mode="python", exclude={"observed_at"})
        if any(not math.isfinite(float(value)) for value in numeric.values()):
            raise ValueError("account state values must be finite")
        if self.used_margin_usd > self.equity_usd:
            raise ValueError("used margin cannot exceed equity")
        if self.available_margin_usd > self.equity_usd:
            raise ValueError("available margin cannot exceed equity")
        return self


class RiskGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int
    reason_code: RiskReason
    action: GateAction
    observed_value: float | bool | str | None
    limit_value: float | bool | str | None
    resulting_notional_usd: float
    resulting_leverage: float


class RiskReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_version: Literal["RISK_RECEIPT_V1"] = "RISK_RECEIPT_V1"
    trade_intent_id: str
    timestamp: datetime
    strategy_id: str
    strategy_version: str
    originating_snapshot_hash: str | None
    originating_decision_hash: str | None
    asset: str
    side: str
    proposed_notional_usd: float
    proposed_leverage: float
    entry_price: float
    stop_price: float
    target_price: float
    ttl_seconds: int
    account_state_snapshot_hash: str
    risk_policy_id: str
    risk_policy_version: str
    risk_policy_hash: str
    ordered_gate_results: tuple[RiskGateResult, ...]
    disposition: RiskDisposition
    allowed_notional_usd: float
    allowed_leverage: float
    rejection_reason_codes: tuple[RiskReason, ...]
    kill_switch_state: bool
    stale_or_missing_data_state: str
    validation_latency_us: int = Field(ge=0)
    receipt_hash: str


def _gate(
    sequence: int,
    reason: RiskReason,
    action: GateAction,
    observed: float | bool | str | None,
    limit: float | bool | str | None,
    notional: float,
    leverage: float,
) -> RiskGateResult:
    return RiskGateResult(
        sequence=sequence,
        reason_code=reason,
        action=action,
        observed_value=observed,
        limit_value=limit,
        resulting_notional_usd=max(0.0, notional),
        resulting_leverage=max(0.0, leverage),
    )


def _invalid_account_hash(account_state: object) -> str:
    try:
        return sha256_canonical({"invalid_account_state": account_state})
    except (TypeError, ValueError):
        return sha256_canonical({"invalid_account_state": type(account_state).__name__})


def evaluate_risk(
    intent: TradeIntent,
    account_state: AccountState | Mapping[str, Any] | None,
    policy: RiskPolicy,
    *,
    supplied_policy_hash: str,
    now: datetime,
    kill_switch_state: bool | None = None,
    protective_exit_available: bool,
    validation_latency_us: int = 0,
) -> RiskReceipt:
    """Pure fail-closed risk evaluation. It has no network, wallet or order path."""
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("risk evaluation timestamp must be UTC")
    kill_switch = (
        policy.kill_switch_default if kill_switch_state is None else kill_switch_state
    )
    gates: list[RiskGateResult] = []
    allowed_notional = intent.proposed_notional_usd
    allowed_leverage = intent.proposed_leverage

    def add(
        reason: RiskReason,
        action: GateAction,
        observed: float | bool | str | None,
        limit: float | bool | str | None,
    ) -> None:
        gates.append(
            _gate(
                len(gates) + 1,
                reason,
                action,
                observed,
                limit,
                allowed_notional,
                allowed_leverage,
            )
        )

    if supplied_policy_hash != policy.policy_hash:
        add(
            RiskReason.POLICY_HASH_MISMATCH,
            GateAction.REJECT,
            supplied_policy_hash,
            policy.policy_hash,
        )

    parsed_account: AccountState | None = None
    account_hash: str
    data_state = "CURRENT"
    if account_state is None:
        account_hash = _invalid_account_hash(None)
        data_state = "MISSING"
        add(RiskReason.MISSING_ACCOUNT_STATE, GateAction.REJECT, "MISSING", "REQUIRED")
    else:
        try:
            parsed_account = (
                account_state
                if isinstance(account_state, AccountState)
                else AccountState.model_validate(account_state)
            )
            account_hash = sha256_canonical(parsed_account)
        except ValidationError:
            account_hash = _invalid_account_hash(account_state)
            data_state = "MALFORMED"
            add(
                RiskReason.MALFORMED_ACCOUNT_STATE,
                GateAction.REJECT,
                "MALFORMED",
                "VALID_ACCOUNT_STATE_REQUIRED",
            )

    add(
        RiskReason.ASSET_NOT_ALLOWED,
        GateAction.PASS
        if intent.asset in policy.asset_allowlist
        else GateAction.REJECT,
        intent.asset,
        ",".join(policy.asset_allowlist),
    )
    add(
        RiskReason.KILL_SWITCH,
        GateAction.REJECT if kill_switch else GateAction.PASS,
        kill_switch,
        False,
    )

    if parsed_account is not None:
        age = (now - parsed_account.observed_at).total_seconds()
        stale = age < 0 or age > policy.maximum_account_state_age_seconds
        if stale:
            data_state = "STALE"
        add(
            RiskReason.STALE_DATA,
            GateAction.REJECT if stale else GateAction.PASS,
            age,
            float(policy.maximum_account_state_age_seconds),
        )
        if allowed_leverage > policy.max_leverage:
            allowed_leverage = policy.max_leverage
            add(
                RiskReason.MAX_LEVERAGE,
                GateAction.MODIFY,
                intent.proposed_leverage,
                policy.max_leverage,
            )
        else:
            add(
                RiskReason.MAX_LEVERAGE,
                GateAction.PASS,
                allowed_leverage,
                policy.max_leverage,
            )
        remaining_position = max(
            0.0,
            policy.max_position_notional_usd
            - parsed_account.asset_position_notional_usd,
        )
        if allowed_notional > remaining_position:
            allowed_notional = remaining_position
            add(
                RiskReason.MAX_POSITION,
                GateAction.MODIFY if allowed_notional > 0 else GateAction.REJECT,
                intent.proposed_notional_usd
                + parsed_account.asset_position_notional_usd,
                policy.max_position_notional_usd,
            )
        else:
            add(
                RiskReason.MAX_POSITION,
                GateAction.PASS,
                allowed_notional + parsed_account.asset_position_notional_usd,
                policy.max_position_notional_usd,
            )
        add(
            RiskReason.DAILY_LOSS_LIMIT,
            GateAction.REJECT
            if parsed_account.realized_pnl_utc_day_usd <= -policy.daily_loss_limit_usd
            else GateAction.PASS,
            parsed_account.realized_pnl_utc_day_usd,
            -policy.daily_loss_limit_usd,
        )
        add(
            RiskReason.MAX_DRAWDOWN,
            GateAction.REJECT
            if parsed_account.drawdown_fraction >= policy.max_drawdown_fraction
            else GateAction.PASS,
            parsed_account.drawdown_fraction,
            policy.max_drawdown_fraction,
        )
        add(
            RiskReason.MAX_TRADES_DAY,
            GateAction.REJECT
            if parsed_account.trades_utc_day >= policy.max_trades_per_utc_day
            else GateAction.PASS,
            float(parsed_account.trades_utc_day),
            float(policy.max_trades_per_utc_day),
        )
        required_margin = (
            allowed_notional / allowed_leverage if allowed_leverage else math.inf
        )
        projected_margin_fraction = (
            parsed_account.used_margin_usd + required_margin
        ) / parsed_account.equity_usd
        margin_failed = (
            required_margin > parsed_account.available_margin_usd
            or projected_margin_fraction > policy.maximum_margin_usage_fraction
        )
        add(
            RiskReason.MARGIN_LIMIT,
            GateAction.REJECT if margin_failed else GateAction.PASS,
            projected_margin_fraction,
            policy.maximum_margin_usage_fraction,
        )
        add(
            RiskReason.ORDER_RATE_LIMIT,
            GateAction.REJECT
            if parsed_account.orders_last_minute >= policy.maximum_orders_per_minute
            else GateAction.PASS,
            float(parsed_account.orders_last_minute),
            float(policy.maximum_orders_per_minute),
        )
    add(
        RiskReason.PROTECTIVE_EXIT_UNAVAILABLE,
        GateAction.REJECT
        if policy.protective_exit_required and not protective_exit_available
        else GateAction.PASS,
        protective_exit_available,
        policy.protective_exit_required,
    )

    rejected = tuple(
        gate.reason_code for gate in gates if gate.action == GateAction.REJECT
    )
    if rejected:
        disposition = RiskDisposition.REJECTED
        allowed_notional = 0.0
        allowed_leverage = 0.0
    elif any(gate.action == GateAction.MODIFY for gate in gates):
        disposition = RiskDisposition.MODIFIED
    else:
        disposition = RiskDisposition.APPROVED
    base = {
        "receipt_version": "RISK_RECEIPT_V1",
        "trade_intent_id": intent.trade_intent_id,
        "timestamp": intent.timestamp,
        "strategy_id": intent.strategy_id,
        "strategy_version": intent.strategy_version,
        "originating_snapshot_hash": intent.snapshot_hash,
        "originating_decision_hash": intent.decision_hash,
        "asset": intent.asset,
        "side": intent.side,
        "proposed_notional_usd": intent.proposed_notional_usd,
        "proposed_leverage": intent.proposed_leverage,
        "entry_price": intent.entry_price,
        "stop_price": intent.stop_price,
        "target_price": intent.target_price,
        "ttl_seconds": intent.ttl_seconds,
        "account_state_snapshot_hash": account_hash,
        "risk_policy_id": policy.policy_id,
        "risk_policy_version": policy.policy_version,
        "risk_policy_hash": policy.policy_hash,
        "ordered_gate_results": tuple(gates),
        "disposition": disposition,
        "allowed_notional_usd": allowed_notional,
        "allowed_leverage": allowed_leverage,
        "rejection_reason_codes": rejected,
        "kill_switch_state": kill_switch,
        "stale_or_missing_data_state": data_state,
        "validation_latency_us": validation_latency_us,
    }
    return RiskReceipt(**base, receipt_hash=sha256_canonical(base))


RISK_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_policies (
  policy_hash TEXT PRIMARY KEY, policy_id TEXT NOT NULL, policy_version TEXT NOT NULL,
  payload_json TEXT NOT NULL, UNIQUE(policy_id, policy_version)
);
CREATE TABLE IF NOT EXISTS risk_receipts (
  receipt_hash TEXT PRIMARY KEY, trade_intent_id TEXT NOT NULL,
  timestamp TEXT NOT NULL, disposition TEXT NOT NULL,
  risk_policy_hash TEXT NOT NULL REFERENCES risk_policies(policy_hash),
  account_state_snapshot_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(trade_intent_id, risk_policy_hash)
);
CREATE TRIGGER IF NOT EXISTS immutable_risk_policies_update BEFORE UPDATE ON risk_policies
BEGIN SELECT RAISE(ABORT, 'risk policies are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_risk_policies_delete BEFORE DELETE ON risk_policies
BEGIN SELECT RAISE(ABORT, 'risk policies are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_risk_receipts_update BEFORE UPDATE ON risk_receipts
BEGIN SELECT RAISE(ABORT, 'risk receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_risk_receipts_delete BEFORE DELETE ON risk_receipts
BEGIN SELECT RAISE(ABORT, 'risk receipts are immutable'); END;
"""


def risk_schema_hash() -> str:
    return sha256_canonical({"schema": RISK_SCHEMA})


def connect_risk_store(
    path: str | Path, isolated_root: str | Path
) -> sqlite3.Connection:
    root = Path(isolated_root).resolve()
    required_root = (root / "data" / "phase3_risk").resolve()
    resolved = Path(path).resolve()
    if required_root != resolved.parent and required_root not in resolved.parents:
        raise ValueError("Risk Receipt database must remain under data/phase3_risk")
    lowered = str(resolved).lower()
    if "phase2_epoch" in lowered or "epoch_001.sqlite3" in lowered:
        raise ValueError("production evidence databases are prohibited")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(resolved)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(RISK_SCHEMA)
    db.commit()
    return db


class RiskReceiptRepository:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def register_policy(self, policy: RiskPolicy) -> None:
        payload = canonical_json(policy)
        try:
            self.db.execute(
                "INSERT INTO risk_policies VALUES (?,?,?,?)",
                (policy.policy_hash, policy.policy_id, policy.policy_version, payload),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json FROM risk_policies WHERE policy_id=? AND policy_version=?",
                (policy.policy_id, policy.policy_version),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise RuntimeError("immutable risk policy conflict")

    def save_receipt(self, receipt: RiskReceipt) -> None:
        receipt_fields = receipt.model_dump(mode="python")
        claimed_hash = str(receipt_fields.pop("receipt_hash"))
        if sha256_canonical(receipt_fields) != claimed_hash:
            raise ValueError("risk receipt hash does not match its payload")
        payload = canonical_json(receipt)
        try:
            self.db.execute(
                "INSERT INTO risk_receipts VALUES (?,?,?,?,?,?,?)",
                (
                    receipt.receipt_hash,
                    receipt.trade_intent_id,
                    receipt.timestamp.astimezone(UTC).isoformat(),
                    receipt.disposition.value,
                    receipt.risk_policy_hash,
                    receipt.account_state_snapshot_hash,
                    payload,
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json FROM risk_receipts WHERE trade_intent_id=? AND risk_policy_hash=?",
                (receipt.trade_intent_id, receipt.risk_policy_hash),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise RuntimeError("immutable risk receipt conflict")

    def load_receipt(self, receipt_hash: str) -> RiskReceipt | None:
        row = self.db.execute(
            "SELECT payload_json FROM risk_receipts WHERE receipt_hash=?",
            (receipt_hash,),
        ).fetchone()
        if not row:
            return None
        receipt = RiskReceipt.model_validate(json.loads(row["payload_json"]))
        receipt_fields = receipt.model_dump(mode="python")
        claimed_hash = str(receipt_fields.pop("receipt_hash"))
        if (
            claimed_hash != receipt_hash
            or sha256_canonical(receipt_fields) != claimed_hash
        ):
            raise RuntimeError("persisted risk receipt hash verification failed")
        return receipt

    def integrity(self) -> tuple[str, int]:
        return (
            str(self.db.execute("PRAGMA integrity_check").fetchone()[0]),
            len(self.db.execute("PRAGMA foreign_key_check").fetchall()),
        )
