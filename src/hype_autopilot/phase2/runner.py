from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import ValidationError

from hype_autopilot.hashing import canonical_json
from hype_autopilot.phase2.audit import (
    SensitiveCredentialMaterial,
    validate_raw_provider_plaintext,
)
from hype_autopilot.phase2.config import Phase2Config
from hype_autopilot.phase2.models import (
    EntryMode,
    FailClosedReason,
    InvocationAttempt,
    LLMDecisionRecord,
    LLMStructuredOutput,
    RunnerStatus,
    structured_output_model,
)
from hype_autopilot.phase2.provider import LLMProvider, ProviderError, ProviderTimeout
from hype_autopilot.phase2.resources import Phase2ResourceGuard, ResourceBudgetExceeded
from hype_autopilot.phase2.storage import Phase2Repository
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from hype_autopilot.snapshots.models import DecisionSnapshot
from hype_autopilot.strategies.base import (
    Decision,
    StrategyDecision,
    deterministic_decision_id,
)


class GeometryViolation(ValueError):
    pass


def _reference_price(output: LLMStructuredOutput, snapshot: DecisionSnapshot) -> float:
    if output.entry.mode in {EntryMode.BREAKOUT, EntryMode.RETEST}:
        assert output.entry.trigger_price is not None
        return output.entry.trigger_price
    context = snapshot.market.hype_context
    if context is None:
        raise GeometryViolation("NOW entry requires snapshot market context")
    reference = context.mark_price or context.mid_price
    if reference is None or reference <= 0:
        raise GeometryViolation(
            "NOW entry requires a positive snapshot reference price"
        )
    return float(reference)


def validate_geometry(output: LLMStructuredOutput, snapshot: DecisionSnapshot) -> None:
    if output.decision == Decision.NO_TRADE:
        return
    reference = _reference_price(output, snapshot)
    assert output.stop is not None
    stop = output.stop.price
    target = output.target.price if output.target else None
    if output.decision == Decision.LONG:
        if stop >= reference or (target is not None and target <= reference):
            raise GeometryViolation(
                "LONG requires stop below and target above the entry reference"
            )
    elif stop <= reference or (target is not None and target >= reference):
        raise GeometryViolation(
            "SHORT requires stop above and target below the entry reference"
        )


