"""Focused NON_SCORED checks for the epoch006 operational recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from deploy.operations.phase2_epoch006_activation_guard import (
    validate_monotonic_timer_deadline,
)

ROOT = Path(__file__).resolve().parents[1]


def test_epoch006_systemd_pins_only_the_fresh_epoch() -> None:
    service = (ROOT / "deploy/systemd/hypebot-phase2.service.template").read_text()
    health = (
        ROOT / "deploy/systemd/hypebot-phase2-health.service.template"
    ).read_text()
    combined = service + health
    assert "phase2_epoch_006" in combined
    assert "phase2-epoch-006" in combined
    assert "41e9e9acc261fd69d32c2d811e9ab4dd556c4620" in service
    assert "c69cf18d642aec9bd6bbaa2c85d3a571f45550be7c8a252221708af403a7f24c" in service
    assert "phase2_epoch006_start_gate.py" in combined
    assert "phase2_epoch006_runtime_worker.py" in service
    for epoch in ("002", "003", "004", "005"):
        assert f"phase2_epoch_{epoch}" not in combined
        assert f"phase2-epoch-{epoch}" not in combined


@pytest.mark.parametrize("value", ["", " ", "infinity", "Infinity", "0"])
def test_timer_deadline_rejects_missing_or_infinite_values(value: str) -> None:
    with pytest.raises(ValueError, match="finite monotonic"):
        validate_monotonic_timer_deadline(value)


@pytest.mark.parametrize("value", ["5min", "1h 2min 3s", "123456789us"])
def test_timer_deadline_accepts_finite_monotonic_values(value: str) -> None:
    assert validate_monotonic_timer_deadline(value) == value


def test_activation_orchestrator_uses_monotonic_timer_and_full_readiness() -> None:
    script = (ROOT / "deploy/operations/phase2_epoch006_activate.sh").read_text()
    assert "NextElapseUSecMonotonic" in script
    assert "NextElapseUSecRealtime" not in script
    assert "READINESS_WINDOW_SECONDS=120" in script
    assert "MONITOR_ALLOWANCE_SECONDS=10" in script
    assert "SECONDS + READINESS_WINDOW_SECONDS + MONITOR_ALLOWANCE_SECONDS" in script
    assert "phase2_epoch006_activation_guard.py" in script
    assert "Do not delete, reset, retry, or reuse epoch006 artifacts." in script


def test_activation_creator_is_fresh_and_never_reuses_epoch005() -> None:
    creator = (
        ROOT / "deploy/operations/phase2_epoch006_create_activation.py"
    ).read_text()
    assert "phase2_epoch_006" in creator
    assert "phase2-epoch-006" in creator
    assert "phase2_epoch_005" not in creator
    assert "phase2-epoch-005" not in creator
    assert "os.O_EXCL" in creator
    assert "config.assert_build_only()" in creator
