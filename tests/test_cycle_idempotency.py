from datetime import timedelta

from hype_autopilot.config import load_yaml
from hype_autopilot.operations import CycleRunner, account_for_downtime
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.builder import SnapshotBuilder
from tests.helpers import memory_repo, populate_scoreable


def _runner():
    repo = memory_repo()
    at = populate_scoreable(repo)
    runner = CycleRunner(repo, SnapshotBuilder(
        repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml")
    ), PaperSimulator(repo))
    return runner, repo, at


def test_cycle_restart_is_idempotent_and_unscored_without_active_epoch():
    runner, repo, at = _runner()
    first = runner.run(at, available_at=at + timedelta(seconds=1))
    second = runner.run(at, available_at=at + timedelta(minutes=5))
    assert first.snapshot_hash == second.snapshot_hash
    assert first.observation_class == "SOAK"
    assert repo.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 1
    assert repo.db.execute("SELECT COUNT(*) FROM strategy_decisions").fetchone()[0] == 2
    assert repo.db.execute("SELECT COUNT(*) FROM detector_decisions").fetchone()[0] == 1


def test_process_downtime_is_persisted_as_rejected_cycles():
    runner, repo, at = _runner()
    runner.run(at)

    rejected = account_for_downtime(runner, at + timedelta(minutes=31))

    assert rejected == [at + timedelta(minutes=15), at + timedelta(minutes=30)]
    rows = repo.db.execute(
        "SELECT scheduled_at, status, snapshot_hash, details_json FROM research_cycles "
        "ORDER BY scheduled_at"
    ).fetchall()
    assert [row["status"] for row in rows] == ["COMPLETE", "REJECTED", "REJECTED"]
    assert all(row["snapshot_hash"] is None for row in rows[1:])
    assert all("PROCESS_DOWNTIME" in row["details_json"] for row in rows[1:])

    assert account_for_downtime(runner, at + timedelta(minutes=31)) == []
