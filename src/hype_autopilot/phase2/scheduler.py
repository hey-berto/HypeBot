from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.clock import floor_quarter_hour, next_quarter_hour
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.pipeline import Phase2Pipeline


class FatalPhase2OperationalError(RuntimeError):
    """A runtime-integrity failure that must stop the scheduler process."""


@dataclass(frozen=True)
class DowntimeAccountingSummary:
    accounted: tuple[str, ...] = ()
    already_accounted: tuple[str, ...] = ()


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
) -> DowntimeAccountingSummary:
    """Persist audit-only rows for elapsed boundaries missed while fully offline.

    This function never collects data, creates snapshots, invokes the provider,
    scores decisions, or progresses simulator state.
    """
    pipeline.assert_active_manifest(manifest)
    now = now.astimezone(UTC)
    first_boundary = next_quarter_hour(manifest.activation_timestamp.astimezone(UTC))
    latest = pipeline.repository.core.latest_cycle_time(
        ObservationClass.SCORED_PROSPECTIVE.value
    )
    cursor = (
        latest.astimezone(UTC) + timedelta(minutes=15)
        if latest is not None
        else first_boundary
    )
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
    stop: threading.Event | None = None,
    grace_seconds: int = 5,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Dense prospective scheduler; activation gate is checked before the first wait or call."""
    clock = clock or (lambda: datetime.now(UTC))
    startup_boundary = next_quarter_hour(clock().astimezone(UTC))
    try:
        pipeline.assert_active_manifest(manifest)
        account_for_process_downtime(pipeline, manifest=manifest, now=clock())
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
