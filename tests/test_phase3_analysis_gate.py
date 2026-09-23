from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.storage import (
    INITIAL_EVIDENCE_WINDOW_RULE,
    OPERATIONAL_RESET_RULE,
    Phase2Repository,
)
from hype_autopilot.phase3.gate import (
    GateEvidence,
    PairEvidence,
    evaluate_gate,
    lag_corrected_ess,
    load_analysis_gate,
)
from hype_autopilot.phase3.operational import collect_operational_telemetry

ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "config/phase3/analysis_gate_v1.yaml"


def gate():
    return load_analysis_gate(GATE_PATH)


def pair(pair_id: str, n: int = 60, value: float = 0.01) -> PairEvidence:
    differences = tuple(value + (index % 3) * 0.0001 for index in range(n))
    return PairEvidence(
        pair_id=pair_id,
        paired_differences=differences,
        cost_adjusted_differences=tuple(item - 0.00001 for item in differences),
        trend_states=tuple("UP" if index < n // 2 else "DOWN" for index in range(n)),
        volatility_states=tuple(
            "NORMAL" if index % 2 else "HIGH" for index in range(n)
        ),
        regime_buckets=tuple(
            "UP_NORMAL" if index < n // 2 else "DOWN_HIGH" for index in range(n)
        ),
        open_position_count=1,
        open_position_durations_seconds=(3600.0,),
    )


def all_pairs():
    return {
        pair_id: pair(pair_id, 30 if rule.designation == "EXPLORATORY" else 60)
        for pair_id, rule in gate().pair_rules.items()
    }


def trade_counts():
    return {
        "QUANT_TREND_V1": 40,
        "QUANT_MR_V1": 40,
        "LLM_V1": 40,
        "HYBRID_TREND_LLM_V1": 20,
        "HYBRID_MR_LLM_V1": 20,
    }


def test_gate_identity_and_calendar_floor_are_frozen():
    frozen = gate()
    assert frozen.gate_version == "PHASE3_ANALYSIS_GATE_V1"
    assert frozen.bootstrap_resamples == 10_000
    assert frozen.confidence_level == 0.90
    assert frozen.earliest_formal_checkpoint == datetime(
        2026, 10, 16, 3, 45, 34, 14109, tzinfo=UTC
    )
    assert len(frozen.config_hash) == 64


def test_synthetic_gate_promotes_positive_primary_pairs_and_ignores_open_pnl():
    frozen = gate()
    report = evaluate_gate(
        frozen,
        GateEvidence(
            phase2_epoch_id="phase2_epoch_002",
            as_of=frozen.earliest_formal_checkpoint,
            triggered_trade_counts=trade_counts(),
            pairs=all_pairs(),
            evidence_source="SYNTHETIC_FIXTURE",
        ),
        repetitions_override=100,
    )
    assert report["project_disposition"] == "PROMOTION_CANDIDATE"
    assert all(
        item["verdict"] == "PROMOTE"
        for pair_id, item in report["pair_reports"].items()
        if frozen.pair_rules[pair_id].confirmatory
    )
    assert all(
        item["n_right_censored"] == 1 for item in report["pair_reports"].values()
    )
    assert report["exploratory_pair_determines_project_outcome"] is False
    assert report["infrastructure_readiness_used_as_decision_input"] is False


def test_calendar_sample_and_ess_failures_are_inconclusive():
    frozen = gate()
    early = GateEvidence(
        phase2_epoch_id="phase2_epoch_002",
        as_of=frozen.earliest_formal_checkpoint - timedelta(seconds=1),
        triggered_trade_counts=trade_counts(),
        pairs=all_pairs(),
        evidence_source="SYNTHETIC_FIXTURE",
    )
    report = evaluate_gate(frozen, early, repetitions_override=50)
    assert report["project_disposition"] == "CONTINUE_COLLECTION"
    assert all(
        not item["requirements"]["calendar_floor"]
        for item in report["pair_reports"].values()
    )
    ess, cutoff = lag_corrected_ess([1.0] * 19)
    assert ess == 19 and cutoff == 0


def test_non_fixture_cannot_reduce_frozen_bootstrap_repetitions():
    frozen = gate()
    with pytest.raises(PermissionError):
        evaluate_gate(
            frozen,
            GateEvidence(
                phase2_epoch_id="phase2_epoch_002",
                as_of=frozen.earliest_formal_checkpoint,
                triggered_trade_counts=trade_counts(),
                pairs=all_pairs(),
                evidence_source="PRODUCTION_READ_ONLY",
            ),
            repetitions_override=10,
        )


def test_operational_telemetry_is_read_only_and_contains_no_performance(tmp_path):
    database = tmp_path / "fixture.sqlite3"
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    repository.initialize()
    activation = datetime(2026, 9, 4, 3, 45, tzinfo=UTC)
    manifest = {
        "phase2_epoch_id": "phase2_epoch_005",
        "frozen_contract": {
            "model": "gpt-5.6-terra",
            "model_version": "gpt-5.6-terra",
            "resource_isolation": {"api_budget_usd_per_day": 10.0},
        }
    }
    db.execute(
        "INSERT INTO phase2_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "m",
            "phase2_epoch_005",
            "phase2_epoch_005",
            activation.isoformat(),
            "g",
            "c",
            "p",
            "o",
            "mh",
            "d",
            json.dumps(manifest),
        ),
    )
    start = activation + timedelta(seconds=1)
    repository.record_recovery_event(
        phase2_epoch_id="phase2_epoch_005",
        event_type="PROSPECTIVE_START_ESTABLISHED",
        source_identity="phase2_epoch_005",
        payload={"prospective_start": start.isoformat()},
        occurred_at=start,
    )
    anchor_hash = db.execute(
        "SELECT integrity_hash FROM phase2_recovery_events "
        "WHERE event_type='PROSPECTIVE_START_ESTABLISHED'"
    ).fetchone()[0]
    repository.establish_initial_evidence_window(
        phase2_epoch_id="phase2_epoch_005",
        prospective_start=start,
        prospective_start_integrity_hash=anchor_hash,
    )
    for index, status in enumerate(("COMPLETE", "COMPLETE")):
        at = activation + timedelta(minutes=15 * (index + 1))
        db.execute(
            "INSERT INTO research_cycles(cycle_id,scheduled_at,observation_class,started_at,status,details_json) VALUES (?,?,?,?,?,?)",
            (
                f"c{index}",
                at.isoformat(),
                "SCORED_PROSPECTIVE",
                at.isoformat(),
                status,
                "{}",
            ),
        )
    snapshot_times = [
        activation + timedelta(minutes=15 * (index + 1)) for index in range(3)
    ]
    for index, at in enumerate(snapshot_times):
        db.execute(
            "INSERT INTO decision_snapshots VALUES (?,?,?,?,?,?,?,?)",
            (
                f"h{index}",
                f"s{index}",
                at.isoformat(),
                "phase2_epoch_005",
                "SCORED_PROSPECTIVE",
                1,
                "{}",
                at.isoformat(),
            ),
        )
    db.execute(
        "INSERT INTO strategy_decisions(decision_id,snapshot_hash,strategy_id,strategy_version,decision,payload_json) VALUES (?,?,?,?,?,?)",
        ("d0", "h0", "LLM_V1", "LLM_V1", "LONG", "{}"),
    )
    signal = activation + timedelta(minutes=20)
    db.execute(
        "INSERT INTO paper_trades(paper_trade_id,strategy_decision_id,strategy_id,snapshot_hash,direction,signal_time,status,last_processed_at,flags_json,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "t0",
            "d0",
            "LLM_V1",
            "h0",
            "LONG",
            signal.isoformat(),
            "PENDING_ENTRY",
            signal.isoformat(),
            "[]",
            "{}",
        ),
    )
    db.commit()
    db.close()
    before = database.read_bytes()
    report = collect_operational_telemetry(
        database, observation_cutoff=activation + timedelta(minutes=30)
    )
    assert database.read_bytes() == before
    assert report["telemetry_scope"] == "OPERATIONAL_ONLY_NO_PERFORMANCE_FIELDS"
    assert report["database_integrity"] == "ok"
    assert report["missing_boundaries"] == []
    assert set(report["coeligibility_rate_by_comparison"]) == {
        "LLM_V1__vs__QUANT_TREND_V1",
        "LLM_V1__vs__QUANT_MR_V1",
        "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1",
        "HYBRID_MR_LLM_V1__vs__QUANT_MR_V1",
    }
    assert report["coeligibility_basis"] == "READ_ONLY_POSITION_INTERVAL_RECONSTRUCTION"
    llm_rate = report["coeligibility_rate_by_comparison"]["LLM_V1__vs__QUANT_TREND_V1"][
        "daily"
    ][0]
    hybrid_rate = report["coeligibility_rate_by_comparison"][
        "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1"
    ]["daily"][0]
    assert llm_rate == {
        "day": "2026-09-04",
        "boundaries": 3,
        "coeligible": 1,
        "rate": 1 / 3,
    }
    assert hybrid_rate == {
        "day": "2026-09-04",
        "boundaries": 3,
        "coeligible": 3,
        "rate": 1.0,
    }
    forbidden = {"pnl", "return", "expectancy", "profit", "sharpe"}
    assert not any(word in json.dumps(report).lower() for word in forbidden)

    evidence_start = activation + timedelta(minutes=15)
    assert report["research_activation_timestamp"] == evidence_start.isoformat()
    assert report["effective_evidence_start"] == evidence_start.isoformat()
    assert report["phase3_calendar_floor_anchor"] == evidence_start.isoformat()
    assert report["evidence_clock_reset_applied"] is False

    # A later failure cannot move the already-established clock.
    db = sqlite3.connect(database)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(
        "INSERT INTO research_cycles(cycle_id,scheduled_at,observation_class,started_at,status,details_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            "later-failure",
            (activation + timedelta(minutes=60)).isoformat(),
            "SCORED_PROSPECTIVE",
            (activation + timedelta(minutes=60)).isoformat(),
            "REJECTED",
            "{}",
        ),
    )
    db.commit()
    db.close()
    after_failure = collect_operational_telemetry(
        database, observation_cutoff=activation + timedelta(minutes=60)
    )
    assert after_failure["effective_evidence_start"] == evidence_start.isoformat()
    assert after_failure["phase3_calendar_floor_anchor"] == evidence_start.isoformat()


