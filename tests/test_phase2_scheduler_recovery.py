from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hype_autopilot.phase2.isolation import Phase2SQLiteConnection
from hype_autopilot.phase2.scheduler import (
    PROSPECTIVE_START_EVENT,
    FatalPhase2OperationalError,
    account_for_process_downtime,
    establish_prospective_start,
    evidence_window_start,
    planned_phase2_boundary,
    prospective_start,
    record_worker_start_attempt,
    run_phase2_boundary,
    schedule_phase2_forever,
)
from hype_autopilot.phase2.storage import Phase2Repository

EPOCH = "phase2_epoch_non_scored_scheduler_fixture"


def _repository() -> Phase2Repository:
    db = sqlite3.connect(":memory:", factory=Phase2SQLiteConnection)
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


def _attempt(pipeline, now: datetime, identity: str) -> str:
    record_worker_start_attempt(
        pipeline, now=now, source_identity=identity
    )
    return identity


def test_fresh_start_ignores_old_manifest_and_never_accounts_prestart_boundaries():
    repository = _repository()
    pipeline = _pipeline(repository)
    activation = datetime(2026, 9, 10, 0, 55, tzinfo=UTC)
    manifest = SimpleNamespace(activation_timestamp=activation)
    now = datetime(2026, 9, 10, 4, 38, tzinfo=UTC)

    attempt = _attempt(pipeline, now, "fresh-attempt")
    first = account_for_process_downtime(
        pipeline,
        manifest=manifest,
        now=now,
        worker_attempt_identity=attempt,
    )
    assert first.accounted == ()
    assert prospective_start(pipeline) == now
    assert planned_phase2_boundary(now) == datetime(2026, 9, 10, 4, 45, tzinfo=UTC)
    assert evidence_window_start(pipeline) == datetime(
        2026, 9, 10, 4, 45, tzinfo=UTC
    )
    assert (
        repository.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 0
    )
    assert (
        repository.db.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0]
        == 0
    )
    assert (
        repository.db.execute(
            "SELECT COUNT(*) FROM llm_invocation_attempts"
        ).fetchone()[0]
        == 0
    )
    assert (
        repository.db.execute("SELECT COUNT(*) FROM llm_decisions").fetchone()[0] == 0
    )
    events = repository.db.execute(
        "SELECT event_type,source_identity FROM phase2_recovery_events"
    ).fetchall()
    assert sorted(
        (row["event_type"], row["source_identity"]) for row in events
    ) == sorted([
        ("WORKER_START_ATTEMPT", "fresh-attempt"),
        (PROSPECTIVE_START_EVENT, EPOCH),
    ])

    # A direct call cannot invoke any provider for an earlier boundary.
    calls: list[datetime] = []
    pipeline.collect_reconstruct_and_score = lambda *, boundary, manifest: calls.append(
        boundary
    )
    with pytest.raises(FatalPhase2OperationalError, match="predates"):
        run_phase2_boundary(
            pipeline,
            manifest=manifest,
            boundary=datetime(2026, 9, 10, 4, 30, tzinfo=UTC),
        )
    assert calls == []
    assert (
        repository.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 0
    )


def test_restarted_active_epoch_accounts_only_genuine_poststart_downtime():
    repository = _repository()
    pipeline = _pipeline(repository)
    manifest = SimpleNamespace(
        activation_timestamp=datetime(2026, 9, 10, 0, 55, tzinfo=UTC)
    )
    start = datetime(2026, 9, 10, 4, 38, tzinfo=UTC)
    first_attempt = _attempt(pipeline, start, "first-attempt")
    assert (
        account_for_process_downtime(
            pipeline,
            manifest=manifest,
            now=start,
            worker_attempt_identity=first_attempt,
        ).accounted
        == ()
    )
    first_boundary = datetime(2026, 9, 10, 4, 45, tzinfo=UTC)
    cycle_id = "non-scored-fixture-completed-boundary"
    assert (
        repository.core.begin_cycle(cycle_id, first_boundary, "SCORED_PROSPECTIVE")
        is None
    )
    repository.core.finish_cycle(cycle_id, "COMPLETE", None, {"scoreable": False})
    now = datetime(2026, 9, 10, 5, 2, tzinfo=UTC)
    restart_attempt = _attempt(pipeline, now, "restart-attempt")
    first = account_for_process_downtime(
        pipeline,
        manifest=manifest,
        now=now,
        worker_attempt_identity=restart_attempt,
    )
    second = account_for_process_downtime(
        pipeline,
        manifest=manifest,
        now=now,
        worker_attempt_identity=restart_attempt,
    )

    assert first.accounted == ("2026-09-10T05:00:00+00:00",)
    assert second.accounted == ()
    rows = repository.db.execute(
        "SELECT scheduled_at,status,snapshot_hash,details_json FROM research_cycles "
        "ORDER BY scheduled_at"
    ).fetchall()
    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["COMPLETE", "REJECTED"]
    assert rows[1]["snapshot_hash"] is None
    assert all(
        datetime.fromisoformat(row["scheduled_at"]).minute % 15 == 0 for row in rows
    )
    for row in rows[1:]:
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
    assert (
        repository.db.execute(
            "SELECT COUNT(*) FROM phase2_recovery_events "
            "WHERE event_type='PROCESS_DOWNTIME_ACCOUNTED'"
        ).fetchone()[0]
        == 1
    )
    # A backward clock adjustment cannot add or mutate historical rows.
    account_for_process_downtime(
        pipeline,
        manifest=manifest,
        now=datetime(2026, 9, 10, 4, 50, tzinfo=UTC),
        worker_attempt_identity=restart_attempt,
    )
    assert (
        repository.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 2
    )
    assert prospective_start(pipeline) == start


