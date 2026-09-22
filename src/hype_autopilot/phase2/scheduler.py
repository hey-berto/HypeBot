from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.clock import ensure_utc, floor_quarter_hour, next_quarter_hour
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.pipeline import Phase2Pipeline
from hype_autopilot.phase2.storage import INITIAL_EVIDENCE_WINDOW_RULE


class FatalPhase2OperationalError(RuntimeError):
    """A runtime-integrity failure that must stop the scheduler process."""


@dataclass(frozen=True)
class DowntimeAccountingSummary:
    accounted: tuple[str, ...] = ()
    already_accounted: tuple[str, ...] = ()


PROSPECTIVE_START_EVENT = "PROSPECTIVE_START_ESTABLISHED"
WORKER_START_ATTEMPT_EVENT = "WORKER_START_ATTEMPT"


def record_worker_start_attempt(
    pipeline: Phase2Pipeline, *, now: datetime, source_identity: str
) -> str:
    """Persist the current process attempt before data-readiness can fail."""
    return pipeline.repository.record_recovery_event(
        phase2_epoch_id=pipeline.config.phase2_epoch_id,
        event_type=WORKER_START_ATTEMPT_EVENT,
        source_identity=source_identity,
        payload={"worker_started_at": ensure_utc(now).isoformat()},
        occurred_at=ensure_utc(now),
    )


