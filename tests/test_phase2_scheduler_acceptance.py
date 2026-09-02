from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hype_autopilot.phase2.acceptance import (
    NON_SCORED_SCHEDULER_ACCEPTANCE,
    run_scheduler_acceptance,
)
from hype_autopilot.phase2.scheduler import planned_phase2_boundary

WORKSPACE = Path(__file__).resolve().parents[1]


def test_planned_boundary_is_strictly_prospective_and_quarter_hour_aligned():
    now = datetime(2026, 9, 2, 0, 7, 30, tzinfo=UTC)
    boundary = planned_phase2_boundary(now)
    assert boundary == datetime(2026, 9, 2, 0, 15, tzinfo=UTC)
    assert boundary > now
    assert boundary.minute % 15 == 0
    assert boundary.second == 0 and boundary.microsecond == 0


def test_non_scored_scheduler_acceptance_contains_failure_and_is_idempotent():
    relative = Path("data/phase2") / f"pytest-scheduler-{uuid4()}.sqlite3"
    database = WORKSPACE / relative
    try:
        result = run_scheduler_acceptance(workspace=WORKSPACE, database_path=relative)
        assert result["audit_class"] == NON_SCORED_SCHEDULER_ACCEPTANCE
        assert result["permanently_non_scored"] is True
        assert result["boundaries"] == [
            "2026-09-02T00:15:00+00:00",
            "2026-09-02T00:30:00+00:00",
        ]
        assert result["statuses"] == {
            "first": "COMPLETE",
            "same_process_duplicate": "DUPLICATE_SKIPPED",
            "restart_duplicate": "DUPLICATE_SKIPPED",
            "post_failure_next_boundary": "COMPLETE",
        }
        assert result["provider_calls"] == 2
        assert [item["reason_code"] for item in result["llm_decisions"]] == [
            "TIMEOUT",
            "NONE",
        ]
        assert all(item["decision_count"] == 5 for item in result["strategy_counts"])
        assert all(item["hybrid_count"] == 2 for item in result["strategy_counts"])
        assert result["production_config_gates"] == {
            "evidence_collection_enabled": False,
            "activation_authorized": False,
        }
        assert result["integrity"] == "ok"
        assert result["foreign_key_errors"] == 0
    finally:
        database.unlink(missing_ok=True)
        database.with_name(f"{database.name}-wal").unlink(missing_ok=True)
        database.with_name(f"{database.name}-shm").unlink(missing_ok=True)
