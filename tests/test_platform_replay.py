from pathlib import Path

from hype_autopilot.platform_replay import platform_replay_json, run_platform_replay

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "config" / "migration" / "platform_replay_fixture_v1.yaml"


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
    assert (
        "__RESOLVE_APPROVED_PHASE2_PRODUCTION_SUPERVISOR_COMMAND_BEFORE_INSTALL__"
        in phase2
    )
    assert not (ROOT / "deploy/systemd/hypebot-phase1.service").exists()
    assert not (ROOT / "deploy/systemd/hypebot-phase2.service").exists()
