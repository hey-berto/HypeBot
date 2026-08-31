import sqlite3

import pytest

from hype_autopilot.config import load_yaml
from hype_autopilot.experiments.registry import register_epoch_configuration
from tests.helpers import memory_repo


def test_frozen_version_metadata_is_persisted_without_starting_epoch():
    repo = memory_repo()
    digest = register_epoch_configuration(repo.db, load_yaml("config/epoch_001.yaml"))
    row = repo.db.execute("SELECT * FROM epoch_configurations WHERE config_hash = ?", (digest,)).fetchone()
    assert row["quant_trend_version"] == "QUANT_TREND_V1"
    assert repo.db.execute("SELECT COUNT(*) FROM epochs").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        repo.db.execute("UPDATE epoch_configurations SET detector_version = 'changed'")
