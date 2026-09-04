from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hype_autopilot.phase2.storage import Phase2Repository
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
                "phase2_epoch_002",
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
    report = collect_operational_telemetry(database)
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
