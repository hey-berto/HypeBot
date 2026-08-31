from __future__ import annotations

from datetime import datetime
from typing import Mapping

from hype_autopilot.clock import ensure_utc
from hype_autopilot.snapshots.models import DataQuality


def assess_freshness(
    snapshot_at: datetime,
    source_cutoffs: Mapping[str, datetime | None],
    maximum_age_seconds: Mapping[str, int],
    required_sources: set[str],
    optional_sources: set[str],
    extra_rejections: tuple[str, ...] = (),
) -> DataQuality:
    snapshot_at = ensure_utc(snapshot_at)
    stale: list[str] = []
    missing_required: list[str] = []
    missing_optional: list[str] = []
    ages: dict[str, float] = {}
    for source in sorted(required_sources | optional_sources):
        cutoff = source_cutoffs.get(source)
        if cutoff is None:
            (missing_required if source in required_sources else missing_optional).append(source)
            continue
        age = max(0.0, (snapshot_at - ensure_utc(cutoff)).total_seconds())
        ages[source] = age
        limit = maximum_age_seconds.get(source)
        if limit is not None and age > limit:
            if source in required_sources:
                stale.append(source)
            else:
                missing_optional.append(source)
    reasons = [*(f"MISSING_REQUIRED:{name}" for name in missing_required),
               *(f"STALE_REQUIRED:{name}" for name in stale), *extra_rejections]
    return DataQuality(
        required_sources_present=not missing_required,
        stale_sources=tuple(stale),
        missing_optional_fields=tuple(sorted(set(missing_optional))),
        source_max_age_seconds=ages,
        scoreable=not reasons,
        rejection_reasons=tuple(reasons),
    )
