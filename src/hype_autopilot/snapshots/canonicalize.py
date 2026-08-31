from __future__ import annotations

from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.snapshots.models import DecisionSnapshot


def snapshot_payload(snapshot: DecisionSnapshot) -> str:
    return canonical_json(snapshot.model_dump(mode="python", exclude={"snapshot_hash"}))


def snapshot_hash(snapshot: DecisionSnapshot) -> str:
    return sha256_canonical(snapshot.model_dump(mode="python", exclude={"snapshot_hash"}))


def freeze_snapshot(snapshot: DecisionSnapshot) -> DecisionSnapshot:
    expected = snapshot_hash(snapshot)
    if snapshot.snapshot_hash is not None and snapshot.snapshot_hash != expected:
        raise ValueError("supplied snapshot hash does not match canonical payload")
    return snapshot.model_copy(update={"snapshot_hash": expected})
