from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from hype_autopilot.phase3.signal_lab import (
    AggressorSide,
    LabOutcome,
    MarketObservation,
    SignalLabRepository,
    SourceProvenance,
    TradePrint,
    aggregate_trade_flow,
    benjamini_hochberg,
    compute_signal_series,
    connect_signal_lab,
    load_batch_manifest,
    screen_batch,
    validate_market_data,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "phase3" / "candidate_signal_lab_batch1.yaml"


def manifest():
    return load_batch_manifest(MANIFEST_PATH)


def source(start: datetime, end: datetime, *, aggressor: bool = True):
    return SourceProvenance(
        source_id="synthetic-source-v1",
        provider="SYNTHETIC_FIXTURE",
        dataset="fixture_hype_history",
        retrieval_timestamp=end + timedelta(minutes=1),
        coverage_start=start,
        coverage_end=end,
        source_hash="a" * 64,
        timestamp_semantics="COMPLETED_INTERVAL_END_UTC",
        aggressor_side_semantics=(
            "BUY_IS_TAKER_BUY_SELL_IS_TAKER_SELL" if aggressor else None
        ),
        reproducible_locator="fixture://tests/phase3-signal-lab-v1",
    )


def rows(count: int = 360) -> list[MarketObservation]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    output = []
    for index in range(count):
        wave = ((index % 17) - 8) / 1000
        output.append(
            MarketObservation(
                timestamp=start + timedelta(minutes=15 * index),
                hype_close=20.0 * (1.0004**index) * (1 + wave),
                btc_close=50_000.0 * (1.0002**index) * (1 - wave / 2),
                funding_rate=((index % 31) - 15) / 100_000,
                open_interest=10_000_000.0 * (1.0001**index) * (1 + wave / 3),
                aggressor_buy_notional=100_000 + (index % 11) * 5_000,
                aggressor_sell_notional=95_000 + ((index + 4) % 13) * 4_000,
                buy_trade_count=100 + index % 9,
                sell_trade_count=96 + (index + 3) % 10,
                average_buy_trade_notional=1_000.0,
                average_sell_trade_notional=990.0,
                regime="UP_NORMAL" if index % 2 else "RANGE_HIGH",
            )
        )
    return output


def test_batch_1_manifest_is_frozen_before_any_screen():
    frozen = manifest()
    assert frozen.status == "FROZEN_NOT_EXECUTED"
    assert len(frozen.hypotheses) == 7
    assert frozen.horizons["primary_minutes"] == [60, 240]
    assert frozen.horizons["exploratory_minutes"] == [15, 720]
    assert frozen.multiple_testing["family_size"] == 14
    assert frozen.multiple_testing["method"] == "benjamini_hochberg"
    assert frozen.source_policy["phase2_database_reads_allowed"] is False
    assert len(frozen.manifest_hash) == 64
    assert all(
        hypothesis.hypothesis_id != "HYPE_LIQUIDATION_BURST_V1"
        for hypothesis in frozen.hypotheses
    )


def test_future_mutation_cannot_change_any_signal_at_or_before_cutoff():
    frozen = manifest()
    baseline = rows()
    cutoff = 220
    changed = list(baseline)
    for index in range(cutoff + 1, len(changed)):
        changed[index] = changed[index].model_copy(
            update={
                "hype_close": changed[index].hype_close * 9,
                "btc_close": changed[index].btc_close / 3,
                "funding_rate": 0.25,
                "open_interest": changed[index].open_interest * 5,
                "aggressor_buy_notional": 1.0,
                "aggressor_sell_notional": 99_000_000.0,
                "buy_trade_count": 1,
                "sell_trade_count": 1_000_000,
            }
        )
    for hypothesis in frozen.hypotheses:
        left = compute_signal_series(baseline, hypothesis.hypothesis_id)
        right = compute_signal_series(changed, hypothesis.hypothesis_id)
        assert left[: cutoff + 1] == right[: cutoff + 1]


def test_data_quality_accounts_for_gaps_duplicates_and_source_fields():
    data = rows(140)
    provenance = source(data[0].timestamp, data[-1].timestamp)
    good = validate_market_data(data, provenance, manifest())
    assert good.valid is True
    assert not good.missing_timestamps
    assert set(good.hypothesis_status.values()) == {"DATA_VALID"}

    broken = data[:50] + data[51:80] + [data[79]] + data[80:]
    bad = validate_market_data(broken, provenance, manifest())
    assert bad.valid is False
    assert "DUPLICATE_TIMESTAMPS" in bad.issues
    assert "MISSING_INTERVALS" in bad.issues


def test_data_quality_rejects_rows_outside_declared_source_coverage():
    data = rows(140)
    provenance = source(
        data[1].timestamp,
        data[-2].timestamp,
    )
    report = validate_market_data(data, provenance, manifest())
    assert report.valid is False
    assert "OBSERVATION_BEFORE_PROVENANCE_COVERAGE" in report.issues
    assert "OBSERVATION_AFTER_PROVENANCE_COVERAGE" in report.issues


def test_order_flow_hypotheses_require_proven_aggressor_semantics():
    data = rows(140)
    quality = validate_market_data(
        data,
        source(data[0].timestamp, data[-1].timestamp, aggressor=False),
        manifest(),
    )
    assert quality.hypothesis_status["HYPE_AGGRESSOR_CVD_V1"].startswith("DATA_INVALID")
    assert quality.hypothesis_status["HYPE_TRADE_COUNT_DELTA_V1"].startswith(
        "DATA_INVALID"
    )


def test_trade_flow_uses_taker_side_and_start_inclusive_end_exclusive():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    trades = [
        TradePrint(
            trade_id="a",
            timestamp=start,
            price=20,
            size=2,
            aggressor_side=AggressorSide.BUY,
        ),
        TradePrint(
            trade_id="b",
            timestamp=start + timedelta(minutes=14, seconds=59),
            price=21,
            size=1,
            aggressor_side=AggressorSide.SELL,
        ),
        TradePrint(
            trade_id="c",
            timestamp=start + timedelta(minutes=15),
            price=22,
            size=9,
            aggressor_side=AggressorSide.BUY,
        ),
    ]
    aggregate = aggregate_trade_flow(trades, start, start + timedelta(minutes=15))
    assert aggregate.aggressor_buy_notional == 40
    assert aggregate.aggressor_sell_notional == 21
    assert aggregate.buy_trade_count == aggregate.sell_trade_count == 1
    assert len(aggregate.aggregate_hash) == 64
    with pytest.raises(ValueError, match="duplicate trade id"):
        aggregate_trade_flow(trades + [trades[0]], start, start + timedelta(minutes=15))


def test_bh_fdr_is_deterministic_and_controls_one_frozen_family():
    p_values = {"a": 0.001, "b": 0.009, "c": 0.03, "d": 0.4}
    first = benjamini_hochberg(p_values, 0.05)
    second = benjamini_hochberg(dict(reversed(list(p_values.items()))), 0.05)
    assert first == second
    assert first["a"]["rejected"] is True
    assert first["b"]["rejected"] is True
    assert first["d"]["rejected"] is False


def test_synthetic_screen_is_deterministic_and_exploratory_cannot_promote():
    data = rows()
    provenance = source(data[0].timestamp, data[-1].timestamp)
    first = screen_batch(data, provenance, manifest(), repetitions_override=80)
    second = screen_batch(data, provenance, manifest(), repetitions_override=80)
    assert first == second
    assert first["bh_family_size"] == 14
    assert first["active_epoch_evidence"] is False
    assert first["stage2_executed"] is False
    assert all(
        report["outcome"] in {item.value for item in LabOutcome}
        and report["exploratory_can_promote"] is False
        for report in first["reports"].values()
    )


def test_phase2_evidence_identity_is_rejected_without_reading_it():
    data = rows(140)
    provenance = source(data[0].timestamp, data[-1].timestamp).model_copy(
        update={"dataset": "phase2_epoch_002"}
    )
    with pytest.raises(PermissionError, match="Phase 2 evidence"):
        screen_batch(data, provenance, manifest(), repetitions_override=10)


def test_lab_database_is_path_isolated_append_only_and_permanent(tmp_path):
    root = tmp_path / "phase3"
    database = root / "data" / "phase3_signal_lab" / "lab.sqlite3"
    db = connect_signal_lab(database, root)
    repository = SignalLabRepository(db)
    frozen = manifest()
    registered = datetime(2026, 9, 6, tzinfo=UTC)
    repository.register_manifest(
        frozen, registered_at=registered, implementation_commit="b" * 40
    )
    assert db.execute("SELECT COUNT(*) FROM lab_hypotheses").fetchone()[0] == 7
    assert repository.integrity() == ("ok", 0)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE lab_hypotheses SET family='changed'")
    db.rollback()
    with pytest.raises(ValueError, match="production evidence"):
        connect_signal_lab(
            root / "data" / "phase3_signal_lab" / "phase2_epoch_002.sqlite3",
            root,
        )
    with pytest.raises(ValueError, match="must remain"):
        connect_signal_lab(tmp_path / "outside.sqlite3", root)
    db.close()


def test_lab_store_rejects_a_tampered_result_hash(tmp_path):
    root = tmp_path / "phase3"
    database = root / "data" / "phase3_signal_lab" / "lab.sqlite3"
    db = connect_signal_lab(database, root)
    repository = SignalLabRepository(db)
    frozen = manifest()
    data = rows()
    provenance = source(data[0].timestamp, data[-1].timestamp)
    repository.register_manifest(
        frozen,
        registered_at=datetime(2026, 9, 6, tzinfo=UTC),
        implementation_commit="b" * 40,
    )
    quality = validate_market_data(data, provenance, frozen)
    repository.register_source(provenance, quality)
    result = screen_batch(data, provenance, frozen, repetitions_override=5)
    result["result_hash"] = "0" * 64
    with pytest.raises(ValueError, match="result hash"):
        repository.save_run(
            result,
            source_hash=provenance.source_hash,
            run_class="SYNTHETIC_FIXTURE",
            started_at=datetime(2026, 9, 6, tzinfo=UTC),
            implementation_commit="b" * 40,
        )
    assert db.execute("SELECT COUNT(*) FROM lab_runs").fetchone()[0] == 0
    db.close()


def test_non_utc_rows_fail_validation_at_model_boundary():
    with pytest.raises(ValueError, match="UTC"):
        MarketObservation(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
            hype_close=20,
        )
