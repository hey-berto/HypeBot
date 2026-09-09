from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.phase2.runner import FailClosedLLMRunner
from hype_autopilot.phase2.storage import Phase2Repository
from hype_autopilot.simulation.models import TradeStatus

RecoveryFaultHook = Callable[[str, dict[str, object]], None]


@dataclass(frozen=True)
class RecoverySummary:
    interrupted_cycles_finalized: int = 0
    raw_responses_recovered: int = 0
    missing_orders_reconciled: int = 0
    outcome_exclusions_added: int = 0
    permanent_exceptions: int = 0


class Phase2RecoveryManager:
    """Deterministic, provider-free reconciliation of incomplete persistence."""

    def __init__(
        self,
        *,
        repository: Phase2Repository,
        llm_runner: FailClosedLLMRunner,
        phase2_epoch_id: str,
        simulator_latency_seconds: int,
        fault_hook: RecoveryFaultHook | None = None,
    ) -> None:
        self.repository = repository
        self.llm_runner = llm_runner
        self.phase2_epoch_id = phase2_epoch_id
        self.simulator_latency_seconds = simulator_latency_seconds
        self.fault_hook = fault_hook or (lambda _stage, _details: None)

    def _fault(self, stage: str, **details: object) -> None:
        self.fault_hook(stage, details)

    def recover_before(self, boundary: datetime) -> RecoverySummary:
        cutoff = boundary.astimezone(UTC).isoformat()
        cycles = self.repository.db.execute(
            "SELECT cycle_id,scheduled_at,observation_class,started_at,details_json "
            "FROM research_cycles WHERE status='RUNNING' AND scheduled_at<=? "
            "ORDER BY scheduled_at",
            (cutoff,),
        ).fetchall()
        finalized = raw_recovered = orders = exclusions = exceptions = 0
        for cycle in cycles:
            with self.repository.atomic():
                snapshot_row = self.repository.db.execute(
                    "SELECT snapshot_hash FROM decision_snapshots "
                    "WHERE epoch_id=? AND snapshot_timestamp=? "
                    "AND observation_class=?",
                    (
                        self.phase2_epoch_id,
                        cycle["scheduled_at"],
                        cycle["observation_class"],
                    ),
                ).fetchone()
                snapshot_hash = snapshot_row["snapshot_hash"] if snapshot_row else None
                recovered_decision = False
                recovery_error: str | None = None
                if snapshot_hash is not None:
                    existing = self.repository.load_llm_decision(
                        self.llm_runner.experiment_id,
                        self.phase2_epoch_id,
                        snapshot_hash,
                        self.llm_runner.config.llm_strategy_version,
                    )
                    if existing is None:
                        snapshot = self.repository.core.load_snapshot(snapshot_hash)
                        try:
                            recovered = self.llm_runner.recover_persisted_response(
                                snapshot
                            )
                        except (ValueError, RuntimeError) as exc:
                            recovery_error = repr(exc)
                            exceptions += 1
                        else:
                            recovered_decision = recovered is not None
                            raw_recovered += int(recovered_decision)
                            if recovered_decision:
                                self._fault(
                                    "AFTER_RAW_DECISION_RECOVERY",
                                    cycle_id=cycle["cycle_id"],
                                    snapshot_hash=snapshot_hash,
                                )

                trade_rows = self.repository.db.execute(
                    "SELECT t.payload_json, o.order_id FROM paper_trades t "
                    "LEFT JOIN paper_orders o ON o.paper_trade_id=t.paper_trade_id "
                    "WHERE (? IS NOT NULL AND t.snapshot_hash=?) ORDER BY t.paper_trade_id",
                    (snapshot_hash, snapshot_hash),
                ).fetchall()
                cycle_exclusions = 0
                cycle_orders = 0
                for row in trade_rows:
                    trade = json.loads(row["payload_json"])
                    if (
                        row["order_id"] is None
                        and trade["status"] == TradeStatus.PENDING_ENTRY.value
                    ):
                        decision = self.repository.core.load_strategy_decision(
                            trade["strategy_decision_id"]
                        )
                        order_id = str(
                            uuid5(NAMESPACE_URL, f"order:{decision.decision_id}")
                        )
                        model = self.repository.core.trade_for_decision(
                            decision.decision_id
                        )
                        assert model is not None
                        self.repository.core.save_order(
                            order_id,
                            model,
                            decision.created_at
                            + timedelta(seconds=self.simulator_latency_seconds),
                        )
                        cycle_orders += 1
                    if trade["status"] in {
                        TradeStatus.PENDING_ENTRY.value,
                        TradeStatus.OPEN.value,
                    }:
                        self.repository.exclude_trade_outcome(
                            paper_trade_id=trade["paper_trade_id"],
                            phase2_epoch_id=self.phase2_epoch_id,
                            reason_code="INTERRUPTED_BOUNDARY_AMBIGUOUS_OUTCOME",
                            source_identity=cycle["cycle_id"],
                        )
                        cycle_exclusions += 1
                orders += cycle_orders
                exclusions += cycle_exclusions
                details = {
                    "reason": "PROCESS_INTERRUPTED",
                    "recovery_status": "PERMANENT_EXCEPTION",
                    "provider_reinvoked": False,
                    "market_data_recollected": False,
                    "snapshot_hash": snapshot_hash,
                    "raw_response_recovered": recovered_decision,
                    "missing_orders_reconciled": cycle_orders,
                    "outcome_exclusions_added": cycle_exclusions,
                    "recovery_error": recovery_error,
                    "original_started_at": cycle["started_at"],
                }
                self.repository.core.finish_cycle(
                    cycle["cycle_id"], "RECOVERY_EXCLUDED", snapshot_hash, details
                )
                self.repository.record_recovery_event(
                    phase2_epoch_id=self.phase2_epoch_id,
                    event_type="INTERRUPTED_CYCLE_FINALIZED",
                    source_identity=cycle["cycle_id"],
                    payload=details,
                )
                self._fault(
                    "AFTER_CYCLE_FINALIZATION",
                    cycle_id=cycle["cycle_id"],
                    snapshot_hash=snapshot_hash,
                )
            finalized += 1

        # A raw response can survive even if an external actor finalized the
        # cycle first. Reconcile this append-only evidence independently.
        orphan_attempts = self.repository.db.execute(
            "SELECT DISTINCT a.input_snapshot_hash FROM llm_invocation_attempts a "
            "LEFT JOIN llm_decisions d ON d.experiment_id=a.experiment_id "
            "AND d.phase2_epoch_id=a.phase2_epoch_id "
            "AND d.input_snapshot_hash=a.input_snapshot_hash "
            "WHERE a.experiment_id=? AND a.phase2_epoch_id=? "
            "AND d.decision_id IS NULL AND a.provider_status='VALID'",
            (self.llm_runner.experiment_id, self.phase2_epoch_id),
        ).fetchall()
        for row in orphan_attempts:
            with self.repository.atomic():
                snapshot = self.repository.core.load_snapshot(
                    row["input_snapshot_hash"]
                )
                try:
                    recovered = self.llm_runner.recover_persisted_response(snapshot)
                except (ValueError, RuntimeError) as exc:
                    exceptions += 1
                    self.repository.record_recovery_event(
                        phase2_epoch_id=self.phase2_epoch_id,
                        event_type="RAW_RESPONSE_RECOVERY_BLOCKED",
                        source_identity=row["input_snapshot_hash"],
                        payload={"error": repr(exc), "provider_reinvoked": False},
                    )
                else:
                    if recovered is not None:
                        raw_recovered += 1
                        self.repository.record_recovery_event(
                            phase2_epoch_id=self.phase2_epoch_id,
                            event_type="RAW_RESPONSE_DECISION_RECOVERED",
                            source_identity=row["input_snapshot_hash"],
                            payload={
                                "provider_reinvoked": False,
                                "decision_status": "VALID_DECISION",
                            },
                        )

        return RecoverySummary(
            interrupted_cycles_finalized=finalized,
            raw_responses_recovered=raw_recovered,
            missing_orders_reconciled=orders,
            outcome_exclusions_added=exclusions,
            permanent_exceptions=exceptions,
        )
