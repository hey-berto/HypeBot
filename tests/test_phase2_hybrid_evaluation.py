from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hype_autopilot.phase2.evaluation import (
    OutcomeStatus,
    PairedOutcome,
    StrategyOutcome,
    build_paired_outcome,
    evaluate_pair,
    evaluate_registered_pairs,
)
from hype_autopilot.phase2.hybrid import HybridAgreementStrategy
from hype_autopilot.phase2.models import (
    Confidence,
    EntryMode,
    EntrySemantics,
    FailClosedReason,
    Invalidation,
    LLMDecisionRecord,
    PriceGeometry,
    RunnerStatus,
)
from hype_autopilot.simulation.models import TradeStatus
from hype_autopilot.strategies.base import Decision, StrategyDecision

AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SNAPSHOT_HASH = "a" * 64


def quant_decision(decision: Decision = Decision.LONG) -> StrategyDecision:
    return StrategyDecision(
        decision_id="quant-1",
        snapshot_hash=SNAPSHOT_HASH,
        strategy_id="QUANT_TREND_V1",
        strategy_version="QUANT_TREND_V1",
        decision=decision,
        created_at=AT,
        entry_mode="AFTER_LATENCY",
        stop_reference=95 if decision != Decision.NO_TRADE else None,
        target_reference=110 if decision != Decision.NO_TRADE else None,
        trade_ttl_minutes=120 if decision != Decision.NO_TRADE else 0,
        reason_codes=("SYNTHETIC",),
        metadata={"frozen": True},
    )


def llm_record(
    decision: Decision = Decision.LONG, *, valid: bool = True
) -> LLMDecisionRecord:
    return LLMDecisionRecord(
        experiment_id="synthetic",
        phase2_epoch_id="phase2_epoch_001",
        timestamp=AT,
        input_snapshot_hash=SNAPSHOT_HASH,
        model="gpt-5.6-terra",
        model_version="gpt-5.6-terra",
        prompt_version="LLM_PROMPT_V1",
        output_schema_version="LLM_OUTPUT_V1",
        decision=decision,
        confidence=Confidence.HIGH,
        invocation_reason="SCHEDULED_RESEARCH",
        entry=EntrySemantics(
            mode=EntryMode.NOW if decision != Decision.NO_TRADE else EntryMode.NONE
        ),
        stop=PriceGeometry(price=95) if decision != Decision.NO_TRADE else None,
        target=PriceGeometry(price=110) if decision != Decision.NO_TRADE else None,
        invalidation=Invalidation(category="TREND_BREAK")
        if decision != Decision.NO_TRADE
        else None,
        ttl_minutes=60 if decision != Decision.NO_TRADE else None,
        request_started_at=AT,
        request_ended_at=AT,
        snapshot_to_call_age_seconds=0,
        latency_ms=0,
        retry_count=0,
        input_tokens=1,
        cached_input_tokens=0,
        output_tokens=1,
        model_cost_usd=0.001,
        tool_calls_count=0,
        tool_integrity_ok=True,
        schema_valid=valid,
        geometry_valid=valid,
        runner_status=RunnerStatus.VALID if valid else RunnerStatus.FAIL_CLOSED,
        reason_code=FailClosedReason.NONE if valid else FailClosedReason.INVALID_SCHEMA,
    )


def test_hybrid_trades_only_on_same_direction_and_preserves_quant_geometry():
    quant = quant_decision()
    original = quant.model_dump(mode="json")
    hybrid = HybridAgreementStrategy(
        strategy_id="HYBRID_TREND_LLM_V1", strategy_version="HYBRID_TREND_LLM_V1"
    )
    agreed = hybrid.combine(quant, llm_record())
    disagreed = hybrid.combine(quant, llm_record(Decision.SHORT))
    failed = hybrid.combine(quant, llm_record(valid=False))
    assert agreed.decision == Decision.LONG
    assert agreed.stop_reference == quant.stop_reference
    assert agreed.target_reference == quant.target_reference
    assert disagreed.decision == failed.decision == Decision.NO_TRADE
    assert quant.model_dump(mode="json") == original


def test_hybrid_rejects_cross_snapshot_inputs():
    record = llm_record().model_copy(update={"input_snapshot_hash": "b" * 64})
    hybrid = HybridAgreementStrategy(
        strategy_id="HYBRID_MR_LLM_V1", strategy_version="HYBRID_MR_LLM_V1"
    )
    with pytest.raises(ValueError):
        hybrid.combine(quant_decision(), record)


