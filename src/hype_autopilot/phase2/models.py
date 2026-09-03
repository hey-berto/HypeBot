from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hype_autopilot.strategies.base import Decision


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EntryMode(StrEnum):
    NONE = "NONE"
    NOW = "NOW"
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"


class RunnerStatus(StrEnum):
    VALID = "VALID"
    FAIL_CLOSED = "FAIL_CLOSED"


class FailClosedReason(StrEnum):
    NONE = "NONE"
    SNAPSHOT_NOT_SCOREABLE = "SNAPSHOT_NOT_SCOREABLE"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    MALFORMED_JSON = "MALFORMED_JSON"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    SNAPSHOT_HASH_MISMATCH = "SNAPSHOT_HASH_MISMATCH"
    TOOL_INTEGRITY_VIOLATION = "TOOL_INTEGRITY_VIOLATION"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    TIMEOUT = "TIMEOUT"
    API_MODEL_ERROR = "API_MODEL_ERROR"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    INFORMATION_BOUNDARY_VIOLATION = "INFORMATION_BOUNDARY_VIOLATION"
    RESOURCE_BUDGET_EXCEEDED = "RESOURCE_BUDGET_EXCEEDED"


class PriceGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str = "ABSOLUTE_PRICE"
    price: float

    @model_validator(mode="after")
    def validate_price(self) -> PriceGeometry:
        if (
            self.kind != "ABSOLUTE_PRICE"
            or not math.isfinite(self.price)
            or self.price <= 0
        ):
            raise ValueError("geometry requires a positive finite absolute price")
        return self


class EntrySemantics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: EntryMode
    trigger_price: float | None = None

    @model_validator(mode="after")
    def validate_trigger(self) -> EntrySemantics:
        if self.mode in {EntryMode.BREAKOUT, EntryMode.RETEST}:
            if (
                self.trigger_price is None
                or not math.isfinite(self.trigger_price)
                or self.trigger_price <= 0
            ):
                raise ValueError(
                    "conditional entry requires a positive finite trigger price"
                )
        elif self.trigger_price is not None:
            raise ValueError("NOW/NONE entry must not contain a trigger price")
        return self


class Invalidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: str
    reference_price: float | None = None
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_reference(self) -> Invalidation:
        if self.reference_price is not None and (
            not math.isfinite(self.reference_price) or self.reference_price <= 0
        ):
            raise ValueError("invalidation reference must be a positive finite price")
        if not self.category.strip():
            raise ValueError("invalidation category is required")
        return self


class LLMStructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_snapshot_hash: str = Field(min_length=64, max_length=64)
    output_schema_version: str
    decision: Decision
    confidence: Confidence
    rationale_tags: tuple[str, ...] = ()
    bull_case: tuple[str, ...] = ()
    bear_case: tuple[str, ...] = ()
    data_conflicts: tuple[str, ...] = ()
    invocation_reason: str = "SCHEDULED_RESEARCH"
    entry: EntrySemantics
    stop: PriceGeometry | None = None
    target: PriceGeometry | None = None
    invalidation: Invalidation | None = None
    ttl_minutes: int | None = Field(default=None, ge=1, le=10_080)

    @model_validator(mode="after")
    def validate_trade_shape(self) -> LLMStructuredOutput:
        if self.decision == Decision.NO_TRADE:
            if self.entry.mode != EntryMode.NONE or any(
                value is not None
                for value in (
                    self.stop,
                    self.target,
                    self.invalidation,
                    self.ttl_minutes,
                )
            ):
                raise ValueError("NO_TRADE must not contain executable geometry")
        elif (
            self.entry.mode == EntryMode.NONE
            or self.stop is None
            or self.invalidation is None
            or self.ttl_minutes is None
        ):
            raise ValueError(
                "trade decisions require entry, stop, invalidation, and TTL"
            )
        return self


class LLMStructuredOutputV2(LLMStructuredOutput):
    """Versioned transport contract with an API-enforced schema identity."""

    output_schema_version: Literal["LLM_OUTPUT_V2"]


def structured_output_model(version: str) -> type[LLMStructuredOutput]:
    if version == "LLM_OUTPUT_V1":
        return LLMStructuredOutput
    if version == "LLM_OUTPUT_V2":
        return LLMStructuredOutputV2
    raise ValueError(f"unsupported Phase 2 output schema version: {version}")


class ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    raw_output: str
    model: str
    model_version: str
    request_started_at: datetime
    request_ended_at: datetime
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls_count: int = 0


class InvocationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    experiment_id: str
    phase2_epoch_id: str
    input_snapshot_hash: str
    attempt: int
    started_at: datetime
    ended_at: datetime
    raw_output_hash: str | None = None
    provider_status: str
    error_code: str | None = None
    tool_calls_count: int = 0


class LLMDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    experiment_id: str
    phase2_epoch_id: str
    timestamp: datetime
    input_snapshot_hash: str
    model: str
    model_version: str
    prompt_version: str
    output_schema_version: str
    decision: Decision
    confidence: Confidence
    rationale_tags: tuple[str, ...] = ()
    invocation_reason: str
    entry: EntrySemantics
    stop: PriceGeometry | None = None
    target: PriceGeometry | None = None
    invalidation: Invalidation | None = None
    ttl_minutes: int | None = None
    request_started_at: datetime
    request_ended_at: datetime
    snapshot_to_call_age_seconds: float
    latency_ms: int
    retry_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    model_cost_usd: float
    tool_calls_count: int
    tool_integrity_ok: bool
    schema_valid: bool
    geometry_valid: bool
    runner_status: RunnerStatus
    reason_code: FailClosedReason
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def fail_closed(
        cls,
        *,
        experiment_id: str,
        phase2_epoch_id: str,
        timestamp: datetime,
        snapshot_hash: str,
        model: str,
        model_version: str,
        prompt_version: str,
        output_schema_version: str,
        reason: FailClosedReason,
        request_started_at: datetime | None = None,
        request_ended_at: datetime | None = None,
        snapshot_age: float = 0.0,
        retry_count: int = 0,
        tool_calls_count: int = 0,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> LLMDecisionRecord:
        start = request_started_at or datetime.now(UTC)
        end = request_ended_at or start
        return cls(
            experiment_id=experiment_id,
            phase2_epoch_id=phase2_epoch_id,
            timestamp=timestamp,
            input_snapshot_hash=snapshot_hash,
            model=model,
            model_version=model_version,
            prompt_version=prompt_version,
            output_schema_version=output_schema_version,
            decision=Decision.NO_TRADE,
            confidence=Confidence.LOW,
            invocation_reason="SCHEDULED_RESEARCH",
            entry=EntrySemantics(mode=EntryMode.NONE),
            request_started_at=start,
            request_ended_at=end,
            snapshot_to_call_age_seconds=max(0.0, snapshot_age),
            latency_ms=max(0, int((end - start).total_seconds() * 1000)),
            retry_count=retry_count,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            model_cost_usd=cost_usd,
            tool_calls_count=tool_calls_count,
            tool_integrity_ok=tool_calls_count == 0,
            schema_valid=False,
            geometry_valid=False,
            runner_status=RunnerStatus.FAIL_CLOSED,
            reason_code=reason,
            metadata=metadata or {},
        )
