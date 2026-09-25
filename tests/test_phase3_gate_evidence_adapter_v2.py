from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.storage import INITIAL_EVIDENCE_WINDOW_RULE, Phase2Repository
from hype_autopilot.phase3.gate_evidence_adapter_v2 import (
    ANCHOR,
    CONFIG_SHA,
    DATABASE_SCHEMA_SHA,
    EPOCH_ID,
    FIRST_BOUNDARY,
    FORMAL_CUTOFF,
    OPERATIONS_SHA,
    OUTPUT_SCHEMA_SHA,
    PROMPT_SHA,
    RESEARCH_SHA,
    canonical_decimal,
    canonical_gate_evidence_payload,
    derive_gate_evidence_v2,
    load_frozen_v2_binding,
    snapshot_order_key,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests/fixtures/phase3_epoch006_gate_evidence_v2_golden.json"
VERSIONS = {
    "QUANT_TREND_V1": "QUANT_TREND",
    "QUANT_MR_V1": "QUANT_MR",
    "LLM_V1": "LLM_V1",
    "HYBRID_TREND_LLM_V1": "HYBRID_TREND_LLM_V1",
    "HYBRID_MR_LLM_V1": "HYBRID_MR_LLM_V1",
}


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _frozen_contract() -> dict[str, object]:
    return {
        "snapshot_schema_version": "1.0.0",
        "feature_schema_version": "1.0.0",
        "quant_trend_version": "QUANT_TREND_V1",
        "quant_mean_reversion_version": "QUANT_MR_V1",
        "detector_version": "SETUP_DETECTOR_V1",
        "regime_version": "REGIME_V1",
        "llm_strategy_version": "LLM_V1",
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "model_version": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "prompt_version": "LLM_PROMPT_V2",
        "output_schema_version": "LLM_OUTPUT_V2",
        "hybrid_trend_version": "HYBRID_TREND_LLM_V1",
        "hybrid_mr_version": "HYBRID_MR_LLM_V1",
        "simulator_version": "SIMULATOR_V1",
        "llm_geometry_adapter_version": "LLM_GEOMETRY_ADAPTER_V1",
        "staleness_seconds": 120,
        "malformed_output_max_retries": 1,
        "request_timeout_seconds": 60,
        "resource_isolation": {},
        "information_boundary": {},
        "pair_designations": {},
    }


def _insert_governance(
    db: sqlite3.Connection, *, config_hash: str = CONFIG_SHA
) -> None:
    created = ANCHOR.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    body = {
        "experiment_id": EPOCH_ID,
        "phase2_epoch_id": EPOCH_ID,
        "created_at": created,
        "activation_timestamp": created,
        "authorization_phrase": "synthetic-fixture-only",
        "git_commit_hash": RESEARCH_SHA,
        "config_hash": config_hash,
        "prompt_hash": PROMPT_SHA,
        "output_schema_hash": OUTPUT_SCHEMA_SHA,
        "database_schema_hash": DATABASE_SCHEMA_SHA,
        "frozen_contract": _frozen_contract(),
    }
    digest = sha256_canonical(body)
    manifest_id = str(uuid5(NAMESPACE_URL, f"phase2-manifest:{digest}"))
    payload = {"manifest_id": manifest_id, **body, "manifest_hash": digest}
    db.execute(
        "INSERT INTO phase2_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            manifest_id,
            EPOCH_ID,
            EPOCH_ID,
            _timestamp(ANCHOR),
            RESEARCH_SHA,
            config_hash,
            PROMPT_SHA,
            OUTPUT_SCHEMA_SHA,
            digest,
            DATABASE_SCHEMA_SHA,
            canonical_json(payload),
        ),
    )
    anchor_body = {
        "phase2_epoch_id": EPOCH_ID,
        "event_type": "PROSPECTIVE_START_ESTABLISHED",
        "source_identity": EPOCH_ID,
        "occurred_at": _timestamp(ANCHOR),
        "details": {"prospective_start": _timestamp(ANCHOR)},
    }
    anchor_hash = sha256_canonical(anchor_body)
    db.execute(
        "INSERT INTO phase2_recovery_events VALUES (?,?,?,?,?,?,?)",
        (
            "anchor",
            EPOCH_ID,
            "PROSPECTIVE_START_ESTABLISHED",
            EPOCH_ID,
            _timestamp(ANCHOR),
            canonical_json(anchor_body),
            anchor_hash,
        ),
    )
    window_body = {
        "window_id": "window",
        "phase2_epoch_id": EPOCH_ID,
        "first_eligible_boundary": _timestamp(FIRST_BOUNDARY),
        "deployment_id": None,
        "prospective_start_integrity_hash": anchor_hash,
        "established_at": _timestamp(ANCHOR),
        "rule_version": INITIAL_EVIDENCE_WINDOW_RULE,
        "reason_code": "INITIAL_PROSPECTIVE_START",
    }
    db.execute(
        "INSERT INTO phase2_evidence_windows VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "window",
            EPOCH_ID,
            _timestamp(FIRST_BOUNDARY),
            None,
            anchor_hash,
            _timestamp(ANCHOR),
            INITIAL_EVIDENCE_WINDOW_RULE,
            "INITIAL_PROSPECTIVE_START",
            canonical_json(window_body),
            sha256_canonical(window_body),
        ),
    )


