from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ubuntu_non_scored_network_acceptance.py"
SPEC = importlib.util.spec_from_file_location("ubuntu_network_acceptance", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_network_acceptance_rejects_production_or_ambiguous_paths(tmp_path):
    assert MODULE.validate_destination(tmp_path / "NON_SCORED_fixture.sqlite3").name
    with pytest.raises(ValueError):
        MODULE.validate_destination(tmp_path / "phase2_epoch_002.sqlite3")
    with pytest.raises(ValueError):
        MODULE.validate_destination(tmp_path / "NON_SCORED_phase2_epoch_002.sqlite3")
    with pytest.raises(ValueError):
        MODULE.validate_destination(tmp_path / "NON_SCORED_fixture.db")