def test_operational_telemetry_rejects_manifest_time_fallback(tmp_path):
    database = tmp_path / "missing-window.sqlite3"
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    repository.initialize()
    activation = datetime(2026, 9, 4, 3, 45, tzinfo=UTC)
    manifest = {
        "phase2_epoch_id": "phase2_epoch_005",
        "frozen_contract": {
            "model": "gpt-5.6-terra",
            "model_version": "gpt-5.6-terra",
            "resource_isolation": {"api_budget_usd_per_day": 10.0},
        },
    }
    db.execute(
        "INSERT INTO phase2_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "m",
            "phase2_epoch_005",
            "phase2_epoch_005",
            activation.isoformat(),
            "g",
            "c",
            "p",
            "o",
            "mh",
            "d",
            json.dumps(manifest),
        ),
    )
    db.commit()
    db.close()

    with pytest.raises(ValueError, match="exactly one immutable"):
        collect_operational_telemetry(database)


def _valid_epoch005_window_database(tmp_path: Path) -> tuple[Path, datetime]:
    database = tmp_path / "valid-epoch005-window.sqlite3"
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    repository.initialize()
    activation = datetime(2026, 9, 22, 4, 38, tzinfo=UTC)
    start = activation + timedelta(seconds=7)
    manifest = {
        "phase2_epoch_id": "phase2_epoch_005",
        "frozen_contract": {
            "model": "gpt-5.6-terra",
            "model_version": "gpt-5.6-terra",
            "resource_isolation": {"api_budget_usd_per_day": 10.0},
        },
    }
    db.execute(
        "INSERT INTO phase2_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "m",
            "phase2_epoch_005",
            "phase2_epoch_005",
            activation.isoformat(),
            "g",
            "c",
            "p",
            "o",
            "mh",
            "d",
            json.dumps(manifest),
        ),
    )
    repository.record_recovery_event(
        phase2_epoch_id="phase2_epoch_005",
        event_type="PROSPECTIVE_START_ESTABLISHED",
        source_identity="phase2_epoch_005",
        payload={"prospective_start": start.isoformat()},
        occurred_at=start,
    )
    anchor_hash = db.execute(
        "SELECT integrity_hash FROM phase2_recovery_events"
    ).fetchone()[0]
    repository.establish_initial_evidence_window(
        phase2_epoch_id="phase2_epoch_005",
        prospective_start=start,
        prospective_start_integrity_hash=anchor_hash,
    )
    db.close()
    return database, start


