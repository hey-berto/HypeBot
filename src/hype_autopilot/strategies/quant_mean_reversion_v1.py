from hype_autopilot.snapshots.models import DecisionSnapshot
from hype_autopilot.strategies.base import Decision, StrategyDecision, deterministic_decision_id, require_scored


class QuantMeanReversionV1:
    strategy_id = "QUANT_MR"
    strategy_version = "QUANT_MR_V1"

    def evaluate(self, snapshot: DecisionSnapshot) -> StrategyDecision:
        digest = require_scored(snapshot)
        f = snapshot.market.hype_features
        side, reason, stop, target = Decision.NO_TRADE, "NO_EXTREME", None, None
        if not snapshot.data_quality.scoreable:
            reason = "SNAPSHOT_NOT_SCOREABLE"
        elif None in (f.funding_zscore, f.rsi14_1h, f.atr14_1h, f.ema20_1h):
            reason = "INSUFFICIENT_HISTORY"
        elif f.funding_zscore >= 2.0 and f.rsi14_1h >= 70.0:
            if f.ema20_1h >= f.last_15m_close:
                reason = "INVALID_TARGET_AT_SIGNAL"
            else:
                side, reason, target = Decision.SHORT, "CROWDED_LONG_EXTREME", f.ema20_1h
                stop = f.last_15m_close + 2.0 * f.atr14_1h
        elif f.funding_zscore <= -2.0 and f.rsi14_1h <= 30.0:
            if f.ema20_1h <= f.last_15m_close:
                reason = "INVALID_TARGET_AT_SIGNAL"
            else:
                side, reason, target = Decision.LONG, "CROWDED_SHORT_EXTREME", f.ema20_1h
                stop = f.last_15m_close - 2.0 * f.atr14_1h
        return StrategyDecision(
            decision_id=deterministic_decision_id(digest, self.strategy_id, self.strategy_version),
            snapshot_hash=digest, strategy_id=self.strategy_id, strategy_version=self.strategy_version,
            created_at=snapshot.snapshot_timestamp,
            decision=side, entry_reference=f.last_15m_close, stop_reference=stop,
            target_reference=target, trade_ttl_minutes=720, reason_codes=(reason,),
            metadata={"target_frozen_at_signal": True, "atr14_1h": f.atr14_1h,
                      "stop_atr_multiple": 2.0},
        )
