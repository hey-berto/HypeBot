from datetime import UTC, datetime, timedelta

from hype_autopilot.data.models import Candle
from hype_autopilot.simulation.engine import simulate_trade
from hype_autopilot.simulation.models import ExitReason
from hype_autopilot.strategies.base import Decision, StrategyDecision


def test_same_bar_stop_and_target_resolves_adversely():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    decision = StrategyDecision(decision_id="d1", snapshot_hash="abc", strategy_id="test",
                                strategy_version="v1", decision=Decision.LONG, created_at=at,
                                entry_reference=100, stop_reference=95, target_reference=105,
                                trade_ttl_minutes=60, reason_codes=("TEST",))
    bar = Candle(symbol="HYPE", interval="1m", open_time=at + timedelta(minutes=1),
                 close_time=at + timedelta(minutes=2), open=100, high=106, low=94, close=101,
                 volume=1, received_at=at + timedelta(minutes=2))
    trade = simulate_trade(decision, [bar], fee_bps_per_side=0, slippage_bps_per_side=0)
    assert trade is not None
    assert trade.exit_reason == ExitReason.STOP
    assert "INTRABAR_ORDER_AMBIGUOUS" in trade.flags