def _open_tamper_connection(database: Path) -> sqlite3.Connection:
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    db.execute("DROP TRIGGER immutable_phase2_evidence_windows_update")
    return db


def test_operational_telemetry_rejects_window_rule_column_payload_mismatch(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    db = _open_tamper_connection(database)
    db.execute("PRAGMA ignore_check_constraints=ON")
    db.execute("UPDATE phase2_evidence_windows SET rule_version='OPERATIONAL_RESET_V1'")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="evidence-window integrity"):
        collect_operational_telemetry(database)


def test_operational_telemetry_rejects_window_anchor_hash_column_payload_mismatch(
    tmp_path,
):
    database, _ = _valid_epoch005_window_database(tmp_path)
    db = _open_tamper_connection(database)
    db.execute(
        "UPDATE phase2_evidence_windows SET prospective_start_integrity_hash=?",
        ("0" * 64,),
    )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="evidence-window integrity"):
        collect_operational_telemetry(database)


@pytest.mark.parametrize("tamper", ["missing", "payload"])
def test_operational_telemetry_rejects_missing_or_tampered_start_anchor(
    tmp_path, tamper
):
    database, _ = _valid_epoch005_window_database(tmp_path)
    db = sqlite3.connect(database)
    if tamper == "missing":
        db.execute("DROP TRIGGER immutable_phase2_recovery_events_delete")
        db.execute("DELETE FROM phase2_recovery_events")
    else:
        db.execute("DROP TRIGGER immutable_phase2_recovery_events_update")
        db.execute(
            "UPDATE phase2_recovery_events SET payload_json=?",
            ('{"tampered":true}',),
        )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="prospective-start anchor"):
        collect_operational_telemetry(database)


