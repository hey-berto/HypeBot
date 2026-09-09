from __future__ import annotations

from datetime import timedelta

import pytest

from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.storage import phase2_database_schema_hash
from tests.phase2_recovery_harness import FIRST, _connect, create_case, invoke_worker


def test_historical_nonterminal_exclusion_preserves_decision_and_trade_bytes(tmp_path):
    case = tmp_path / "phase2-exclusion"
    create_case(case, scenario="pending_without_bar")
    assert invoke_worker(case, 0)["status"] == "COMPLETE"
    repository = _connect(case)
    trade_rows_before = repository.db.execute(
        "SELECT paper_trade_id,payload_json FROM paper_trades ORDER BY paper_trade_id"
    ).fetchall()
    decision_rows_before = repository.db.execute(
        "SELECT decision_id,payload_json FROM strategy_decisions ORDER BY decision_id"
    ).fetchall()

    first = repository.freeze_predeployment_nonterminal_exclusions(
        phase2_epoch_id="phase2_epoch_002",
        before=FIRST + timedelta(minutes=1),
        source_identity="operational-fix-candidate",
    )
    second = repository.freeze_predeployment_nonterminal_exclusions(
        phase2_epoch_id="phase2_epoch_002",
        before=FIRST + timedelta(minutes=1),
        source_identity="operational-fix-candidate",
    )
    assert first == second and len(first) == 2
    assert repository.core.active_trades() == []
    assert (
        repository.db.execute(
            "SELECT count(*) FROM phase2_evidence_eligible_trades"
        ).fetchone()[0]
        == 0
    )
    assert tuple(
        repository.db.execute(
            "SELECT decision_status,outcome_status FROM ("
            "SELECT json_extract(payload_json,'$.decision_status') decision_status,"
            "json_extract(payload_json,'$.outcome_status') outcome_status "
            "FROM phase2_outcome_exclusions)"
        ).fetchone()
    ) == ("VALID_DECISION", "OUTCOME_EXCLUDED")
    assert [
        tuple(row)
        for row in repository.db.execute(
            "SELECT paper_trade_id,payload_json FROM paper_trades ORDER BY paper_trade_id"
        ).fetchall()
    ] == [tuple(row) for row in trade_rows_before]
    assert [
        tuple(row)
        for row in repository.db.execute(
            "SELECT decision_id,payload_json FROM strategy_decisions ORDER BY decision_id"
        ).fetchall()
    ] == [tuple(row) for row in decision_rows_before]
    with pytest.raises(Exception, match="immutable"):
        repository.db.execute("UPDATE phase2_outcome_exclusions SET reason_code='x'")
    repository.db.rollback()
    repository.db.close()


def test_operational_deployment_authorizes_schema_overlay_and_resets_clock(tmp_path):
    case = tmp_path / "phase2-deployment"
    create_case(case)
    repository = _connect(case)
    manifest = Phase2Manifest.model_validate_json(
        repository.db.execute("SELECT payload_json FROM phase2_manifests").fetchone()[0]
    )
    candidate_commit = "a" * 40
    candidate_schema = phase2_database_schema_hash()
    assert not repository.authorize_runtime_identity(
        manifest=manifest,
        source_commit=candidate_commit,
        database_schema_hash=candidate_schema,
    )
    repository.record_operational_deployment(
        deployment_id="phase2-operational-recovery-v1",
        phase2_epoch_id="phase2_epoch_002",
        base_manifest_hash=manifest.manifest_hash,
        source_commit=candidate_commit,
        database_schema_hash=candidate_schema,
        deployed_at=FIRST,
    )
    assert repository.authorize_runtime_identity(
        manifest=manifest,
        source_commit=candidate_commit,
        database_schema_hash=candidate_schema,
    )
    repository.set_evidence_window_start(
        window_id="phase2-post-fix-window-v1",
        phase2_epoch_id="phase2_epoch_002",
        first_eligible_boundary=FIRST + timedelta(minutes=15),
        deployment_id="phase2-operational-recovery-v1",
    )
    row = repository.db.execute(
        "SELECT first_eligible_boundary,reason_code FROM phase2_evidence_windows"
    ).fetchone()
    assert row[0] == (FIRST + timedelta(minutes=15)).isoformat()
    assert row[1] == "POST_OPERATIONAL_FIX_PROSPECTIVE_RESET"
    with pytest.raises(ValueError, match="quarter-hour"):
        repository.set_evidence_window_start(
            window_id="bad",
            phase2_epoch_id="phase2_epoch_002",
            first_eligible_boundary=FIRST + timedelta(minutes=16),
            deployment_id="phase2-operational-recovery-v1",
        )
    repository.db.close()