def _snapshot_payload(
    boundary: datetime,
    *,
    price: float,
    trend: str,
    volatility: str,
    identifier: str,
    combined: str | None = None,
) -> dict[str, object]:
    return {
        "snapshot_id": identifier,
        "snapshot_timestamp": _timestamp(boundary),
        "created_at": _timestamp(boundary),
        "snapshot_schema_version": "1.0.0",
        "feature_schema_version": "1.0.0",
        "epoch_id": EPOCH_ID,
        "observation_class": "SCORED_PROSPECTIVE",
        "market": {"hype_features": {"last_15m_close": price}},
        "regime": {
            "trend": trend,
            "volatility": volatility,
            "combined": combined or f"{trend}_{volatility}",
            "version": "REGIME_V1",
        },
        "data_quality": {
            "required_sources_present": True,
            "stale_sources": [],
            "missing_optional_fields": [],
            "source_max_age_seconds": {},
            "scoreable": True,
            "rejection_reasons": [],
        },
        "source_cutoffs": {},
    }


def _insert_trade(
    db: sqlite3.Connection,
    *,
    decision_id: str,
    strategy_id: str,
    snapshot_hash: str,
    signal_time: datetime,
    status: str,
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
    entry_price: float | None = None,
    return_pct: float = 0.0,
) -> None:
    trade_id = f"trade-{decision_id}"
    last_processed = exit_time or entry_time or signal_time
    payload = {
        "paper_trade_id": trade_id,
        "strategy_decision_id": decision_id,
        "strategy_id": strategy_id,
        "snapshot_hash": snapshot_hash,
        "direction": "LONG",
        "signal_time": _timestamp(signal_time),
        "entry_time": _timestamp(entry_time) if entry_time else None,
        "exit_time": _timestamp(exit_time) if exit_time else None,
        "entry_price": entry_price,
        "status": status,
        "last_processed_at": _timestamp(last_processed),
    }
    db.execute(
        "INSERT INTO paper_trades (paper_trade_id,strategy_decision_id,strategy_id,snapshot_hash,direction,signal_time,entry_time,entry_price,exit_time,fees,slippage_cost,funding_cost,return_pct,status,last_processed_at,flags_json,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            trade_id,
            decision_id,
            strategy_id,
            snapshot_hash,
            "LONG",
            _timestamp(signal_time),
            _timestamp(entry_time) if entry_time else None,
            entry_price,
            _timestamp(exit_time) if exit_time else None,
            50.0,
            25.0,
            10.0,
            return_pct,
            status,
            _timestamp(last_processed),
            "[]",
            canonical_json(payload),
        ),
    )