def test_operational_telemetry_recomputes_boundary_from_verified_anchor(tmp_path):
    database, start = _valid_epoch005_window_database(tmp_path)
    wrong_boundary = (start + timedelta(minutes=22)).replace(
        minute=0, second=0, microsecond=0
    )
    db = _open_tamper_connection(database)
    row = db.execute("SELECT payload_json FROM phase2_evidence_windows").fetchone()
    payload = json.loads(row["payload_json"])
    payload["first_eligible_boundary"] = wrong_boundary.isoformat()
    db.execute(
        "UPDATE phase2_evidence_windows SET first_eligible_boundary=?,payload_json=?,integrity_hash=?",
        (
            wrong_boundary.isoformat(),
            canonical_json(payload),
            sha256_canonical(payload),
        ),
    )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="boundary differs"):
        collect_operational_telemetry(database)


def test_operational_telemetry_rejects_unsupported_reason_rule_combination(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    db = _open_tamper_connection(database)
    db.execute("PRAGMA ignore_check_constraints=ON")
    row = db.execute("SELECT payload_json FROM phase2_evidence_windows").fetchone()
    payload = json.loads(row["payload_json"])
    payload["reason_code"] = "INITIAL_PROSPECTIVE_START"
    payload["rule_version"] = "INITIAL_PROSPECTIVE_START"
    db.execute(
        "UPDATE phase2_evidence_windows SET rule_version=?,reason_code=?,payload_json=?,integrity_hash=?",
        (
            payload["rule_version"],
            payload["reason_code"],
            canonical_json(payload),
            sha256_canonical(payload),
        ),
    )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="unsupported evidence-window"):
        collect_operational_telemetry(database)