class FailClosedLLMRunner:
    def __init__(
        self,
        *,
        config: Phase2Config,
        provider: LLMProvider,
        repository: Phase2Repository,
        prompt: str,
        experiment_id: str,
        clock: Callable[[], datetime] | None = None,
        resource_guard: Phase2ResourceGuard | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.repository = repository
        self.prompt = prompt
        self.experiment_id = experiment_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.resource_guard = resource_guard

    def evaluate(self, snapshot: DecisionSnapshot) -> LLMDecisionRecord:
        frozen = freeze_snapshot(snapshot)
        assert frozen.snapshot_hash is not None
        existing = self.repository.load_llm_decision(
            self.experiment_id,
            self.config.phase2_epoch_id,
            frozen.snapshot_hash,
            self.config.llm_strategy_version,
        )
        if existing is not None:
            return existing
        now = self.clock().astimezone(UTC)
        age = max(0.0, (now - frozen.snapshot_timestamp).total_seconds())
        if not frozen.data_quality.scoreable:
            return self._persist_fail_closed(
                frozen, FailClosedReason.SNAPSHOT_NOT_SCOREABLE, age=age
            )
        if age > self.config.snapshot_to_call_staleness_seconds:
            return self._persist_fail_closed(
                frozen, FailClosedReason.STALE_SNAPSHOT, age=age
            )
        boundary = self.config.information_boundary
        if not boundary.snapshot_only or self.config.tools_allowed:
            return self._persist_fail_closed(
                frozen, FailClosedReason.INFORMATION_BOUNDARY_VIOLATION, age=age
            )

        snapshot_json = canonical_json(frozen)
        max_attempts = 1 + self.config.malformed_output_max_retries
        for attempt_number in range(1, max_attempts + 1):
            try:
                if self.resource_guard is None:
                    response = self.provider.invoke(
                        prompt=self.prompt,
                        snapshot_json=snapshot_json,
                        timeout_seconds=self.config.request_timeout_seconds,
                    )
                else:
                    self.resource_guard.assert_disk_budget()
                    self.resource_guard.assert_api_budget(
                        self.repository.total_model_cost_usd()
                    )
                    with self.resource_guard.llm_slot():
                        response = self.provider.invoke(
                            prompt=self.prompt,
                            snapshot_json=snapshot_json,
                            timeout_seconds=self.config.request_timeout_seconds,
                        )
            except ResourceBudgetExceeded:
                failed_at = self.clock().astimezone(UTC)
                self._save_attempt(
                    frozen,
                    attempt_number,
                    failed_at,
                    failed_at,
                    "REJECTED",
                    FailClosedReason.RESOURCE_BUDGET_EXCEEDED.value,
                    0,
                    None,
                )
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.RESOURCE_BUDGET_EXCEEDED,
                    age=age,
                    retry_count=attempt_number - 1,
                )
            except ProviderTimeout:
                failed_at = self.clock().astimezone(UTC)
                self._save_attempt(
                    frozen,
                    attempt_number,
                    failed_at,
                    failed_at,
                    "TIMEOUT",
                    FailClosedReason.TIMEOUT.value,
                    0,
                    None,
                )
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.TIMEOUT,
                    age=age,
                    retry_count=attempt_number - 1,
                )
            except ProviderError:
                failed_at = self.clock().astimezone(UTC)
                self._save_attempt(
                    frozen,
                    attempt_number,
                    failed_at,
                    failed_at,
                    "ERROR",
                    FailClosedReason.API_MODEL_ERROR.value,
                    0,
                    None,
                )
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.API_MODEL_ERROR,
                    age=age,
                    retry_count=attempt_number - 1,
                )

            raw_hash = sha256(response.raw_output.encode("utf-8")).hexdigest()
            try:
                raw_plaintext = validate_raw_provider_plaintext(response.raw_output)
                raw_capture_status = "CAPTURED"
            except SensitiveCredentialMaterial:
                raw_plaintext = None
                raw_capture_status = "WITHHELD_SENSITIVE"
            age = max(
                0.0,
                (
                    response.request_started_at - frozen.snapshot_timestamp
                ).total_seconds(),
            )
            if response.tool_calls_count > 0:
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "REJECTED",
                    FailClosedReason.TOOL_INTEGRITY_VIOLATION.value,
                    response.tool_calls_count,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.TOOL_INTEGRITY_VIOLATION,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                )
            if raw_capture_status == "WITHHELD_SENSITIVE":
                reason = FailClosedReason.SENSITIVE_CREDENTIAL_MATERIAL
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "REJECTED",
                    reason.value,
                    0,
                    raw_hash,
                    None,
                    raw_capture_status,
                )
                return self._persist_fail_closed(
                    frozen,
                    reason,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                )
            completion_age = max(
                0.0,
                (response.request_ended_at - frozen.snapshot_timestamp).total_seconds(),
            )
            if completion_age > self.config.snapshot_to_call_staleness_seconds:
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "REJECTED",
                    FailClosedReason.STALE_SNAPSHOT.value,
                    0,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.STALE_SNAPSHOT,
                    age=completion_age,
                    response=response,
                    retry_count=attempt_number - 1,
                )
            if (
                response.model != self.config.model
                or response.model_version != self.config.model_version
            ):
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "REJECTED",
                    FailClosedReason.API_MODEL_ERROR.value,
                    0,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.API_MODEL_ERROR,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                )
            try:
                raw = json.loads(response.raw_output)
            except json.JSONDecodeError:
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "MALFORMED",
                    FailClosedReason.MALFORMED_JSON.value,
                    0,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                if attempt_number < max_attempts:
                    continue
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.RETRY_EXHAUSTED,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                    metadata={"terminal_cause": FailClosedReason.MALFORMED_JSON.value},
                )
            try:
                output = structured_output_model(
                    self.config.output_schema_version
                ).model_validate(raw)
            except ValidationError:
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "INVALID_SCHEMA",
                    FailClosedReason.INVALID_SCHEMA.value,
                    0,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                if attempt_number < max_attempts:
                    continue
                return self._persist_fail_closed(
                    frozen,
                    FailClosedReason.RETRY_EXHAUSTED,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                    metadata={"terminal_cause": FailClosedReason.INVALID_SCHEMA.value},
                )
            if output.output_schema_version != self.config.output_schema_version:
                reason = FailClosedReason.UNSUPPORTED_SCHEMA_VERSION
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "REJECTED",
                    reason.value,
                    0,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                return self._persist_fail_closed(
                    frozen,
                    reason,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                )
            if output.input_snapshot_hash != frozen.snapshot_hash:
                reason = FailClosedReason.SNAPSHOT_HASH_MISMATCH
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "REJECTED",
                    reason.value,
                    0,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                return self._persist_fail_closed(
                    frozen,
                    reason,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                )
            try:
                validate_geometry(output, frozen)
            except GeometryViolation:
                reason = FailClosedReason.INVALID_GEOMETRY
                self._save_attempt(
                    frozen,
                    attempt_number,
                    response.request_started_at,
                    response.request_ended_at,
                    "REJECTED",
                    reason.value,
                    0,
                    raw_hash,
                    raw_plaintext,
                    raw_capture_status,
                )
                return self._persist_fail_closed(
                    frozen,
                    reason,
                    age=age,
                    response=response,
                    retry_count=attempt_number - 1,
                )
            self._save_attempt(
                frozen,
                attempt_number,
                response.request_started_at,
                response.request_ended_at,
                "VALID",
                None,
                0,
                raw_hash,
                raw_plaintext,
                raw_capture_status,
            )
            record = LLMDecisionRecord(
                experiment_id=self.experiment_id,
                phase2_epoch_id=self.config.phase2_epoch_id,
                timestamp=frozen.snapshot_timestamp,
                input_snapshot_hash=frozen.snapshot_hash,
                model=response.model,
                model_version=response.model_version,
                prompt_version=self.config.prompt_version,
                output_schema_version=output.output_schema_version,
                decision=output.decision,
                confidence=output.confidence,
                rationale_tags=output.rationale_tags,
                invocation_reason=output.invocation_reason,
                entry=output.entry,
                stop=output.stop,
                target=output.target,
                invalidation=output.invalidation,
                ttl_minutes=output.ttl_minutes,
                request_started_at=response.request_started_at,
                request_ended_at=response.request_ended_at,
                snapshot_to_call_age_seconds=age,
                latency_ms=max(
                    0,
                    int(
                        (
                            response.request_ended_at - response.request_started_at
                        ).total_seconds()
                        * 1000
                    ),
                ),
                retry_count=attempt_number - 1,
                input_tokens=response.input_tokens,
                cached_input_tokens=response.cached_input_tokens,
                output_tokens=response.output_tokens,
                model_cost_usd=response.cost_usd,
                tool_calls_count=0,
                tool_integrity_ok=True,
                schema_valid=True,
                geometry_valid=True,
                runner_status=RunnerStatus.VALID,
                reason_code=FailClosedReason.NONE,
                metadata={
                    "bull_case": list(output.bull_case),
                    "bear_case": list(output.bear_case),
                    "data_conflicts": list(output.data_conflicts),
                    "snapshot_payload_sha256": sha256(
                        snapshot_json.encode("utf-8")
                    ).hexdigest(),
                },
            )
            return self.repository.save_llm_decision(
                self.config.llm_strategy_version, record
            )
        raise AssertionError("unreachable")

    def _save_attempt(
        self,
        snapshot: DecisionSnapshot,
        attempt: int,
        started: datetime,
        ended: datetime,
        provider_status: str,
        error_code: str | None,
        tool_calls_count: int,
        raw_output_hash: str | None,
        raw_output_plaintext: str | None = None,
        raw_capture_status: str = "NOT_AVAILABLE",
    ) -> None:
        assert snapshot.snapshot_hash is not None
        self.repository.save_attempt(
            InvocationAttempt(
                experiment_id=self.experiment_id,
                phase2_epoch_id=self.config.phase2_epoch_id,
                input_snapshot_hash=snapshot.snapshot_hash,
                attempt=attempt,
                started_at=started,
                ended_at=ended,
                raw_output_hash=raw_output_hash,
                raw_output_plaintext=raw_output_plaintext,
                raw_capture_status=raw_capture_status,
                provider_status=provider_status,
                error_code=error_code,
                tool_calls_count=tool_calls_count,
            )
        )

    def _persist_fail_closed(
        self,
        snapshot: DecisionSnapshot,
        reason: FailClosedReason,
        *,
        age: float,
        response: object | None = None,
        retry_count: int = 0,
        metadata: dict[str, object] | None = None,
    ) -> LLMDecisionRecord:
        assert snapshot.snapshot_hash is not None
        kwargs: dict[str, object] = {}
        if response is not None:
            kwargs = {
                "request_started_at": response.request_started_at,
                "request_ended_at": response.request_ended_at,
                "tool_calls_count": response.tool_calls_count,
                "input_tokens": response.input_tokens,
                "cached_input_tokens": response.cached_input_tokens,
                "output_tokens": response.output_tokens,
                "cost_usd": response.cost_usd,
            }
        record = LLMDecisionRecord.fail_closed(
            experiment_id=self.experiment_id,
            phase2_epoch_id=self.config.phase2_epoch_id,
            timestamp=snapshot.snapshot_timestamp,
            snapshot_hash=snapshot.snapshot_hash,
            model=self.config.model,
            model_version=self.config.model_version,
            prompt_version=self.config.prompt_version,
            output_schema_version=self.config.output_schema_version,
            reason=reason,
            snapshot_age=age,
            retry_count=retry_count,
            metadata=dict(metadata or {}),
            **kwargs,
        )
        return self.repository.save_llm_decision(
            self.config.llm_strategy_version, record
        )


def adapt_llm_decision(
    record: LLMDecisionRecord, adapter_version: str
) -> StrategyDecision:
    entry_reference = record.entry.trigger_price
    return StrategyDecision(
        decision_id=deterministic_decision_id(
            record.input_snapshot_hash, "LLM_V1", "LLM_V1"
        ),
        snapshot_hash=record.input_snapshot_hash,
        strategy_id="LLM_V1",
        strategy_version="LLM_V1",
        decision=record.decision
        if record.runner_status == RunnerStatus.VALID
        else Decision.NO_TRADE,
        created_at=record.request_ended_at,
        entry_mode=record.entry.mode.value,
        entry_reference=entry_reference,
        stop_reference=record.stop.price if record.stop else None,
        target_reference=record.target.price if record.target else None,
        trade_ttl_minutes=record.ttl_minutes or 0,
        reason_codes=(record.reason_code.value,),
        metadata={
            "adapter_version": adapter_version,
            "confidence": record.confidence.value,
            "invalidation": record.invalidation.model_dump(mode="json")
            if record.invalidation
            else None,
            "runner_status": record.runner_status.value,
        },
    )