def _insert_snapshot(
    db: sqlite3.Connection,
    *,
    boundary: datetime,
    identifier: str,
    price: float,
    trend: str,
    volatility: str,
    combined: str | None = None,
    llm_cost: float,
    decisions: dict[str, str],
    trades: dict[str, dict[str, object]],
) -> None:
    payload = _snapshot_payload(
        boundary,
        price=price,
        trend=trend,
        volatility=volatility,
        identifier=identifier,
        combined=combined,
    )
    snapshot_hash = sha256_canonical(payload)
    db.execute(
        "INSERT INTO decision_snapshots VALUES (?,?,?,?,?,?,?,?)",
        (
            snapshot_hash,
            identifier,
            _timestamp(boundary),
            EPOCH_ID,
            "SCORED_PROSPECTIVE",
            1,
            canonical_json(payload),
            _timestamp(boundary),
        ),
    )
    db.execute(
        "INSERT INTO research_cycles VALUES (?,?,?,?,?,?,?,?)",
        (
            f"cycle-{identifier}",
            _timestamp(boundary),
            "SCORED_PROSPECTIVE",
            _timestamp(boundary),
            _timestamp(boundary + timedelta(minutes=1)),
            "COMPLETE",
            snapshot_hash,
            '{"scoreable":true}',
        ),
    )
    for version, decision in decisions.items():
        strategy_id = VERSIONS[version]
        decision_id = f"decision-{identifier}-{version}"
        decision_payload = {
            "decision_id": decision_id,
            "snapshot_hash": snapshot_hash,
            "strategy_id": strategy_id,
            "strategy_version": version,
            "decision": decision,
            "created_at": _timestamp(boundary),
        }
        db.execute(
            "INSERT INTO strategy_decisions (decision_id,snapshot_hash,strategy_id,strategy_version,decision,payload_json) VALUES (?,?,?,?,?,?)",
            (
                decision_id,
                snapshot_hash,
                strategy_id,
                version,
                decision,
                canonical_json(decision_payload),
            ),
        )
        if version in trades:
            _insert_trade(
                db,
                decision_id=decision_id,
                strategy_id=strategy_id,
                snapshot_hash=snapshot_hash,
                signal_time=boundary + timedelta(seconds=1),
                **trades[version],
            )
    llm_payload = {
        "experiment_id": EPOCH_ID,
        "phase2_epoch_id": EPOCH_ID,
        "timestamp": _timestamp(boundary),
        "input_snapshot_hash": snapshot_hash,
        "model": "gpt-5.6-terra",
        "model_version": "gpt-5.6-terra",
        "prompt_version": "LLM_PROMPT_V2",
        "output_schema_version": "LLM_OUTPUT_V2",
        "decision": decisions["LLM_V1"],
        "runner_status": "VALID",
        "reason_code": "NONE",
        "model_cost_usd": llm_cost,
        "tool_calls_count": 0,
        "tool_integrity_ok": True,
    }
    db.execute(
        "INSERT INTO llm_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"llm-{identifier}",
            EPOCH_ID,
            EPOCH_ID,
            snapshot_hash,
            "LLM_V1",
            decisions["LLM_V1"],
            "VALID",
            "NONE",
            _timestamp(boundary),
            canonical_json(llm_payload),
            sha256_canonical(llm_payload),
        ),
    )


