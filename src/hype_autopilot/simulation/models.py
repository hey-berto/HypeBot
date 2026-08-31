from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ExitReason(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    TTL = "TTL"
    NO_ENTRY = "NO_ENTRY"


class TradeStatus(StrEnum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    SUPPRESSED_POSITION_OPEN = "SUPPRESSED_POSITION_OPEN"
    SUPPRESSED_INVALID_TARGET_AFTER_LATENCY = "SUPPRESSED_INVALID_TARGET_AFTER_LATENCY"
    SUPPRESSED_NO_ENTRY_DATA = "SUPPRESSED_NO_ENTRY_DATA"


class PaperTrade(BaseModel):
    model_config = ConfigDict(frozen=True)
    paper_trade_id: str
    strategy_decision_id: str
    strategy_id: str
    snapshot_hash: str
    direction: str
    signal_time: datetime
    entry_time: datetime | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    current_stop_price: float | None = None
    highest_price: float | None = None
    lowest_price: float | None = None
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    fees: float = 0.0
    slippage_cost: float = 0.0
    funding_cost: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    r_multiple: float | None = None
    status: TradeStatus
    last_processed_at: datetime
    flags: tuple[str, ...] = ()
