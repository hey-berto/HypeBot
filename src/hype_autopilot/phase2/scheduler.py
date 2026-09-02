from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.clock import next_quarter_hour
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.pipeline import Phase2Pipeline


def planned_phase2_boundary(now: datetime) -> datetime:
    """Return the next prospective boundary; never return or replay history."""
    now = now.astimezone(UTC)
    boundary = next_quarter_hour(now)
    if boundary <= now:
        raise RuntimeError("Phase 2 scheduler produced a non-prospective boundary")
    return boundary


def run_phase2_boundary(
    pipeline: Phase2Pipeline,
    *,
    manifest: Phase2Manifest,
    boundary: datetime,
) -> str:
    """Run one idempotent boundary and contain all operational failures."""
    pipeline.assert_active_manifest(manifest)
    cycle_id = str(
        uuid5(
            NAMESPACE_URL,
            f"phase2-cycle:{pipeline.config.phase2_epoch_id}:{boundary.isoformat()}",
        )
    )
    existing = pipeline.repository.core.begin_cycle(
        cycle_id, boundary, ObservationClass.SCORED_PROSPECTIVE.value
    )
    if existing is not None:
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
) -> None:
    """Dense prospective scheduler; activation gate is checked before the first wait or call."""
    pipeline.assert_active_manifest(manifest)
    stop = stop or threading.Event()
    while not stop.is_set():
        boundary = planned_phase2_boundary(datetime.now(UTC))
        delay = max(0.0, (boundary - datetime.now(UTC)).total_seconds() + grace_seconds)
        try:
            await asyncio.wait_for(asyncio.to_thread(stop.wait), timeout=delay)
            continue
        except TimeoutError:
            pass
        run_phase2_boundary(pipeline, manifest=manifest, boundary=boundary)