def _fixture_database(
    tmp_path: Path,
    *,
    config_hash: str = CONFIG_SHA,
    reverse: bool = False,
    invalid_regime: bool = False,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    database = tmp_path / "phase2_epoch_006.sqlite3"
    db = sqlite3.connect(database)
    Phase2Repository(db).initialize()
    _insert_governance(db, config_hash=config_hash)
    common_no_trade = {
        "QUANT_TREND_V1": "NO_TRADE",
        "QUANT_MR_V1": "NO_TRADE",
        "LLM_V1": "NO_TRADE",
        "HYBRID_TREND_LLM_V1": "NO_TRADE",
        "HYBRID_MR_LLM_V1": "NO_TRADE",
    }
    rows = [
        {
            "boundary": FIRST_BOUNDARY,
            "identifier": "a",
            "price": 100.0,
            "trend": "UP",
            "volatility": "NORMAL",
            "llm_cost": 0.02,
            "decisions": {
                key: (
                    "LONG"
                    if key in {"QUANT_TREND_V1", "LLM_V1", "HYBRID_TREND_LLM_V1"}
                    else value
                )
                for key, value in common_no_trade.items()
            },
            "trades": {
                "QUANT_TREND_V1": {
                    "status": "CLOSED",
                    "entry_time": FIRST_BOUNDARY + timedelta(seconds=2),
                    "exit_time": FIRST_BOUNDARY + timedelta(minutes=5),
                    "entry_price": 100.0,
                    "return_pct": 0.01000000004,
                },
                "LLM_V1": {
                    "status": "CLOSED",
                    "entry_time": FIRST_BOUNDARY + timedelta(seconds=3),
                    "exit_time": FIRST_BOUNDARY + timedelta(minutes=5),
                    "entry_price": 100.0,
                    "return_pct": 0.01200000004,
                },
                "HYBRID_TREND_LLM_V1": {
                    "status": "CLOSED",
                    "entry_time": FIRST_BOUNDARY + timedelta(seconds=4),
                    "exit_time": FIRST_BOUNDARY + timedelta(minutes=5),
                    "entry_price": 100.0,
                    "return_pct": 0.01100000004,
                },
            },
        },
        {
            "boundary": FIRST_BOUNDARY + timedelta(minutes=15),
            "identifier": "b",
            "price": 200.0,
            "trend": "DOWN",
            "volatility": "HIGH",
            "llm_cost": 0.03,
            "decisions": {
                **common_no_trade,
                "LLM_V1": "LONG",
                "HYBRID_TREND_LLM_V1": "LONG",
            },
            "trades": {
                "LLM_V1": {
                    "status": "OPEN",
                    "entry_time": FIRST_BOUNDARY + timedelta(minutes=15, seconds=2),
                    "entry_price": 200.0,
                },
                "HYBRID_TREND_LLM_V1": {"status": "SUPPRESSED_POSITION_OPEN"},
            },
        },
        {
            "boundary": FORMAL_CUTOFF,
            "identifier": "c",
            "price": 300.0,
            "trend": "RANGE",
            "volatility": "LOW",
            "llm_cost": 0.04,
            "decisions": {
                **common_no_trade,
                "QUANT_TREND_V1": "LONG",
                "HYBRID_TREND_LLM_V1": "LONG",
            },
            "trades": {
                "QUANT_TREND_V1": {
                    "status": "OPEN",
                    "entry_time": FORMAL_CUTOFF,
                    "entry_price": 300.0,
                },
                "HYBRID_TREND_LLM_V1": {
                    "status": "CLOSED",
                    "entry_time": FORMAL_CUTOFF + timedelta(seconds=1),
                    "exit_time": FORMAL_CUTOFF + timedelta(seconds=2),
                    "entry_price": 300.0,
                    "return_pct": 99.0,
                },
            },
        },
    ]
    if invalid_regime:
        rows[0]["combined"] = "DOWN_HIGH"
    for row in reversed(rows) if reverse else rows:
        _insert_snapshot(db, **row)
    db.commit()
    db.close()
    return database


def test_golden_gate_evidence_derivation_is_exact_read_only_and_order_stable(tmp_path):
    first = _fixture_database(tmp_path / "first")
    second = _fixture_database(tmp_path / "second", reverse=True)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    before = first.read_bytes()
    first_payload = canonical_gate_evidence_payload(
        derive_gate_evidence_v2(
            first, repository_root=ROOT, installed_operations_commit=OPERATIONS_SHA
        )
    )
    second_payload = canonical_gate_evidence_payload(
        derive_gate_evidence_v2(
            second, repository_root=ROOT, installed_operations_commit=OPERATIONS_SHA
        )
    )
    assert first.read_bytes() == before
    assert first_payload == expected
    assert second_payload == expected


def test_trigger_cutoff_inclusion_and_censoring_are_frozen(tmp_path):
    evidence = derive_gate_evidence_v2(
        _fixture_database(tmp_path),
        repository_root=ROOT,
        installed_operations_commit=OPERATIONS_SHA,
    )
    assert evidence.triggered_trade_counts == {
        "QUANT_TREND_V1": 2,
        "QUANT_MR_V1": 0,
        "LLM_V1": 2,
        "HYBRID_TREND_LLM_V1": 1,
        "HYBRID_MR_LLM_V1": 0,
    }
    assert (
        evidence.pairs["HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1"].open_position_count
        == 1
    )
    assert (
        evidence.pairs[
            "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1"
        ].open_position_durations_seconds[-1]
        == 0.0
    )


def test_stored_return_and_one_hype_api_cost_are_not_double_adjusted(tmp_path):
    evidence = derive_gate_evidence_v2(
        _fixture_database(tmp_path),
        repository_root=ROOT,
        installed_operations_commit=OPERATIONS_SHA,
    )
    llm_vs_trend = evidence.pairs["LLM_V1__vs__QUANT_TREND_V1"]
    assert llm_vs_trend.paired_differences == (0.002,)
    assert llm_vs_trend.cost_adjusted_differences == (0.0018,)
    hybrid_mr_vs_quant = evidence.pairs["HYBRID_MR_LLM_V1__vs__QUANT_MR_V1"]
    assert hybrid_mr_vs_quant.cost_adjusted_differences == (
        -0.0002,
        -0.00015,
        -0.0001333333,
    )


def test_sqlite_float_decimal_half_even_and_signed_zero_are_exact(tmp_path):
    db = sqlite3.connect(tmp_path / "numbers.sqlite3")
    db.execute("CREATE TABLE values_real(value REAL)")
    db.executemany(
        "INSERT INTO values_real VALUES (?)",
        [(1.23456789005,), (1.23456789015,), (-0.0,)],
    )
    returned = [
        row[0] for row in db.execute("SELECT value FROM values_real ORDER BY rowid")
    ]
    assert [canonical_decimal(value) for value in returned] == [
        "1.2345678900",
        "1.2345678902",
        "0.0000000000",
    ]


def test_equal_timestamp_hash_tiebreak_is_lexical_and_stable():
    at = FIRST_BOUNDARY
    assert sorted(
        [(at, "b" * 64), (at, "a" * 64)],
        key=lambda item: snapshot_order_key(*item),
    ) == [(at, "a" * 64), (at, "b" * 64)]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("operations", "operations commit"),
        ("config", "config_hash"),
        ("lineage", "five-strategy"),
        ("regime", "regime"),
    ],
)
def test_identity_and_lineage_mismatches_fail_closed(tmp_path, mutation, message):
    database = _fixture_database(
        tmp_path,
        config_hash=("0" * 64 if mutation == "config" else CONFIG_SHA),
        invalid_regime=mutation == "regime",
    )
    if mutation == "lineage":
        db = sqlite3.connect(database)
        db.execute(
            "DELETE FROM strategy_decisions WHERE strategy_version='QUANT_MR_V1' AND snapshot_hash=(SELECT snapshot_hash FROM decision_snapshots ORDER BY snapshot_timestamp LIMIT 1)"
        )
        db.commit()
        db.close()
    with pytest.raises(ValueError, match=message):
        derive_gate_evidence_v2(
            database,
            repository_root=ROOT,
            installed_operations_commit=(
                "0" * 40 if mutation == "operations" else OPERATIONS_SHA
            ),
        )


