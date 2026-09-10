from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hype_autopilot.phase2.scheduler import (
    FatalPhase2OperationalError,
    account_for_process_downtime,
    planned_phase2_boundary,
    run_phase2_boundary,
)
from hype_autopilot.phase2.storage import Phase2Repository

EPOCH = "phase2_epoch_non_scored_scheduler_fixture"


def _repository() -> Phase2Repository:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    repository = Phase2Repository(db)
    repository.initialize()
    return repository


def _pipeline(repository: Phase2Repository) -> SimpleNamespace:
    return SimpleNamespace(
        repository=repository,
        config=SimpleNamespace(phase2_epoch_id=EPOCH),
        assert_active_manifest=lambda _manifest: None,
        recovery=SimpleNamespace(recover_before=lambda _boundary: None),
    )


def test_downtime_accounting_is_audit_only_idempotent_and_quarter_hour_aligned():
    repository = _repository()
    pipeline = _pipeline(repository)
    activation = datetime(2026, 9, 10, 0, 7, tzinfo=UTC)
    manifest = SimpleNamespace(activation_timestamp=activation)
    now = datetime(2026, 9, 10, 1, 7, tzinfo=UTC)

    first = account_for_process_downtime(
        pipeline, manifest=manifest, now=now
    )
    second = account_for_process_downtime(
        pipeline, manifest=manifest, now=now
    )

    assert first.accounted == (
        "2026-09-10T00:15:00+00:00",
        "2026-09-10T00:30:00+00:00",
        "2026-09-10T00:45:00+00:00",
        "2026-09-10T01:00:00+00:00",
    )
    assert second.accounted == ()
    rows = repository.db.execute(
        "SELECT scheduled_at,status,snapshot_hash,details_json FROM research_cycles "
        "ORDER BY scheduled_at"
    ).fetchall()
    assert len(rows) == 4
    assert all(row["status"] == "REJECTED" for row in rows)
    assert all(row["snapshot_hash"] is None for row in rows)
    assert all(datetime.fromisoformat(row["scheduled_at"]).minute % 15 == 0 for row in rows)
    for row in rows:
        details = json.loads(row["details_json"])
        assert details["reason"] == "PROCESS_DOWNTIME"
        assert details["audit_only"] is True
        assert details["historical_backfill"] is False
        assert not any(
            details[key]
            for key in (
                "market_data_collected",
                "snapshot_created",
                "provider_invoked",
                "decision_created",
                "trade_created",
                "order_created",
                "fill_created",
            )
        )
    for table in (
        "raw_candles",
        "decision_snapshots",
        "strategy_decisions",
        "detector_decisions",
        "llm_invocation_attempts",
        "llm_decisions",
        "paper_trades",
        "paper_orders",
        "paper_fills",
    ):
        assert repository.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert repository.db.execute(
        "SELECT COUNT(*) FROM phase2_recovery_events "
        "WHERE event_type='PROCESS_DOWNTIME_ACCOUNTED'"
    ).fetchone()[0] == 4
    # A backward clock adjustment cannot add or mutate historical rows.
    account_for_process_downtime(
        pipeline,
        manifest=manifest,
        now=datetime(2026, 9, 10, 0, 50, tzinfo=UTC),
    )
    assert repository.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 4


def test_exact_boundary_planning_remains_strictly_prospective():
    exact = datetime(2026, 9, 10, 1, 0, tzinfo=UTC)
    assert planned_phase2_boundary(exact) == exact + timedelta(minutes=15)


@pytest.mark.parametrize(
    ("stage", "configure"),
    (
        (
            "ASSERT_ACTIVE_MANIFEST",
            lambda pipeline: setattr(
                pipeline,
                "assert_active_manifest",
                lambda _manifest: (_ for _ in ()).throw(PermissionError("identity drift")),
            ),
        ),
        (
            "RECOVER_BEFORE",
            lambda pipeline: setattr(
                pipeline,
                "recovery",
                SimpleNamespace(
                    recover_before=lambda _boundary: (_ for _ in ()).throw(
                        RuntimeError("recovery fault")
                    )
                ),
            ),
        ),
    ),
)
def test_fatal_manifest_and_recovery_failures_are_structured_and_stop(
    stage, configure
):
    repository = _repository()
    pipeline = _pipeline(repository)
    configure(pipeline)
    boundary = datetime(2026, 9, 10, 1, 15, tzinfo=UTC)

    with pytest.raises(FatalPhase2OperationalError, match="audit_persisted=True"):
        run_phase2_boundary(pipeline, manifest=object(), boundary=boundary)

    assert repository.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 0
    row = repository.db.execute(
        "SELECT event_type,source_identity,payload_json FROM phase2_recovery_events"
    ).fetchone()
    assert row["event_type"] == "FATAL_RECOVERY_FAILED"
    assert row["source_identity"] == f"{stage}:{boundary.isoformat()}"
    payload = json.loads(row["payload_json"])["details"]
    assert payload["severity"] == "FATAL"
    assert payload["classification"] == "RECOVERY_FAILED"
    assert payload["scheduler_action"] == "STOP_PROCESS"
    assert payload["stage"] == stage
