from datetime import UTC, datetime, timedelta

from hype_autopilot.config import load_yaml
from hype_autopilot.diagnostics import detector_proxy_report
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.strategies.quant_trend_v1 import QuantTrendV1
from hype_autopilot.strategies.setup_detector_v1 import DetectorTrigger, SetupDetectorV1
from hype_autopilot.simulation.models import ExitReason, PaperTrade, TradeStatus
from hype_autopilot.strategies.base import Decision, StrategyDecision
from tests.helpers import memory_repo, populate_scoreable


def test_detector_is_independent_and_report_is_explicitly_provisional():
    repo = memory_repo()
    at = populate_scoreable(repo)
    snapshot = repo.save_snapshot(SnapshotBuilder(
        repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml")
    ).build(at, available_at=at + timedelta(seconds=1)))
    strategy = repo.save_strategy_decision(QuantTrendV1().evaluate(snapshot))
    detector = repo.save_detector_decision(SetupDetectorV1().evaluate(snapshot))
    assert detector.snapshot_hash == strategy.snapshot_hash
    assert detector.detector_version == "SETUP_DETECTOR_V1"
    report = detector_proxy_report(repo)
    assert report["label"].startswith("PHASE_1_PROVISIONAL_PROXY")
    assert report["snapshots"] == 1


def test_profitable_capture_and_misses_are_grouped_by_regime_and_direction():
    repo = memory_repo()
    base_at = populate_scoreable(repo)
    builder = SnapshotBuilder(repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml"))
    for index, trigger in enumerate((DetectorTrigger.TRIGGER_LONG, DetectorTrigger.NO_TRIGGER)):
        at = base_at - timedelta(minutes=15 * index)
        snap = repo.save_snapshot(builder.build(at, available_at=base_at + timedelta(seconds=1)))
        decision = StrategyDecision(
            decision_id=f"profitable-{index}", snapshot_hash=snap.snapshot_hash,
            strategy_id="TEST_TREND", strategy_version="V1", decision=Decision.LONG,
            created_at=at, entry_reference=100, stop_reference=95, target_reference=110,
            trade_ttl_minutes=60, reason_codes=("TEST",),
        )
        repo.save_strategy_decision(decision)
        detector = SetupDetectorV1().evaluate(snap).model_copy(update={"trigger": trigger})
        repo.save_detector_decision(detector)
        repo.save_trade(PaperTrade(
            paper_trade_id=f"profitable-trade-{index}", strategy_decision_id=decision.decision_id,
            strategy_id=decision.strategy_id, snapshot_hash=snap.snapshot_hash, direction="LONG",
            signal_time=at, entry_time=at + timedelta(minutes=1), entry_price=100,
            stop_price=95, target_price=110, current_stop_price=95,
            highest_price=110, lowest_price=99, exit_time=at + timedelta(minutes=2),
            exit_price=110, exit_reason=ExitReason.TARGET, gross_pnl=10, net_pnl=9,
            return_pct=.09, r_multiple=1.8, status=TradeStatus.CLOSED,
            last_processed_at=at + timedelta(minutes=2),
        ))
    report = detector_proxy_report(repo)
    assert report["profitable_quant_trade_capture_rate"] == .5
    assert report["missed_profitable_quant_trade_count"] == 1
    assert sum(report["missed_profitable_by_regime_direction"].values()) == 1