def test_tampered_spec_or_frozen_dependency_fails_before_database_access(tmp_path):
    copied = tmp_path / "repo"
    for relative in (
        "config/phase3/epoch006_derivation_spec_v2.yaml",
        "config/phase3/epoch006_derivation_spec_v2.canonical.sha256",
        "config/phase3/epoch006_derivation_spec_v2_freeze_authorization.yaml",
        "config/phase3/analysis_gate_v1.yaml",
        "src/hype_autopilot/phase3/gate.py",
    ):
        target = copied / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    with (copied / "config/phase3/epoch006_derivation_spec_v2.yaml").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("\ntampered: true\n")
    with pytest.raises(ValueError, match="canonical identity"):
        load_frozen_v2_binding(copied)
    shutil.copy2(
        ROOT / "config/phase3/epoch006_derivation_spec_v2.yaml",
        copied / "config/phase3/epoch006_derivation_spec_v2.yaml",
    )
    with (copied / "src/hype_autopilot/phase3/gate.py").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("\n# tampered\n")
    with pytest.raises(ValueError, match="evaluator SHA"):
        load_frozen_v2_binding(copied)


@pytest.mark.parametrize("missing", ["trade", "llm"])
def test_missing_trade_or_llm_lineage_fails_closed(tmp_path, missing):
    database = _fixture_database(tmp_path)
    db = sqlite3.connect(database)
    first_hash = db.execute(
        "SELECT snapshot_hash FROM decision_snapshots ORDER BY snapshot_timestamp LIMIT 1"
    ).fetchone()[0]
    if missing == "trade":
        db.execute(
            "DELETE FROM paper_trades WHERE snapshot_hash=? AND strategy_id='LLM_V1'",
            (first_hash,),
        )
    else:
        db.execute("DROP TRIGGER immutable_llm_decisions_delete")
        db.execute(
            "DELETE FROM llm_decisions WHERE input_snapshot_hash=?", (first_hash,)
        )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="paper-trade lineage|shared LLM decision"):
        derive_gate_evidence_v2(
            database,
            repository_root=ROOT,
            installed_operations_commit=OPERATIONS_SHA,
        )


def test_post_cutoff_scored_cycle_fails_closed(tmp_path):
    database = _fixture_database(tmp_path)
    db = sqlite3.connect(database)
    after = FORMAL_CUTOFF + timedelta(minutes=15)
    db.execute(
        "INSERT INTO research_cycles (cycle_id,scheduled_at,observation_class,started_at,status,details_json) VALUES (?,?,?,?,?,?)",
        (
            "future",
            _timestamp(after),
            "SCORED_PROSPECTIVE",
            _timestamp(after),
            "STARTED",
            "{}",
        ),
    )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="post-cutoff scored cycle"):
        derive_gate_evidence_v2(
            database,
            repository_root=ROOT,
            installed_operations_commit=OPERATIONS_SHA,
        )
