from __future__ import annotations

from datetime import datetime
from typing import Any

from hype_autopilot.data.models import ObservationClass
from hype_autopilot.data.repository import Repository
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.snapshots.canonicalize import snapshot_payload
from hype_autopilot.snapshots.models import DecisionSnapshot


def reconstruct_phase2_snapshot(
    repository: Repository,
    *,
    base_config: dict[str, Any],
    epoch_config: dict[str, Any],
    snapshot_at: datetime,
    available_at: datetime,
    observation_class: ObservationClass,
) -> DecisionSnapshot:
    """Reconstruct solely from the isolated repository using frozen Phase 1 code."""
    return SnapshotBuilder(repository, base_config, epoch_config).build(
        snapshot_at,
        available_at=available_at,
        observation_class=observation_class,
    )


def assert_snapshot_parity(left: DecisionSnapshot, right: DecisionSnapshot) -> None:
    if left.snapshot_hash != right.snapshot_hash or snapshot_payload(
        left
    ) != snapshot_payload(right):
        raise AssertionError("snapshot reconstruction parity failure")
