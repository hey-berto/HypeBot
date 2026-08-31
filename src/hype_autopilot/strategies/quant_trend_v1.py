from hype_autopilot.snapshots.models import DecisionSnapshot
from hype_autopilot.strategies.base import Decision, StrategyDecision, deterministic_decision_id, require_scored


class QuantTrendV1:
    strategy_id = "QUANT_TREND"
    strategy_version = "QUANT_TREND_V1"

    def evaluate(self, snapshot: DecisionSnapshot) -> StrategyDecision:
        digest = require_scored(snapshot)
        f = snapshot.market.hype_features
        side, reason = Decision.NO_TRADE, "NO_BREAKOUT"
        stop = None
        if not snapshot.data_quality.scoreable:
            reason = "SNAPSHOT_NOT_SCOREABLE"
        elif None in (f.donchian_high_1h, f.donchian_low_1h, f.atr14_1h):
            reason = "INSUFFICIENT_HISTORY"
        elif f.last_15m_close > f.donchian_high_1h:
            side, reason = Decision.LONG, "BREAKOUT_PRIOR_DONCHIAN_HIGH"
            stop = f.last_15m_close - 3.0 * f.atr14_1h
        elif f.last_15m_close < f.donchian_low_1h:
            side, reason = Decision.SHORT, "BREAKOUT_PRIOR_DONCHIAN_LOW"
            stop = f.last_15m_close + 3.0 * f.atr14_1h
        return StrategyDecision(
            decision_id=deterministic_decision_id(digest, self.strategy_id, self.strategy_version),
            snapshot_hash=digest, strategy_id=self.strategy_id, strategy_version=self.strategy_version,
            created_at=snapshot.snapshot_timestamp,
            decision=side, entry_reference=f.last_15m_close, stop_reference=stop,
            target_reference=None, trade_ttl_minutes=2880, reason_codes=(reason,),
            metadata={"trailing_stop": "CHANDELIER_3_ATR_1M", "atr14_1h": f.atr14_1h,
                      "stop_atr_multiple": 3.0},
        )