def test_valid_epoch005_fresh_start_has_no_clock_reset(tmp_path):
    database, start = _valid_epoch005_window_database(tmp_path)
    report = collect_operational_telemetry(database)
    assert report["effective_evidence_start"] == datetime(
        2026, 9, 22, 4, 45, tzinfo=UTC
    ).isoformat()
    assert report["effective_evidence_start"] != start.isoformat()
    assert report["evidence_clock_reset_applied"] is False
    db = sqlite3.connect(database)
    assert db.execute(
        "SELECT rule_version FROM phase2_evidence_windows"
    ).fetchone()[0] == INITIAL_EVIDENCE_WINDOW_RULE
    db.close()


def test_genuine_operational_reset_preserves_reset_reporting(tmp_path):
    database = tmp_path / "operational-reset.sqlite3"
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    repository.initialize()
    activation = datetime(2026, 9, 22, 3, 45, tzinfo=UTC)
    manifest = {
        "phase2_epoch_id": "phase2_epoch_002",
        "frozen_contract": {
            "model": "gpt-5.6-terra",
            "model_version": "gpt-5.6-terra",
            "resource_isolation": {"api_budget_usd_per_day": 10.0},
        },
    }
    db.execute(
        "INSERT INTO phase2_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "m",
            "phase2_epoch_002",
            "phase2_epoch_002",
            activation.isoformat(),
            "g",
            "c",
            "p",
            "o",
            "mh",
            "d",
            json.dumps(manifest),
        ),
    )
    reset_at = activation + timedelta(minutes=30)
    repository.record_operational_deployment(
        deployment_id="reset-v1",
        phase2_epoch_id="phase2_epoch_002",
        base_manifest_hash="mh",
        source_commit="a" * 40,
        database_schema_hash="b" * 64,
        deployed_at=reset_at,
    )
    repository.set_evidence_window_start(
        window_id="reset-window-v1",
        phase2_epoch_id="phase2_epoch_002",
        first_eligible_boundary=reset_at,
        deployment_id="reset-v1",
    )
    db.close()
    report = collect_operational_telemetry(database, observation_cutoff=reset_at)
    assert report["effective_evidence_start"] == reset_at.isoformat()
    assert report["evidence_clock_reset_applied"] is True


@pytest.mark.parametrize("epoch_id", ["phase2_epoch_005", "phase2_epoch_006"])
def test_fresh_start_epoch_writer_rejects_operational_evidence_window_reset(
    tmp_path, epoch_id
):
    database, start = _valid_epoch005_window_database(tmp_path)
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    with pytest.raises(ValueError, match="fresh-start-only"):
        repository.set_evidence_window_start(
            window_id="forbidden-reset",
            phase2_epoch_id=epoch_id,
            first_eligible_boundary=start + timedelta(minutes=15),
            deployment_id="unused",
        )
    db.close()


