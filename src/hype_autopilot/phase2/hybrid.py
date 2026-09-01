from __future__ import annotations

from hype_autopilot.phase2.models import LLMDecisionRecord, RunnerStatus
from hype_autopilot.strategies.base import (
    Decision,
    StrategyDecision,
    deterministic_decision_id,
)


class HybridAgreementStrategy:
    def __init__(self, *, strategy_id: str, strategy_version: str) -> None:
        if strategy_id not in {"HYBRID_TREND_LLM_V1", "HYBRID_MR_LLM_V1"}:
            raise ValueError("unsupported frozen hybrid identity")
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version

    def combine(
        self, quant: StrategyDecision, llm: LLMDecisionRecord
    ) -> StrategyDecision:
        if quant.snapshot_hash != llm.input_snapshot_hash:
            raise ValueError(
                "hybrid inputs must reference the exact same snapshot hash"
            )
        agreement = (
            llm.runner_status == RunnerStatus.VALID
            and quant.decision != Decision.NO_TRADE
            and quant.decision == llm.decision
        )
        decision = quant.decision if agreement else Decision.NO_TRADE
        reasons = (
            ("SAME_DIRECTION_AGREEMENT",)
            if agreement
            else ("NO_SAME_DIRECTION_AGREEMENT", llm.reason_code.value)
        )
        return StrategyDecision(
            decision_id=deterministic_decision_id(
                quant.snapshot_hash, self.strategy_id, self.strategy_version
            ),
            snapshot_hash=quant.snapshot_hash,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            decision=decision,
            created_at=max(quant.created_at, llm.request_ended_at),
            entry_mode=quant.entry_mode,
            entry_reference=quant.entry_reference if agreement else None,
            stop_reference=quant.stop_reference if agreement else None,
            target_reference=quant.target_reference if agreement else None,
            trade_ttl_minutes=quant.trade_ttl_minutes if agreement else 0,
            reason_codes=reasons,
            metadata={
                "quant_decision_id": quant.decision_id,
                "llm_input_snapshot_hash": llm.input_snapshot_hash,
                "llm_confidence": llm.confidence.value,
                "agreement": agreement,
                "geometry_source": quant.strategy_version,
            },
        )
