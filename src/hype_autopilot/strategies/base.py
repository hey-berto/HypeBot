from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from hype_autopilot.snapshots.models import DecisionSnapshot


class Decision(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class StrategyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision_id: str
    snapshot_hash: str
    strategy_id: str
    strategy_version: str
    decision: Decision
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entry_mode: str = "AFTER_LATENCY"
    entry_reference: float | None = None
    stop_reference: float | None = None
    target_reference: float | None = None
    trade_ttl_minutes: int
    reason_codes: tuple[str, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)


class Strategy(Protocol):
    def evaluate(self, snapshot: DecisionSnapshot) -> StrategyDecision: ...


def require_scored(snapshot: DecisionSnapshot) -> str:
    if not snapshot.snapshot_hash:
        raise ValueError("strategy evaluation requires a frozen snapshot hash")
    return snapshot.snapshot_hash


def deterministic_decision_id(snapshot_hash: str, strategy_id: str, strategy_version: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{snapshot_hash}:{strategy_id}:{strategy_version}"))
