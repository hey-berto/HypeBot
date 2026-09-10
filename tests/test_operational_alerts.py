import json
from datetime import UTC, datetime, timedelta

import pytest

from hype_autopilot.alerts import build_operational_alert, dispatch_operational_alert


def test_isolated_non_scored_alert_delivery_is_sanitized_and_debounced(tmp_path):
    delivered = []

    def sender(url, payload, token, timeout):
        delivered.append((url, payload, token, timeout))

    at = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
    first = dispatch_operational_alert(
        component="hypebot-phase2.service",
        classification="RECOVERY_FAILED",
        audit_log=tmp_path / "alerts.jsonl",
        state_path=tmp_path / "state.json",
        webhook_url="https://alerts.invalid/hypebot",
        bearer_token="test-secret-never-persisted",
        observed_at=at,
        hostname="mmt2",
        sender=sender,
    )
    second = dispatch_operational_alert(
        component="hypebot-phase2.service",
        classification="RECOVERY_FAILED",
        audit_log=tmp_path / "alerts.jsonl",
        state_path=tmp_path / "state.json",
        webhook_url="https://alerts.invalid/hypebot",
        bearer_token="test-secret-never-persisted",
        observed_at=at + timedelta(minutes=1),
        hostname="mmt2",
        sender=sender,
    )

    assert first["delivery"] == "DELIVERED"
    assert second["delivery"] == "SUPPRESSED_COOLDOWN"
    assert len(delivered) == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "alerts.jsonl").read_text().splitlines()
    ]
    assert [row["delivery"] for row in rows] == ["DELIVERED", "SUPPRESSED_COOLDOWN"]
    persisted = (tmp_path / "alerts.jsonl").read_text() + (
        tmp_path / "state.json"
    ).read_text()
    assert "test-secret-never-persisted" not in persisted
    assert set(delivered[0][1]) == {
        "alert_id",
        "alert_version",
        "classification",
        "component",
        "hostname",
        "observed_at",
        "severity",
    }


@pytest.mark.parametrize(
    "classification", ["CYCLE_REJECTED", "TRADE_LOSS", "LLM_INVALID"]
)
def test_noisy_or_research_alert_classifications_are_rejected(classification):
    with pytest.raises(ValueError, match="not eligible"):
        build_operational_alert(
            component="hypebot-phase2.service",
            classification=classification,
            observed_at=datetime.now(UTC),
            hostname="mmt2",
        )


def test_required_external_delivery_fails_closed_without_endpoint(tmp_path):
    with pytest.raises(RuntimeError, match="endpoint is not configured"):
        dispatch_operational_alert(
            component="hypebot-phase2.service",
            classification="SERVICE_FAILED",
            audit_log=tmp_path / "alerts.jsonl",
            state_path=tmp_path / "state.json",
            webhook_url=None,
            observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            hostname="mmt2",
        )
    row = json.loads((tmp_path / "alerts.jsonl").read_text())
    assert row["delivery"] == "FAILED_NO_ENDPOINT"