def test_epoch005_consumer_rejects_structurally_valid_operational_reset(tmp_path):
    database = tmp_path / "forbidden-epoch005-reset.sqlite3"
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    repository = Phase2Repository(db)
    repository.initialize()
    activation = datetime(2026, 9, 22, 3, 45, tzinfo=UTC)
    manifest = {
        "phase2_epoch_id": "phase2_epoch_005",
        "frozen_contract": {
            "model": "gpt-5.6-terra",
            "model_version": "gpt-5.6-terra",
            "resource_isolation": {"api_budget_usd_per_day": 10.0},
        },
    }
    db.execute(
        "INSERT INTO phase2_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "m",
            "phase2_epoch_005",
            "phase2_epoch_005",
            activation.isoformat(),
            "g",
            "c",
            "p",
            "o",
            "mh",
            "d",
            json.dumps(manifest),
        ),
    )
    repository.record_operational_deployment(
        deployment_id="forbidden-reset-v1",
        phase2_epoch_id="phase2_epoch_005",
        base_manifest_hash="mh",
        source_commit="a" * 40,
        database_schema_hash="b" * 64,
        deployed_at=activation,
    )
    body = {
        "window_id": "forbidden-window-v1",
        "phase2_epoch_id": "phase2_epoch_005",
        "first_eligible_boundary": activation.isoformat(),
        "deployment_id": "forbidden-reset-v1",
        "prospective_start_integrity_hash": None,
        "established_at": activation.isoformat(),
        "rule_version": OPERATIONAL_RESET_RULE,
        "reason_code": "POST_OPERATIONAL_FIX_PROSPECTIVE_RESET",
    }
    db.execute(
        "INSERT INTO phase2_evidence_windows VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            body["window_id"],
            body["phase2_epoch_id"],
            body["first_eligible_boundary"],
            body["deployment_id"],
            body["prospective_start_integrity_hash"],
            body["established_at"],
            body["rule_version"],
            body["reason_code"],
            canonical_json(body),
            sha256_canonical(body),
        ),
    )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="fresh-start-only"):
        collect_operational_telemetry(database, observation_cutoff=activation)


def _insert_operational_cycle(
    database: Path,
    *,
    scheduled_at: datetime,
    status: str = "COMPLETE",
    scoreable: bool = True,
    reason: str | None = None,
    observation_class: str = "SCORED_PROSPECTIVE",
) -> None:
    details = {"scoreable": scoreable}
    if reason is not None:
        details["reason"] = reason
    db = sqlite3.connect(database)
    db.execute(
        "INSERT INTO research_cycles"
        "(cycle_id,scheduled_at,observation_class,started_at,status,details_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            f"cycle-{scheduled_at.isoformat()}",
            scheduled_at.isoformat(),
            observation_class,
            scheduled_at.isoformat(),
            status,
            json.dumps(details),
        ),
    )
    db.commit()
    db.close()


def test_missing_first_boundary_is_not_hidden_by_four_later_successes(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    for offset in range(1, 5):
        _insert_operational_cycle(
            database, scheduled_at=first + timedelta(minutes=15 * offset)
        )
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(minutes=60)
    )
    assert report["missing_boundaries"] == [first.isoformat()]
    check = report["preliminary_first_four_cycle_check"]
    assert check["passed"] is False
    assert check["authorizes_evidence_start"] is False
    assert check["results"][0]["status"] == "ABSENT"


def test_no_cycles_after_window_reports_only_overdue_boundaries_missing(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(minutes=20, seconds=1)
    )
    assert report["missing_boundaries"] == [first.isoformat()]
    assert report["in_progress_boundaries"] == [
        (first + timedelta(minutes=15)).isoformat()
    ]


def test_internal_absent_boundary_is_reported(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    _insert_operational_cycle(database, scheduled_at=first)
    _insert_operational_cycle(database, scheduled_at=first + timedelta(minutes=30))
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(minutes=36)
    )
    assert report["missing_boundaries"] == [
        (first + timedelta(minutes=15)).isoformat()
    ]


