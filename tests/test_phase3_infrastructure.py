from datetime import UTC, datetime
from pathlib import Path

import pytest

from hype_autopilot.phase3.infrastructure import initialize_frozen_infrastructure

ROOT = Path(__file__).resolve().parents[1]


def test_initialization_records_frozen_identities_without_running_batch(tmp_path):
    project = tmp_path / "project"
    (project / "config").parent.mkdir(parents=True)
    (project / "config").symlink_to(ROOT / "config", target_is_directory=True)
    result = initialize_frozen_infrastructure(
        project,
        implementation_commit="a" * 40,
        registered_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    assert result["batch_status"] == "FROZEN_NOT_EXECUTED"
    assert result["lab_run_count"] == 0
    assert result["risk_receipt_count"] == 0
    assert result["stage1_executed"] is False
    assert result["testnet_authorized"] is False
    assert result["mainnet_authorized"] is False
    assert result["lab_integrity"] == ("ok", 0)
    assert result["risk_integrity"] == ("ok", 0)

    repeated = initialize_frozen_infrastructure(
        project,
        implementation_commit="a" * 40,
        registered_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    assert repeated == result


def test_initialization_requires_commit_and_utc_timestamp(tmp_path):
    with pytest.raises(ValueError, match="40-character"):
        initialize_frozen_infrastructure(
            tmp_path,
            implementation_commit="bad",
            registered_at=datetime(2026, 9, 6, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="UTC"):
        initialize_frozen_infrastructure(
            tmp_path,
            implementation_commit="a" * 40,
            registered_at=datetime(2026, 9, 6),  # noqa: DTZ001 - deliberate failure
        )
