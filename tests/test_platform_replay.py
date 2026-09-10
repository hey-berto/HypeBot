from pathlib import Path

from hype_autopilot.platform_replay import platform_replay_json, run_platform_replay

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "config" / "migration" / "platform_replay_fixture_v1.yaml"
REVIEWED_COMMIT = "0a9eb262bb77980106cbfad03336dd4209a34308"


def test_platform_replay_is_byte_deterministic_and_complete():
    first = platform_replay_json(ROOT, FIXTURE)
    second = platform_replay_json(ROOT, FIXTURE)
    assert first == second
    result = run_platform_replay(ROOT, FIXTURE)
    assert result["snapshot_hash"]
    assert result["canonical_snapshot_json"]
    assert result["normalized_hype_features"]
    assert result["normalized_btc_features"]
    assert len(result["quant_decisions"]) == 2
    assert result["detector_output"]
    assert len(result["raw_input_hash"]) == 64
    assert len(result["replay_hash"]) == 64


def test_systemd_templates_are_inactive_and_phase2_fails_closed():
    phase1 = (ROOT / "deploy/systemd/hypebot-phase1.service.template").read_text()
    phase2 = (ROOT / "deploy/systemd/hypebot-phase2.service.template").read_text()
    assert "ConditionPathExists=/etc/hypebot/authorized/" in phase1
    assert "ConditionPathExists=/etc/hypebot/authorized/" in phase2
    assert "hype-autopilot-phase2-supervisor" in phase2
    assert "hype-autopilot-phase2-worker" in phase2
    assert "/usr/bin/caffeinate" not in phase2
    assert "__PHASE2_INTEGRATION_REVIEW_COMMIT__" not in phase2
    assert phase2.count(f"--expected-commit {REVIEWED_COMMIT}") == 2
    assert (
        "ConditionPathExists=/etc/hypebot/authorized/phase2-epoch-002.grant.json"
        in phase2
    )
    assert not (ROOT / "deploy/systemd/hypebot-phase1.service").exists()
    assert not (ROOT / "deploy/systemd/hypebot-phase2.service").exists()
    runbook = (ROOT / "docs/ubuntu-migration-runbook.md").read_text()
    assert REVIEWED_COMMIT in runbook
    assert "worktree add --detach /opt/hypebot/phase2 0322e53" not in runbook


def test_alert_template_uses_repository_adapter_and_fatal_debounce():
    alert = (ROOT / "deploy/systemd/hypebot-alert@.service.template").read_text()
    assert "hype-autopilot-alert" in alert
    assert "/opt/hypebot/operations/.venv/bin/hype-autopilot-alert" in alert
    assert "--classification SERVICE_FAILED" in alert
    assert "--cooldown-seconds 900" in alert
    assert "__INSTALL_LOCAL_ALERT_COMMAND" not in alert
    assert "ReadWritePaths=/var/log/hypebot /var/lib/hypebot/alerts" in alert