def prospective_start(pipeline: Phase2Pipeline) -> datetime | None:
    """Read the immutable first-worker-start anchor, never the earlier grant time."""
    rows = pipeline.repository.db.execute(
        "SELECT occurred_at,payload_json,integrity_hash FROM phase2_recovery_events "
        "WHERE phase2_epoch_id=? AND event_type=? AND source_identity=?",
        (
            pipeline.config.phase2_epoch_id,
            PROSPECTIVE_START_EVENT,
            pipeline.config.phase2_epoch_id,
        ),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise FatalPhase2OperationalError("ambiguous prospective-start anchor")
    row = rows[0]
    body = json.loads(row["payload_json"])
    if (
        sha256_canonical(body) != row["integrity_hash"]
        or body.get("phase2_epoch_id") != pipeline.config.phase2_epoch_id
        or body.get("event_type") != PROSPECTIVE_START_EVENT
        or body.get("source_identity") != pipeline.config.phase2_epoch_id
        or body.get("occurred_at") != row["occurred_at"]
        or body.get("details", {}).get("prospective_start") != row["occurred_at"]
    ):
        raise FatalPhase2OperationalError("invalid prospective-start anchor")
    return ensure_utc(datetime.fromisoformat(row["occurred_at"]))


def evidence_window_start(pipeline: Phase2Pipeline) -> datetime | None:
    rows = pipeline.repository.db.execute(
        "SELECT first_eligible_boundary,prospective_start_integrity_hash,"
        "established_at,rule_version,reason_code,payload_json,integrity_hash "
        "FROM phase2_evidence_windows WHERE phase2_epoch_id=?",
        (pipeline.config.phase2_epoch_id,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise FatalPhase2OperationalError("ambiguous evidence-window start")
    start = prospective_start(pipeline)
    if start is None:
        raise FatalPhase2OperationalError("evidence window exists without start anchor")
    anchor = pipeline.repository.db.execute(
        "SELECT integrity_hash FROM phase2_recovery_events "
        "WHERE phase2_epoch_id=? AND event_type=? AND source_identity=?",
        (
            pipeline.config.phase2_epoch_id,
            PROSPECTIVE_START_EVENT,
            pipeline.config.phase2_epoch_id,
        ),
    ).fetchone()
    row = rows[0]
    body = json.loads(row["payload_json"])
    boundary = ensure_utc(datetime.fromisoformat(row["first_eligible_boundary"]))
    if (
        sha256_canonical(body) != row["integrity_hash"]
        or anchor is None
        or row["prospective_start_integrity_hash"] != anchor["integrity_hash"]
        or row["established_at"] != start.isoformat()
        or row["rule_version"] != INITIAL_EVIDENCE_WINDOW_RULE
        or row["reason_code"] != "INITIAL_PROSPECTIVE_START"
        or boundary != planned_phase2_boundary(start)
    ):
        raise FatalPhase2OperationalError("invalid immutable evidence window")
    return boundary


def establish_prospective_start(
    pipeline: Phase2Pipeline,
    *,
    manifest: Phase2Manifest,
    now: datetime,
    worker_attempt_identity: str,
) -> tuple[datetime, bool]:
    """Atomically persist one start and its immutable first eligible boundary."""
    now = ensure_utc(now)
    existing = prospective_start(pipeline)
    if existing is not None:
        if existing < manifest.activation_timestamp.astimezone(UTC):
            raise FatalPhase2OperationalError("start predates authorization manifest")
        if evidence_window_start(pipeline) != planned_phase2_boundary(existing):
            raise FatalPhase2OperationalError("evidence window/start mismatch")
        return existing, False
    if now < manifest.activation_timestamp.astimezone(UTC):
        raise FatalPhase2OperationalError(
            "worker clock predates authorization manifest"
        )
    attempts = pipeline.repository.db.execute(
        "SELECT source_identity FROM phase2_recovery_events "
        "WHERE phase2_epoch_id=? AND event_type=? ORDER BY occurred_at",
        (pipeline.config.phase2_epoch_id, WORKER_START_ATTEMPT_EVENT),
    ).fetchall()
    if len(attempts) != 1 or attempts[0]["source_identity"] != worker_attempt_identity:
        raise FatalPhase2OperationalError(
            "activation attempt is ambiguous or follows a failed pre-window start"
        )
    for table in (
        "research_cycles",
        "decision_snapshots",
        "llm_invocation_attempts",
        "llm_decisions",
        "strategy_decisions",
        "detector_decisions",
        "paper_trades",
    ):
        if pipeline.repository.db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            raise FatalPhase2OperationalError(
                "existing evidence without a prospective-start anchor"
            )
    unexpected = pipeline.repository.db.execute(
        "SELECT 1 FROM phase2_recovery_events WHERE phase2_epoch_id=? "
        "AND NOT (event_type=? AND source_identity=?) LIMIT 1",
        (
            pipeline.config.phase2_epoch_id,
            WORKER_START_ATTEMPT_EVENT,
            worker_attempt_identity,
        ),
    ).fetchone()
    if unexpected is not None:
        raise FatalPhase2OperationalError(
            "existing recovery evidence without a prospective-start anchor"
        )
    with pipeline.repository.atomic():
        pipeline.repository.record_recovery_event(
            phase2_epoch_id=pipeline.config.phase2_epoch_id,
            event_type=PROSPECTIVE_START_EVENT,
            source_identity=pipeline.config.phase2_epoch_id,
            payload={"prospective_start": now.isoformat()},
            occurred_at=now,
        )
        anchor = pipeline.repository.db.execute(
            "SELECT integrity_hash FROM phase2_recovery_events "
            "WHERE phase2_epoch_id=? AND event_type=? AND source_identity=?",
            (
                pipeline.config.phase2_epoch_id,
                PROSPECTIVE_START_EVENT,
                pipeline.config.phase2_epoch_id,
            ),
        ).fetchone()
        if anchor is None:
            raise FatalPhase2OperationalError("prospective-start anchor is absent")
        pipeline.repository.establish_initial_evidence_window(
            phase2_epoch_id=pipeline.config.phase2_epoch_id,
            prospective_start=now,
            prospective_start_integrity_hash=anchor["integrity_hash"],
        )
    if prospective_start(pipeline) != now:
        raise FatalPhase2OperationalError("prospective-start persistence mismatch")
    if evidence_window_start(pipeline) != planned_phase2_boundary(now):
        raise FatalPhase2OperationalError("evidence-window persistence mismatch")
    return now, True


def _cycle_id(pipeline: Phase2Pipeline, boundary: datetime) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"phase2-cycle:{pipeline.config.phase2_epoch_id}:{boundary.isoformat()}",
        )
    )


def account_for_process_downtime(
    pipeline: Phase2Pipeline,
    *,
    manifest: Phase2Manifest,
    now: datetime,
    worker_attempt_identity: str,
) -> DowntimeAccountingSummary:
    """Persist audit-only rows for elapsed boundaries missed while fully offline.

    This function never collects data, creates snapshots, invokes the provider,
    scores decisions, or progresses simulator state.
    """
    pipeline.assert_active_manifest(manifest)
    now = now.astimezone(UTC)
    _start, created = establish_prospective_start(
        pipeline,
        manifest=manifest,
        now=now,
        worker_attempt_identity=worker_attempt_identity,
    )
    if created:
        return DowntimeAccountingSummary()
    first_boundary = evidence_window_start(pipeline)
    if first_boundary is None:
        raise FatalPhase2OperationalError("evidence window is not established")
    latest = pipeline.repository.core.latest_cycle_time(
        ObservationClass.SCORED_PROSPECTIVE.value
    )
    cursor = (
        latest.astimezone(UTC) + timedelta(minutes=15)
        if latest is not None
        else first_boundary
    )
    if cursor < first_boundary:
        raise FatalPhase2OperationalError("cycle history predates prospective start")
    elapsed_through = floor_quarter_hour(now)
    accounted: list[str] = []
    existing: list[str] = []
    while cursor <= elapsed_through:
        cycle_id = _cycle_id(pipeline, cursor)
        row = pipeline.repository.core.begin_cycle(
            cycle_id, cursor, ObservationClass.SCORED_PROSPECTIVE.value
        )
        if row is not None:
            existing.append(cursor.isoformat())
            cursor += timedelta(minutes=15)
            continue
        details = {
            "reason": "PROCESS_DOWNTIME",
            "scoreable": False,
            "audit_only": True,
            "market_data_collected": False,
            "snapshot_created": False,
            "provider_invoked": False,
            "decision_created": False,
            "trade_created": False,
            "order_created": False,
            "fill_created": False,
            "historical_backfill": False,
            "detected_at": now.isoformat(),
        }
        pipeline.repository.core.finish_cycle(cycle_id, "REJECTED", None, details)
        pipeline.repository.record_recovery_event(
            phase2_epoch_id=pipeline.config.phase2_epoch_id,
            event_type="PROCESS_DOWNTIME_ACCOUNTED",
            source_identity=cursor.isoformat(),
            payload=details,
            occurred_at=now,
        )
        pipeline.repository.core.health(
            "phase2_scheduler",
            "PROCESS_DOWNTIME",
            {"scheduled_at": cursor.isoformat(), **details},
            at=now,
        )
        accounted.append(cursor.isoformat())
        cursor += timedelta(minutes=15)
    return DowntimeAccountingSummary(tuple(accounted), tuple(existing))


def _raise_fatal(
    pipeline: Phase2Pipeline,
    *,
    boundary: datetime,
    stage: str,
    error: BaseException,
) -> None:
    payload = {
        "severity": "FATAL",
        "classification": "RECOVERY_FAILED",
        "stage": stage,
        "boundary": boundary.astimezone(UTC).isoformat(),
        "runtime_epoch_id": pipeline.config.phase2_epoch_id,
        "error_class": type(error).__name__,
        "error": repr(error),
        "scheduler_action": "STOP_PROCESS",
    }
    persisted = True
    try:
        pipeline.repository.record_recovery_event(
            phase2_epoch_id=pipeline.config.phase2_epoch_id,
            event_type="FATAL_RECOVERY_FAILED",
            source_identity=f"{stage}:{boundary.astimezone(UTC).isoformat()}",
            payload=payload,
            occurred_at=boundary,
        )
        pipeline.repository.core.health(
            "phase2_scheduler", "FATAL_RECOVERY_FAILED", payload, at=boundary
        )
    except Exception:  # noqa: BLE001 - persistence may itself be the fatal defect
        persisted = False
    raise FatalPhase2OperationalError(
        f"fatal Phase 2 operational failure at {stage}; audit_persisted={persisted}"
    ) from error


def planned_phase2_boundary(now: datetime) -> datetime:
    """Return the next prospective boundary; never return or replay history."""
    now = now.astimezone(UTC)
    boundary = next_quarter_hour(now)
    if boundary <= now:
        boundary += timedelta(minutes=15)
    return boundary


def run_phase2_boundary(
    pipeline: Phase2Pipeline,
    *,
    manifest: Phase2Manifest,
    boundary: datetime,
) -> str:
    """Run one idempotent boundary and contain all operational failures."""
    start = prospective_start(pipeline)
    window = evidence_window_start(pipeline)
    if start is None or window is None or boundary.astimezone(UTC) < window:
        raise FatalPhase2OperationalError(
            "boundary predates the immutable prospective start"
        )
    try:
        pipeline.assert_active_manifest(manifest)
    except Exception as exc:  # noqa: BLE001 - all manifest failures are fatal
        _raise_fatal(
            pipeline, boundary=boundary, stage="ASSERT_ACTIVE_MANIFEST", error=exc
        )
    try:
        pipeline.recovery.recover_before(boundary)
    except Exception as exc:  # noqa: BLE001 - all recovery failures are fatal
        _raise_fatal(pipeline, boundary=boundary, stage="RECOVER_BEFORE", error=exc)
    cycle_id = _cycle_id(pipeline, boundary)
    existing = pipeline.repository.core.begin_cycle(
        cycle_id, boundary, ObservationClass.SCORED_PROSPECTIVE.value
    )
    if existing is not None:
        if existing["status"] == "RECOVERY_EXCLUDED":
            return "RECOVERY_EXCLUDED"
        return "DUPLICATE_SKIPPED"
    try:
        result = pipeline.collect_reconstruct_and_score(
            boundary=boundary, manifest=manifest
        )
    except Exception as exc:  # noqa: BLE001 - every boundary must be contained
        pipeline.repository.core.finish_cycle(
            cycle_id,
            "REJECTED",
            None,
            {
                "reason": "PHASE2_BOUNDARY_FAILED",
                "error": repr(exc),
                "scoreable": False,
            },
        )
        return "REJECTED"
    pipeline.repository.core.finish_cycle(
        cycle_id,
        "COMPLETE",
        result.snapshot_hash,
        {
            "scoreable": True,
            "strategy_count": len(result.decisions),
            "submitted_trade_count": result.submitted_trade_count,
        },
    )
    return "COMPLETE"


async def schedule_phase2_forever(
    pipeline: Phase2Pipeline,
    *,
    manifest: Phase2Manifest,
    worker_attempt_identity: str,
    stop: threading.Event | None = None,
    grace_seconds: int = 5,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Dense prospective scheduler; activation gate is checked before the first wait or call."""
    clock = clock or (lambda: datetime.now(UTC))
    startup_boundary = next_quarter_hour(clock().astimezone(UTC))
    try:
        pipeline.assert_active_manifest(manifest)
        account_for_process_downtime(
            pipeline,
            manifest=manifest,
            now=clock(),
            worker_attempt_identity=worker_attempt_identity,
        )
    except FatalPhase2OperationalError:
        raise
    except Exception as exc:  # noqa: BLE001 - startup integrity failures are fatal
        _raise_fatal(
            pipeline,
            boundary=startup_boundary,
            stage="STARTUP_IDENTITY_OR_DOWNTIME_ACCOUNTING",
            error=exc,
        )
    stop = stop or threading.Event()
    while not stop.is_set():
        boundary = planned_phase2_boundary(clock())
        delay = max(0.0, (boundary - clock()).total_seconds() + grace_seconds)
        try:
            await asyncio.wait_for(asyncio.to_thread(stop.wait), timeout=delay)
            continue
        except TimeoutError:
            pass
        run_phase2_boundary(pipeline, manifest=manifest, boundary=boundary)