def test_exact_boundary_planning_remains_strictly_prospective():
    exact = datetime(2026, 9, 10, 1, 0, tzinfo=UTC)
    assert planned_phase2_boundary(exact) == exact + timedelta(minutes=15)


def test_failed_pre_window_attempt_cannot_be_reset_by_a_later_worker():
    repository = _repository()
    pipeline = _pipeline(repository)
    manifest = SimpleNamespace(
        activation_timestamp=datetime(2026, 9, 10, 4, 30, tzinfo=UTC)
    )
    first = datetime(2026, 9, 10, 4, 38, tzinfo=UTC)
    second = datetime(2026, 9, 10, 4, 40, tzinfo=UTC)
    _attempt(pipeline, first, "failed-before-readiness")
    current = _attempt(pipeline, second, "automatic-restart")

    with pytest.raises(FatalPhase2OperationalError, match="failed pre-window"):
        establish_prospective_start(
            pipeline,
            manifest=manifest,
            now=second,
            worker_attempt_identity=current,
        )

    assert prospective_start(pipeline) is None
    assert evidence_window_start(pipeline) is None
    assert repository.db.execute(
        "SELECT COUNT(*) FROM phase2_evidence_windows"
    ).fetchone()[0] == 0


def test_evidence_window_is_single_immutable_and_never_moves_after_failures():
    repository = _repository()
    pipeline = _pipeline(repository)
    manifest = SimpleNamespace(
        activation_timestamp=datetime(2026, 9, 10, 4, 30, tzinfo=UTC)
    )
    start = datetime(2026, 9, 10, 4, 38, tzinfo=UTC)
    attempt = _attempt(pipeline, start, "initial-attempt")
    establish_prospective_start(
        pipeline,
        manifest=manifest,
        now=start,
        worker_attempt_identity=attempt,
    )
    expected = datetime(2026, 9, 10, 4, 45, tzinfo=UTC)
    before = repository.db.execute(
        "SELECT payload_json,integrity_hash FROM phase2_evidence_windows"
    ).fetchone()

    later = datetime(2026, 9, 10, 6, 2, tzinfo=UTC)
    restart = _attempt(pipeline, later, "later-restart")
    account_for_process_downtime(
        pipeline,
        manifest=manifest,
        now=later,
        worker_attempt_identity=restart,
    )
    after = repository.db.execute(
        "SELECT payload_json,integrity_hash FROM phase2_evidence_windows"
    ).fetchone()
    assert evidence_window_start(pipeline) == expected
    assert tuple(after) == tuple(before)
    assert repository.db.execute(
        "SELECT COUNT(*) FROM phase2_evidence_windows"
    ).fetchone()[0] == 1

    with pytest.raises(RuntimeError, match="immutable.*window conflict"):
        repository.establish_initial_evidence_window(
            phase2_epoch_id=EPOCH,
            prospective_start=start + timedelta(minutes=15),
            prospective_start_integrity_hash="f" * 64,
        )
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        repository.db.execute("DELETE FROM phase2_evidence_windows")
    repository.db.rollback()