def outcome(
    strategy: str,
    decision: Decision,
    *,
    status: TradeStatus | None,
    result: float | None,
    flat: bool = True,
    direction: str = "LONG",
    regime: str = "UP_NORMAL",
) -> StrategyOutcome:
    return StrategyOutcome(
        snapshot_hash=SNAPSHOT_HASH,
        strategy_id=strategy,
        decision=decision,
        was_flat=flat,
        trade_status=status,
        net_return=result,
        direction=direction,
        regime=regime,
        fees=0.001 if status == TradeStatus.CLOSED else 0,
        slippage=0.0005 if status == TradeStatus.CLOSED else 0,
        api_cost=0.0002 if strategy == "LLM_V1" else 0,
    )


def test_pair_accounting_no_trade_zero_open_censored_and_suppressed_not_executed():
    no_trade = build_paired_outcome(
        pair_id="LLM_vs_QUANT",
        designation="CONFIRMATORY",
        treatment=outcome("LLM_V1", Decision.NO_TRADE, status=None, result=None),
        control=outcome(
            "QUANT_TREND_V1", Decision.LONG, status=TradeStatus.CLOSED, result=0.02
        ),
    )
    assert no_trade.outcome_status == OutcomeStatus.COMPLETE
    assert no_trade.treatment_return == 0 and no_trade.control_return == 0.02
    open_row = build_paired_outcome(
        pair_id="LLM_vs_QUANT",
        designation="CONFIRMATORY",
        treatment=outcome(
            "LLM_V1", Decision.LONG, status=TradeStatus.OPEN, result=None
        ),
        control=outcome("QUANT_TREND_V1", Decision.NO_TRADE, status=None, result=None),
    )
    assert open_row.outcome_status == OutcomeStatus.RIGHT_CENSORED
    suppressed = build_paired_outcome(
        pair_id="LLM_vs_QUANT",
        designation="CONFIRMATORY",
        treatment=outcome(
            "LLM_V1",
            Decision.LONG,
            status=TradeStatus.SUPPRESSED_POSITION_OPEN,
            result=None,
        ),
        control=outcome("QUANT_TREND_V1", Decision.NO_TRADE, status=None, result=None),
    )
    assert suppressed.treatment_return == 0 and not suppressed.treatment_executed


def test_nonflat_boundary_is_not_coeligible():
    row = build_paired_outcome(
        pair_id="HYBRID_vs_QUANT",
        designation="PRIMARY_CONFIRMATORY",
        treatment=outcome(
            "HYBRID", Decision.NO_TRADE, status=None, result=None, flat=False
        ),
        control=outcome("QUANT", Decision.NO_TRADE, status=None, result=None),
    )
    assert row.outcome_status == OutcomeStatus.EXCLUDED
    assert row.treatment_return is None


def paired_rows(pair_id: str, designation: str) -> list[PairedOutcome]:
    rows = []
    for index, value in enumerate(
        (0.01, -0.005, 0.02, 0.0, 0.015, -0.002, 0.008, 0.004, 0.012, -0.003)
    ):
        treatment = outcome(
            "LLM_V1",
            Decision.LONG,
            status=TradeStatus.CLOSED,
            result=value,
            direction="LONG" if index % 2 == 0 else "SHORT",
            regime="UP_NORMAL" if index < 5 else "DOWN_HIGH",
        )
        control = outcome(
            "QUANT",
            Decision.NO_TRADE,
            status=None,
            result=None,
            direction="FLAT",
            regime=treatment.regime,
        )
        row = build_paired_outcome(
            pair_id=pair_id,
            designation=designation,
            treatment=treatment,
            control=control,
        )
        rows.append(row.__class__(**{**row.__dict__, "snapshot_hash": f"{index:064x}"}))
    return rows


def test_pair_evaluation_uses_arch_block_bootstraps_and_reports_robustness():
    report = evaluate_pair(
        paired_rows("LLM_vs_TREND", "CONFIRMATORY"), repetitions=100, block_size=3
    )
    assert report["bootstrap_library"] == "arch"
    assert report["n_complete"] == 10
    assert report["effective_sample_size"] <= 10
    assert len(report["stationary_bootstrap_95_ci"]) == 2
    assert "UP_NORMAL" in report["regime_slice_mean_difference"]
    assert report["treatment_executed_trades"] == 10


def test_confirmatory_and_exploratory_pairs_are_evaluated_separately():
    rows = paired_rows("LLM_vs_TREND", "CONFIRMATORY") + paired_rows(
        "HYBRID_MR_vs_MR", "EXPLORATORY"
    )
    reports = evaluate_registered_pairs(rows, repetitions=50, block_size=2)
    assert set(reports) == {"LLM_vs_TREND", "HYBRID_MR_vs_MR"}
    with pytest.raises(ValueError):
        evaluate_pair(rows, repetitions=10)
