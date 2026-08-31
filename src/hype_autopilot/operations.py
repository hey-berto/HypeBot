from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.clock import floor_quarter_hour, next_quarter_hour
from hype_autopilot.data.collectors import MarketDataCollector, ResilientWebsocketCollector
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.data.repository import Repository
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.snapshots.models import DecisionSnapshot
from hype_autopilot.strategies.quant_mean_reversion_v1 import QuantMeanReversionV1
from hype_autopilot.strategies.quant_trend_v1 import QuantTrendV1
from hype_autopilot.strategies.setup_detector_v1 import SetupDetectorV1


class CycleRunner:
    def __init__(self, repository: Repository, builder: SnapshotBuilder,
                 simulator: PaperSimulator) -> None:
        self.repository = repository
        self.builder = builder
        self.simulator = simulator
        self.strategies = (QuantTrendV1(), QuantMeanReversionV1())
        self.detector = SetupDetectorV1()

    def observation_class(self) -> ObservationClass:
        active = self.repository.db.execute(
            "SELECT 1 FROM epochs WHERE status = 'ACTIVE' LIMIT 1"
        ).fetchone()
        return ObservationClass.SCORED_PROSPECTIVE if active else ObservationClass.SOAK

    def run(self, scheduled_at: datetime, *, available_at: datetime | None = None,
            observation_class: ObservationClass | None = None) -> DecisionSnapshot:
        scheduled_at = floor_quarter_hour(scheduled_at)
        observation_class = observation_class or self.observation_class()
        cycle_id = str(uuid5(NAMESPACE_URL, f"cycle:{observation_class.value}:{scheduled_at.isoformat()}"))
        existing = self.repository.begin_cycle(cycle_id, scheduled_at, observation_class.value)
        if existing and existing["status"] == "COMPLETE" and existing["snapshot_hash"]:
            return self.repository.load_snapshot(existing["snapshot_hash"])
        if existing:
            cycle_id = existing["cycle_id"]
        try:
            managed_before = self.simulator.process_until(scheduled_at)
            snapshot = self.repository.snapshot_for_cycle_key(
                self.builder.epoch_config["epoch_id"], scheduled_at, observation_class.value
            )
            if snapshot is None:
                snapshot = self.repository.save_snapshot(self.builder.build(
                    scheduled_at, available_at=available_at, observation_class=observation_class
                ))
            decisions = [self.repository.save_strategy_decision(strategy.evaluate(snapshot))
                         for strategy in self.strategies]
            detector = self.repository.save_detector_decision(self.detector.evaluate(snapshot))
            submitted = [self.simulator.submit(decision) for decision in decisions]
            if not snapshot.data_quality.scoreable:
                self.repository.data_quality_event(
                    "WARN", "SNAPSHOT_NOT_SCOREABLE",
                    {"snapshot_hash": snapshot.snapshot_hash,
                     "reasons": list(snapshot.data_quality.rejection_reasons)},
                )
            details = {
                "scoreable": snapshot.data_quality.scoreable,
                "decisions": [item.decision.value for item in decisions],
                "detector": detector.trigger.value,
                "managed_trades": len(managed_before),
                "submitted_trades": sum(item is not None for item in submitted),
            }
            self.repository.finish_cycle(cycle_id, "COMPLETE", snapshot.snapshot_hash, details)
            self.repository.health("cycle", "COMPLETE", {"scheduled_at": scheduled_at, **details})
            return snapshot
        except Exception as exc:
            self.repository.finish_cycle(cycle_id, "FAILED", None, {"error": repr(exc)})
            self.repository.health("cycle", "FAILED", {"scheduled_at": scheduled_at, "error": repr(exc)})
            raise


async def schedule_forever(runner: CycleRunner, collector: MarketDataCollector,
                           websocket: ResilientWebsocketCollector, *, grace_seconds: int = 5) -> None:
    stop = threading.Event()
    thread = threading.Thread(target=websocket.run_forever, args=(stop,), daemon=True)
    thread.start()
    try:
        while True:
            boundary = next_quarter_hour(datetime.now(UTC))
            delay = max(0.0, (boundary - datetime.now(UTC)).total_seconds() + grace_seconds)
            await asyncio.sleep(delay)
            collector.collect_incremental(observation_class=runner.observation_class())
            collector.recover_gaps(boundary)
            runner.run(boundary, available_at=datetime.now(UTC))
    finally:
        stop.set()
        thread.join(timeout=10)