def test_rejected_downtime_boundary_is_present_but_not_successful(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    _insert_operational_cycle(
        database,
        scheduled_at=first,
        status="REJECTED",
        scoreable=False,
        reason="PROCESS_DOWNTIME",
    )
    report = collect_operational_telemetry(database, observation_cutoff=first)
    assert report["missing_boundaries"] == []
    assert report["rejected_process_downtime_boundaries"] == [first.isoformat()]
    assert report["preliminary_first_four_cycle_check"]["passed"] is False


def test_boundary_not_yet_scheduled_is_not_reported_missing(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    report = collect_operational_telemetry(
        database,
        observation_cutoff=datetime(2026, 9, 22, 4, 44, 59, tzinfo=UTC),
    )
    assert report["missing_boundaries"] == []


def test_preliminary_first_four_check_requires_exact_initial_boundaries(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    for offset in range(4):
        _insert_operational_cycle(
            database, scheduled_at=first + timedelta(minutes=15 * offset)
        )
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(minutes=45)
    )
    assert report["missing_boundaries"] == []
    check = report["preliminary_first_four_cycle_check"]
    assert check["passed"] is True
    assert check["authorizes_evidence_start"] is False
    assert check["required_boundaries"] == [
        (first + timedelta(minutes=15 * offset)).isoformat() for offset in range(4)
    ]
    assert report["final_evidence_start_authorization"] == {
        "authorized": False,
        "status": "REQUIRES_CONTROLLED_ACTIVATION_EVIDENCE_REVIEW",
        "procedure": (
            "docs/phase2_epoch005_evidence_window_review.md"
            "#final-evidence-start-authorization-boundary"
        ),
    }


def test_non_scored_cycles_cannot_mask_scored_startup_boundaries(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    for offset in range(4):
        _insert_operational_cycle(
            database,
            scheduled_at=first + timedelta(minutes=15 * offset),
            observation_class="NON_SCORED_ACCEPTANCE",
        )
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(minutes=45)
    )
    assert report["preliminary_first_four_cycle_check"]["passed"] is False
    assert report["preliminary_first_four_cycle_check"][
        "authorizes_evidence_start"
    ] is False
    assert report["missing_boundaries"] == [
        first.isoformat(),
        (first + timedelta(minutes=15)).isoformat(),
    ]
    assert report["in_progress_boundaries"] == [
        (first + timedelta(minutes=30)).isoformat(),
        (first + timedelta(minutes=45)).isoformat(),
    ]


def test_scoreable_cycles_without_downstream_lineage_never_authorize_start(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    for offset in range(4):
        _insert_operational_cycle(
            database, scheduled_at=first + timedelta(minutes=15 * offset)
        )
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(minutes=45)
    )
    assert report["preliminary_first_four_cycle_check"]["passed"] is True
    assert report["final_evidence_start_authorization"]["authorized"] is False


def test_boundary_inside_scheduler_and_completion_grace_is_in_progress(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(seconds=1)
    )
    assert report["missing_boundaries"] == []
    assert report["in_progress_boundaries"] == [first.isoformat()]
    assert report["boundary_reporting_policy"] == {
        "scheduler_execution_grace_seconds": 5,
        "completion_allowance_seconds": 1200,
        "completion_allowance_source": (
            "INSTALLED_PHASE2_HEALTH_MAXIMUM_BOUNDARY_AGE_20_MINUTES"
        ),
    }


def test_absent_boundary_becomes_missing_after_reporting_deadline(tmp_path):
    database, _ = _valid_epoch005_window_database(tmp_path)
    first = datetime(2026, 9, 22, 4, 45, tzinfo=UTC)
    report = collect_operational_telemetry(
        database, observation_cutoff=first + timedelta(minutes=20, seconds=1)
    )
    assert report["missing_boundaries"] == [first.isoformat()]
    assert report["in_progress_boundaries"] == [
        (first + timedelta(minutes=15)).isoformat()
    ]