def test_non_scored_scheduler_start_at_0438_runs_only_0445(monkeypatch):
    repository = _repository()
    pipeline = _pipeline(repository)
    manifest = SimpleNamespace(
        activation_timestamp=datetime(2026, 9, 10, 0, 55, tzinfo=UTC)
    )
    now = datetime(2026, 9, 10, 4, 38, tzinfo=UTC)
    stop = threading.Event()
    attempted: list[datetime] = []

    def non_scored_fixture(*, boundary, manifest):
        attempted.append(boundary)
        stop.set()
        raise RuntimeError("non-scored fixture does not collect market data")

    async def elapsed(awaitable, *, timeout):
        del timeout
        awaitable.close()
        raise TimeoutError

    pipeline.collect_reconstruct_and_score = non_scored_fixture
    monkeypatch.setattr("hype_autopilot.phase2.scheduler.asyncio.wait_for", elapsed)
    attempt = _attempt(pipeline, now, "scheduler-attempt")
    asyncio.run(
        schedule_phase2_forever(
            pipeline,
            manifest=manifest,
            worker_attempt_identity=attempt,
            stop=stop,
            clock=lambda: now,
        )
    )
    assert attempted == [datetime(2026, 9, 10, 4, 45, tzinfo=UTC)]
    rows = repository.db.execute(
        "SELECT scheduled_at,status FROM research_cycles ORDER BY scheduled_at"
    ).fetchall()
    assert [(row["scheduled_at"], row["status"]) for row in rows] == [
        ("2026-09-10T04:45:00+00:00", "REJECTED")
    ]
    assert (
        repository.db.execute(
            "SELECT COUNT(*) FROM phase2_recovery_events "
            "WHERE event_type='PROCESS_DOWNTIME_ACCOUNTED'"
        ).fetchone()[0]
        == 0
    )
    assert (
        repository.db.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0]
        == 0
    )
    assert (
        repository.db.execute(
            "SELECT COUNT(*) FROM llm_invocation_attempts"
        ).fetchone()[0]
        == 0
    )


def test_existing_rows_without_start_anchor_fail_closed_instead_of_reinterpreting_history():
    repository = _repository()
    pipeline = _pipeline(repository)
    repository.core.begin_cycle(
        "historical-fixture",
        datetime(2026, 9, 10, 4, 30, tzinfo=UTC),
        "SCORED_PROSPECTIVE",
    )
    manifest = SimpleNamespace(
        activation_timestamp=datetime(2026, 9, 10, 0, 55, tzinfo=UTC)
    )
    attempt = _attempt(
        pipeline, datetime(2026, 9, 10, 4, 38, tzinfo=UTC), "bad-history-attempt"
    )
    with pytest.raises(FatalPhase2OperationalError, match="existing evidence"):
        establish_prospective_start(
            pipeline,
            manifest=manifest,
            now=datetime(2026, 9, 10, 4, 38, tzinfo=UTC),
            worker_attempt_identity=attempt,
        )
    assert prospective_start(pipeline) is None
    assert repository.db.execute(
        "SELECT event_type FROM phase2_recovery_events"
    ).fetchall()[0][0] == "WORKER_START_ATTEMPT"


@pytest.mark.parametrize(
    ("stage", "configure"),
    (
        (
            "ASSERT_ACTIVE_MANIFEST",
            lambda pipeline: setattr(
                pipeline,
                "assert_active_manifest",
                lambda _manifest: (_ for _ in ()).throw(
                    PermissionError("identity drift")
                ),
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
def test_fatal_manifest_and_recovery_failures_are_structured_and_stop(stage, configure):
    repository = _repository()
    pipeline = _pipeline(repository)
    boundary = datetime(2026, 9, 10, 1, 15, tzinfo=UTC)
    attempt = _attempt(
        pipeline, datetime(2026, 9, 10, 1, 7, tzinfo=UTC), f"{stage}-attempt"
    )
    establish_prospective_start(
        pipeline,
        manifest=SimpleNamespace(
            activation_timestamp=datetime(2026, 9, 10, 1, 0, tzinfo=UTC)
        ),
        now=datetime(2026, 9, 10, 1, 7, tzinfo=UTC),
        worker_attempt_identity=attempt,
    )
    configure(pipeline)

    with pytest.raises(FatalPhase2OperationalError, match="audit_persisted=True"):
        run_phase2_boundary(pipeline, manifest=object(), boundary=boundary)

    assert (
        repository.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 0
    )
    row = repository.db.execute(
        "SELECT event_type,source_identity,payload_json FROM phase2_recovery_events "
        "WHERE event_type='FATAL_RECOVERY_FAILED'"
    ).fetchone()
    assert row["event_type"] == "FATAL_RECOVERY_FAILED"
    assert row["source_identity"] == f"{stage}:{boundary.isoformat()}"
    payload = json.loads(row["payload_json"])["details"]
    assert payload["severity"] == "FATAL"
    assert payload["classification"] == "RECOVERY_FAILED"
    assert payload["scheduler_action"] == "STOP_PROCESS"
    assert payload["stage"] == stage
