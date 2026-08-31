from datetime import timedelta

from hype_autopilot.config import load_yaml
from hype_autopilot.data.models import FundingObservation, ObservationClass
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.simulation.models import ExitReason, TradeStatus
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.strategies.base import Decision, StrategyDecision
from tests.helpers import candle_series, memory_repo, populate_scoreable


def setup_decision(direction=Decision.LONG, strategy="QUANT_MR", version="QUANT_MR_V1"):
    repo = memory_repo()
    at = populate_scoreable(repo)
    snapshot = repo.save_snapshot(SnapshotBuilder(
        repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml")
    ).build(at, available_at=at))
    decision = StrategyDecision(
        decision_id=f"{strategy}-{direction.value}", snapshot_hash=snapshot.snapshot_hash,
        strategy_id=strategy, strategy_version=version, decision=direction, created_at=at,
        entry_reference=100, stop_reference=90 if direction == Decision.LONG else 110,
        target_reference=105 if direction == Decision.LONG else 95,
        trade_ttl_minutes=60, reason_codes=("TEST",),
        metadata={"atr14_1h": 5.0, "stop_atr_multiple": 2.0},
    )
    repo.save_strategy_decision(decision)
    return repo, at, decision


def test_restart_safe_long_trade_opens_and_closes_with_persisted_costs():
    repo, at, decision = setup_decision()
    simulator = PaperSimulator(repo, fee_bps_per_side=1, slippage_bps_per_side=1)
    pending = simulator.submit(decision)
    assert pending.status == TradeStatus.PENDING_ENTRY
    bars = candle_series("HYPE", "1m", at + timedelta(minutes=3), 3, timedelta(minutes=1), 100)
    bars[1] = bars[1].model_copy(update={"high": 106, "close": 105.5})
    repo.save_candles(bars)
    restarted = PaperSimulator(repo, fee_bps_per_side=1, slippage_bps_per_side=1)
    result = restarted.process_until(at + timedelta(minutes=3))[0]
    assert result.status == TradeStatus.CLOSED
    assert result.exit_reason == ExitReason.TARGET
    assert result.fees > 0 and result.slippage_cost > 0
    row = repo.db.execute("SELECT fees, slippage_cost FROM paper_trades").fetchone()
    assert row["fees"] > 0 and row["slippage_cost"] > 0
    assert repo.db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2


def test_short_trade_and_position_open_suppression():
    repo, at, decision = setup_decision(Decision.SHORT)
    simulator = PaperSimulator(repo, fee_bps_per_side=0, slippage_bps_per_side=0)
    assert simulator.submit(decision).status == TradeStatus.PENDING_ENTRY
    second = decision.model_copy(update={"decision_id": "second-short", "strategy_version": "QUANT_MR_V1_REPEAT"})
    repo.save_strategy_decision(second)
    assert simulator.submit(second).status == TradeStatus.SUPPRESSED_POSITION_OPEN
    bars = candle_series("HYPE", "1m", at + timedelta(minutes=3), 3, timedelta(minutes=1), 100)
    bars[1] = bars[1].model_copy(update={"low": 94, "close": 95})
    repo.save_candles(bars)
    closed = simulator.process_until(at + timedelta(minutes=3))[0]
    assert closed.status == TradeStatus.CLOSED
    assert closed.direction == "SHORT"


def test_funding_accrues_once_and_chandelier_only_tightens_after_completed_bar():
    repo, at, decision = setup_decision(Decision.LONG, "QUANT_TREND", "QUANT_TREND_V1")
    decision = decision.model_copy(update={"target_reference": None,
        "metadata": {"atr14_1h": 2.0, "stop_atr_multiple": 3.0}})
    repo.db.execute("DELETE FROM strategy_decisions")
    repo.db.commit()
    repo.save_strategy_decision(decision)
    simulator = PaperSimulator(repo, fee_bps_per_side=0, slippage_bps_per_side=0)
    simulator.submit(decision)
    bars = candle_series("HYPE", "1m", at + timedelta(minutes=3), 3, timedelta(minutes=1), 100)
    bars[1] = bars[1].model_copy(update={"high": 110, "low": 99, "close": 109})
    bars[2] = bars[2].model_copy(update={"open": 107, "high": 109, "low": 103, "close": 107})
    repo.save_candles(bars)
    repo.save_funding([FundingObservation(symbol="HYPE", source_timestamp=at + timedelta(minutes=2),
        received_at=at + timedelta(minutes=2), funding_rate=.001,
        observation_class=ObservationClass.SOAK)])
    closed = simulator.process_until(at + timedelta(minutes=3))[0]
    assert closed.exit_reason == ExitReason.STOP
    assert closed.current_stop_price >= closed.stop_price
    first_cost = closed.funding_cost
    simulator.process_until(at + timedelta(minutes=4))
    assert repo.trade_for_decision(decision.decision_id).funding_cost == first_cost
