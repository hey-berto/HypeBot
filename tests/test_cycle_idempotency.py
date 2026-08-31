from datetime import timedelta

from hype_autopilot.config import load_yaml
from hype_autopilot.operations import CycleRunner
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.builder import SnapshotBuilder
from tests.helpers import memory_repo, populate_scoreable


def test_cycle_restart_is_idempotent_and_unscored_without_active_epoch():
    repo = memory_repo()
    at = populate_scoreable(repo)
    runner = CycleRunner(repo, SnapshotBuilder(
        repo, load_yaml("config/base.yaml"), load_yaml("config/epoch_001.yaml")
    ), PaperSimulator(repo))
    first = runner.run(at, available_at=at + timedelta(seconds=1))
    second = runner.run(at, available_at=at + timedelta(minutes=5))
    assert first.snapshot_hash == second.snapshot_hash
    assert first.observation_class == "SOAK"
    assert repo.db.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0] == 1
    assert repo.db.execute("SELECT COUNT(*) FROM strategy_decisions").fetchone()[0] == 2
    assert repo.db.execute("SELECT COUNT(*) FROM detector_decisions").fetchone()[0] == 1
