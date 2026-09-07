"""Offline, permanently NON_SCORED scheduler-path recovery acceptance.

The production contract is loaded byte-for-byte. Only activation gates are enabled
in memory, as in the existing acceptance harness. The provider and collector are
offline fixtures; the builder, strategies, runner, scheduler and simulator are real.
Virtual quarter-hour timestamps describe fixture chronology, not historical evidence.
No production database is opened, copied, replayed, or used as a market-data source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hype_autopilot.config import load_yaml
from hype_autopilot.data.models import AssetContext, Candle, ObservationClass
from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.config import (
    ACTIVATION_PHRASE,
    file_sha256,
    load_phase2_config,
)
from hype_autopilot.phase2.manifest import Phase2Manifest, build_activation_manifest
from hype_autopilot.phase2.models import LLMStructuredOutputV2, ProviderResponse
from hype_autopilot.phase2.pipeline import Phase2Pipeline
from hype_autopilot.phase2.provider import output_json_schema
from hype_autopilot.phase2.runner import FailClosedLLMRunner
from hype_autopilot.phase2.scheduler import planned_phase2_boundary, run_phase2_boundary
from hype_autopilot.phase2.storage import Phase2Repository, phase2_database_schema_hash
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from tests.helpers import candle_series, populate_scoreable

ROOT = Path(__file__).resolve().parents[1]
AUDIT_CLASS = "NON_SCORED_PHASE2_SIMULATOR_RECOVERY_ACCEPTANCE"
DB_NAME = "NON_SCORED_SIMULATOR_ACCEPTANCE.sqlite3"
FIRST = datetime(2026, 9, 5, 0, 15, tzinfo=UTC)
BASELINE = "0322e53ba55df1c9ad44286616cd71bd2e0700c6"
TABLES = (
    "research_cycles",
    "decision_snapshots",
    "strategy_decisions",
    "detector_decisions",
    "llm_decisions",
    "llm_invocation_attempts",
    "paper_trades",
    "paper_orders",
    "paper_fills",
)


def block_network() -> None:
    """Any accidental network request fails; there are no real provider calls."""

    def denied(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("NON_SCORED offline acceptance forbids network access")

    socket.socket.connect = denied
    socket.create_connection = denied


def config_identities() -> dict[str, Any]:
    config, digest = load_phase2_config(ROOT / "config/phase2/phase2_epoch_002.yaml")
    config.assert_build_only()
    return {
        "baseline_commit": BASELINE,
        "head_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "config_hash": digest,
        "config_file_hash": file_sha256(ROOT / "config/phase2/phase2_epoch_002.yaml"),
        "prompt_hash": file_sha256(ROOT / config.prompt_path),
        "output_schema_hash": sha256_canonical(
            output_json_schema(config.output_schema_version)
        ),
        "database_schema_hash": phase2_database_schema_hash(),
        "provider": config.provider,
        "model": config.model,
        "model_version": config.model_version,
        "pipeline_file_hash": file_sha256(
            ROOT / "src/hype_autopilot/phase2/pipeline.py"
        ),
        "simulator_file_hash": file_sha256(
            ROOT / "src/hype_autopilot/simulation/engine.py"
        ),
        "production_config_gates": {
            "activation_authorized": config.activation_authorized,
            "evidence_collection_enabled": config.evidence_collection_enabled,
        },
    }


def _database(case: Path) -> Path:
    case = case.resolve()
    marker = case / "NON_SCORED.json"
    if not marker.is_file():
        raise PermissionError("refusing unmarked acceptance directory")
    metadata = json.loads(marker.read_text())
    if metadata.get("audit_class") != AUDIT_CLASS or not metadata.get(
        "permanently_non_scored"
    ):
        raise PermissionError("acceptance directory must be permanently non-scored")
    database = case / DB_NAME
    if database.is_symlink():
        raise PermissionError("acceptance database cannot be a symlink")
    return database


def _connect(case: Path, *, readonly: bool = False) -> Phase2Repository:
    database = _database(case)
    db = sqlite3.connect(f"file:{database}?mode={'ro' if readonly else 'rw'}", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if readonly:
        db.execute("PRAGMA query_only=ON")
    return Phase2Repository(db)


def create_case(
    case: Path, *, scenario: str = "normal", response_mode: str = "valid"
) -> dict[str, Any]:
    if case.exists():
        raise FileExistsError(
            f"refusing to overwrite prior acceptance evidence: {case}"
        )
    case.mkdir(parents=True)
    identities = config_identities()
    metadata = {
        "audit_class": AUDIT_CLASS,
        "permanently_non_scored": True,
        "experiment_id": f"{AUDIT_CLASS}:{case.name}",
        "fixture_clock": True,
        "fixture_first_boundary": FIRST.isoformat(),
        "fixture_activation": (FIRST - timedelta(seconds=10)).isoformat(),
        "market_source": "deterministic_offline_raw_market_fixture_not_production",
        "provider_source": "offline_ProviderResponse_fixture_not_real_provider",
        "scenario": scenario,
        "response_mode": response_mode,
        "identities": identities,
    }
    (case / "NON_SCORED.json").write_text(json.dumps(metadata, indent=2) + "\n")
    db = sqlite3.connect(_database(case))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    repository = Phase2Repository(db)
    repository.initialize()
    frozen, digest = load_phase2_config(ROOT / "config/phase2/phase2_epoch_002.yaml")
    enabled = frozen.model_copy(
        update={"activation_authorized": True, "evidence_collection_enabled": True}
    )
    manifest = build_activation_manifest(
        config=enabled,
        experiment_id=metadata["experiment_id"],
        activation_timestamp=FIRST - timedelta(seconds=10),
        authorization=ACTIVATION_PHRASE,
        git_commit_hash=identities["head_commit"],
        config_hash=digest,
        prompt_hash=identities["prompt_hash"],
        output_schema_hash=identities["output_schema_hash"],
        database_schema_hash=identities["database_schema_hash"],
    )
    repository.save_manifest(manifest)
    populate_scoreable(repository.core, FIRST)
    repository.core.health(
        AUDIT_CLASS, "PERMANENTLY_NON_SCORED", metadata, FIRST - timedelta(seconds=10)
    )
    db.close()
    return metadata


class RawFixtureCollector:
    """Injects only raw observations; never injects snapshots or decisions."""

    def __init__(self, repository: Phase2Repository, *, scenario: str, crash: str):
        self.repository = repository
        self.scenario = scenario
        self.crash = crash

    def collect_incremental(
        self, *, end: datetime, observation_class: ObservationClass
    ) -> None:
        if self.crash == "before_collection":
            os._exit(87)
        repository = self.repository.core
        if end > FIRST:
            for symbol, intervals in (
                ("HYPE", ("5m", "15m", "1h", "4h")),
                ("BTC", ("15m", "1h", "4h")),
            ):
                for interval in intervals:
                    step = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}[interval]
                    latest = repository.latest_candle_close(symbol, interval)
                    assert latest is not None
                    count = int((end - latest).total_seconds() // (60 * step))
                    if count:
                        # Append complete fixture bars; never correct prior rows.
                        final = latest + timedelta(minutes=step * count)
                        repository.save_candles(
                            candle_series(
                                symbol,
                                interval,
                                final,
                                count,
                                timedelta(minutes=step),
                                100 if symbol == "HYPE" else 70000,
                            )
                        )
            if self.scenario != "pending_without_bar":
                latest = repository.latest_candle_close("HYPE", "1m")
                assert latest is not None
                bars = []
                at = latest
                while at < end:
                    close = at + timedelta(minutes=1)
                    target = self.scenario == "normal" and close > FIRST + timedelta(
                        minutes=15
                    )
                    stop = (
                        self.scenario == "adverse_first"
                        and close > FIRST + timedelta(minutes=15)
                    )
                    bars.append(
                        Candle(
                            symbol="HYPE",
                            interval="1m",
                            open_time=at,
                            close_time=close,
                            open=100.0,
                            high=126.0 if target or stop else 100.5,
                            low=94.0 if stop else 99.5,
                            close=100.0,
                            volume=1000.0,
                            trade_count=10,
                            received_at=close,
                            observation_class=ObservationClass.WARMUP,
                        )
                    )
                    at = close
                repository.save_candles(bars)
        # Existing FIRST context is not overwritten: use a later source timestamp
        # at each future boundary and preserve all seed rows.
        if end > FIRST:
            repository.save_asset_context(
                AssetContext(
                    symbol="HYPE",
                    source_timestamp=end,
                    received_at=end,
                    mark_price=100.0,
                    mid_price=100.0,
                    oracle_price=100.0,
                    funding_rate=0.0001,
                    open_interest=1_100_000,
                    day_notional_volume=50_000_000,
                )
            )
        self.repository.core.health(
            AUDIT_CLASS,
            "COLLECTED_OFFLINE_RAW_FIXTURE",
            {"boundary": end.isoformat(), "pid": os.getpid()},
            end,
        )

    def recover_gaps(self, boundary: datetime) -> None:
        self.repository.core.health(
            AUDIT_CLASS,
            "NO_EXTERNAL_GAP_FETCH",
            {"boundary": boundary.isoformat(), "pid": os.getpid()},
            boundary,
        )


class FixtureProvider:
    def __init__(self, config: Any, *, mode: str, scenario: str):
        self.config = config
        self.mode = mode
        self.scenario = scenario
        self.calls = 0

    def invoke(
        self, *, prompt: str, snapshot_json: str, timeout_seconds: int
    ) -> ProviderResponse:
        self.calls += 1
        assert prompt == (ROOT / self.config.prompt_path).read_text()
        assert timeout_seconds == self.config.request_timeout_seconds
        snapshot = json.loads(snapshot_json)
        at = datetime.fromisoformat(snapshot["snapshot_timestamp"])
        trade = at == FIRST
        # FIRST's fixture reference is about 121. Geometry remains valid for
        # NOW entry; simulator's first eligible raw 1m fallback close is 100.
        value = {
            "input_snapshot_hash": snapshot["snapshot_hash"],
            "output_schema_version": "LLM_OUTPUT_V2",
            "decision": "LONG" if trade else "NO_TRADE",
            "confidence": "MEDIUM",
            "rationale_tags": ["NON_SCORED_FIXTURE"],
            "bull_case": [],
            "bear_case": [],
            "data_conflicts": [],
            "invocation_reason": AUDIT_CLASS,
            "entry": {"mode": "NOW" if trade else "NONE", "trigger_price": None},
            "stop": {"kind": "ABSOLUTE_PRICE", "price": 95.0} if trade else None,
            "target": {"kind": "ABSOLUTE_PRICE", "price": 125.0} if trade else None,
            "invalidation": {
                "category": "PRICE_LEVEL",
                "reference_price": 95.0,
                "tags": [],
            }
            if trade
            else None,
            "ttl_minutes": (15 if self.scenario == "ttl" else 60) if trade else None,
        }
        # Target is above the frozen reference and reached at the next fixture
        # management step; no strategy/economic parameter is modified.
        raw = json.dumps(value, sort_keys=True)
        if (
            self.mode == "malformed_then_valid"
            and self.calls == 1
            or self.mode == "malformed_exhaustion"
        ):
            raw = "{NON_SCORED deliberately malformed JSON"
        return ProviderResponse(
            raw_output=raw,
            model=self.config.model,
            model_version=self.config.model_version,
            request_started_at=at + timedelta(seconds=5 + self.calls),
            request_ended_at=at + timedelta(seconds=6 + self.calls),
            input_tokens=100,
            output_tokens=60,
            cost_usd=0.0,
            tool_calls_count=0,
        )


def run_worker(
    case: Path, boundary_number: int, *, crash: str = "none"
) -> dict[str, Any]:
    block_network()
    repository = _connect(case)
    metadata = json.loads((case / "NON_SCORED.json").read_text())
    frozen, digest = load_phase2_config(ROOT / "config/phase2/phase2_epoch_002.yaml")
    frozen.assert_build_only()
    config = frozen.model_copy(
        update={"activation_authorized": True, "evidence_collection_enabled": True}
    )
    manifest = Phase2Manifest.model_validate_json(
        repository.db.execute("SELECT payload_json FROM phase2_manifests").fetchone()[0]
    )
    assert manifest.experiment_id.startswith(AUDIT_CLASS)
    boundary = FIRST + timedelta(minutes=15 * boundary_number)
    assert boundary >= manifest.activation_timestamp
    assert planned_phase2_boundary(boundary - timedelta(seconds=1)) == boundary
    epoch = load_yaml(ROOT / "config/epoch_001.yaml")
    epoch["epoch_id"] = config.phase2_epoch_id
    provider = FixtureProvider(
        config, mode=metadata["response_mode"], scenario=metadata["scenario"]
    )
    if crash == "after_trade_before_order":
        original = repository.core.save_trade

        def crash_after_trade(trade: Any) -> Any:
            result = original(trade)
            if trade.status.value == "PENDING_ENTRY" and trade.strategy_id == "LLM_V1":
                os._exit(87)
            return result

        repository.core.save_trade = crash_after_trade
    if crash == "after_attempt_before_decision":
        original_attempt = repository.save_attempt

        def crash_after_attempt(attempt: Any) -> Any:
            original_attempt(attempt)
            os._exit(87)

        repository.save_attempt = crash_after_attempt
    if crash == "after_entry_fill_before_trade":
        original_fill = repository.core.save_fill

        def crash_after_entry_fill(*args: Any, **kwargs: Any) -> None:
            original_fill(*args, **kwargs)
            strategy = repository.db.execute(
                "SELECT strategy_id FROM paper_trades WHERE paper_trade_id=?",
                (args[0],),
            ).fetchone()[0]
            if args[2] == "ENTRY" and strategy == "LLM_V1":
                os._exit(87)

        repository.core.save_fill = crash_after_entry_fill
    pipeline = Phase2Pipeline(
        config=config,
        repository=repository,
        builder=SnapshotBuilder(
            repository.core, load_yaml(ROOT / "config/base.yaml"), epoch
        ),
        collector=RawFixtureCollector(
            repository, scenario=metadata["scenario"], crash=crash
        ),
        llm_runner=FailClosedLLMRunner(
            config=config,
            provider=provider,
            repository=repository,
            prompt=(ROOT / config.prompt_path).read_text(),
            experiment_id=manifest.experiment_id,
            clock=lambda: boundary + timedelta(seconds=5),
        ),
        simulator=PaperSimulator(repository.core),
        git_commit_hash=manifest.git_commit_hash,
        config_hash=digest,
        prompt_hash=file_sha256(ROOT / config.prompt_path),
        output_schema_hash=sha256_canonical(
            output_json_schema(config.output_schema_version)
        ),
        database_schema_hash=phase2_database_schema_hash(),
    )
    result = run_phase2_boundary(pipeline, manifest=manifest, boundary=boundary)
    snapshot = inspect_case(case)
    repository.db.close()
    return {
        "pid": os.getpid(),
        "boundary": boundary.isoformat(),
        "status": result,
        "provider_fixture_calls": provider.calls,
        "checkpoint": snapshot,
    }


def invoke_worker(
    case: Path, boundary_number: int, *, crash: str = "none"
) -> dict[str, Any]:
    env = dict(os.environ, PYTHONPATH=f"{ROOT / 'src'}:{ROOT}")
    # Fixtures never need a credential. Remove inherited provider credentials
    # from the child entirely, even though network use is also blocked.
    env.pop("OPENAI_API_KEY", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.phase2_recovery_harness",
            "worker",
            str(case),
            str(boundary_number),
            "--crash",
            crash,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if completed.returncode == 87:
        return {
            "exit_code": 87,
            "crash_injected": crash,
            "checkpoint": inspect_case(case),
        }
    if completed.returncode:
        raise RuntimeError(f"acceptance subprocess failed: {completed.stderr}")
    return {"exit_code": 0, **json.loads(completed.stdout)}


def _duplicate_groups(db: sqlite3.Connection, table: str, columns: str) -> int:
    return db.execute(
        f"SELECT count(*) FROM (SELECT 1 FROM {table} GROUP BY {columns} HAVING count(*) > 1)"
    ).fetchone()[0]


def inspect_case(case: Path) -> dict[str, Any]:
    repository = _connect(case, readonly=True)
    db = repository.db
    counts = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in TABLES
    }
    duplicate_keys = {
        "research_cycles": "scheduled_at, observation_class",
        "cycle_ids": "cycle_id",
        "decision_snapshots": "epoch_id, snapshot_timestamp, observation_class",
        "snapshot_ids": "snapshot_id",
        "strategy_decisions": "snapshot_hash, strategy_id, strategy_version",
        "detector_decisions": "snapshot_hash, detector_version",
        "llm_decisions": "experiment_id, phase2_epoch_id, input_snapshot_hash, strategy_version",
        "llm_invocation_attempts": "experiment_id, phase2_epoch_id, input_snapshot_hash, attempt",
        "paper_trades": "strategy_decision_id",
        "paper_orders": "strategy_decision_id",
        "paper_fills": "paper_trade_id, fill_time, fill_type",
    }
    duplicate_tables = {
        "cycle_ids": "research_cycles",
        "snapshot_ids": "decision_snapshots",
    }
    duplicates = {
        name: _duplicate_groups(db, duplicate_tables.get(name, name), key)
        for name, key in duplicate_keys.items()
    }
    attempts = [
        dict(row)
        for row in db.execute(
            "SELECT input_snapshot_hash,attempt,raw_output_plaintext,raw_output_hash,raw_capture_status,provider_status,tool_calls_count,payload_json,integrity_hash FROM llm_invocation_attempts ORDER BY started_at, attempt"
        )
    ]
    records = [
        dict(row)
        for row in db.execute("SELECT * FROM llm_decisions ORDER BY timestamp")
    ]
    raw_checks = []
    for row in attempts:
        raw = row["raw_output_plaintext"]
        valid = None
        if row["provider_status"] == "VALID":
            parsed = LLMStructuredOutputV2.model_validate_json(raw)
            valid = parsed.input_snapshot_hash == row["input_snapshot_hash"]
        raw_checks.append(
            {
                "snapshot_hash": row["input_snapshot_hash"],
                "attempt": row["attempt"],
                "provider_status": row["provider_status"],
                "raw_retained": raw is not None,
                "raw_sha256_matches": raw is not None
                and hashlib.sha256(raw.encode()).hexdigest() == row["raw_output_hash"],
                "attempt_integrity_hash_matches": sha256_canonical(
                    json.loads(row["payload_json"])
                )
                == row["integrity_hash"],
                "valid_schema_and_input_lineage": valid,
                "tool_calls": row["tool_calls_count"],
            }
        )
    parsed_lineage = []
    model_identity = []
    for row in records:
        value = json.loads(row["payload_json"])
        model_identity.append(
            value["model"] == "gpt-5.6-terra"
            and value["model_version"] == "gpt-5.6-terra"
        )
        if row["runner_status"] != "VALID":
            continue
        matching = [
            item
            for item in attempts
            if item["input_snapshot_hash"] == row["input_snapshot_hash"]
            and item["provider_status"] == "VALID"
        ]
        same = len(matching) == 1
        if same:
            parsed = LLMStructuredOutputV2.model_validate_json(
                matching[0]["raw_output_plaintext"]
            ).model_dump(mode="json")
            same = all(
                (value[key] if key in value else value["metadata"].get(key))
                == parsed[key]
                for key in parsed
            )
            frozen = repository.core.load_snapshot(row["input_snapshot_hash"])
            same = (
                same
                and value["metadata"]["snapshot_payload_sha256"]
                == hashlib.sha256(canonical_json(frozen).encode()).hexdigest()
            )
        parsed_lineage.append(
            {
                "snapshot_hash": row["input_snapshot_hash"],
                "exact_parsed_raw_lineage": same,
                "schema_valid": value["schema_valid"],
            }
        )
    cycles = [
        dict(row)
        for row in db.execute(
            "SELECT scheduled_at, status, snapshot_hash, details_json FROM research_cycles ORDER BY scheduled_at"
        )
    ]
    snapshot_lineage = []
    for row in db.execute(
        "SELECT * FROM decision_snapshots ORDER BY snapshot_timestamp"
    ):
        stored = json.loads(row["canonical_json"])
        decisions = [
            dict(item)
            for item in db.execute(
                "SELECT strategy_id,strategy_version FROM strategy_decisions WHERE snapshot_hash=? ORDER BY strategy_id",
                (row["snapshot_hash"],),
            )
        ]
        detectors = [
            item[0]
            for item in db.execute(
                "SELECT detector_version FROM detector_decisions WHERE snapshot_hash=?",
                (row["snapshot_hash"],),
            )
        ]
        snapshot_lineage.append(
            {
                "snapshot_hash": row["snapshot_hash"],
                "boundary": row["snapshot_timestamp"],
                "epoch_id": row["epoch_id"],
                "scoreable": bool(row["scoreable"]),
                "snapshot_schema": stored["snapshot_schema_version"],
                "feature_schema": stored["feature_schema_version"],
                "rejection_reasons": stored["data_quality"]["rejection_reasons"],
                "strategies": decisions,
                "detectors": detectors,
            }
        )
    trades = [
        dict(row)
        for row in db.execute(
            "SELECT paper_trade_id,strategy_decision_id,strategy_id,status,entry_time,entry_price,exit_time,exit_reason,last_processed_at,flags_json FROM paper_trades ORDER BY strategy_id,signal_time"
        )
    ]
    orders = [
        dict(row)
        for row in db.execute(
            "SELECT order_id,paper_trade_id,strategy_decision_id,status,eligible_at,fill_time,fill_price FROM paper_orders ORDER BY order_id"
        )
    ]
    fills = [
        dict(row)
        for row in db.execute(
            "SELECT paper_trade_id,fill_time,fill_type,price FROM paper_fills ORDER BY fill_time,fill_type"
        )
    ]
    activation = db.execute(
        "SELECT activation_timestamp FROM phase2_manifests"
    ).fetchone()[0]
    past = db.execute(
        "SELECT count(*) FROM research_cycles WHERE scheduled_at < ?", (activation,)
    ).fetchone()[0]
    running = sum(row["status"] == "RUNNING" for row in cycles)
    orphan_trades = db.execute(
        "SELECT count(*) FROM paper_trades t LEFT JOIN paper_orders o USING(paper_trade_id) WHERE o.order_id IS NULL"
    ).fetchone()[0]
    attempts_without_decision = db.execute(
        "SELECT count(*) FROM llm_invocation_attempts a LEFT JOIN llm_decisions d ON a.experiment_id=d.experiment_id AND a.phase2_epoch_id=d.phase2_epoch_id AND a.input_snapshot_hash=d.input_snapshot_hash WHERE d.decision_id IS NULL"
    ).fetchone()[0]
    attempt_policy = db.execute(
        "SELECT count(*) FROM (SELECT count(*) n, max(attempt) a FROM llm_invocation_attempts GROUP BY input_snapshot_hash HAVING n>2 OR a>2)"
    ).fetchone()[0]
    result = {
        "database": str(_database(case)),
        "counts": counts,
        "cycles": cycles,
        "snapshot_lineage": snapshot_lineage,
        "trades": trades,
        "orders": orders,
        "fills": fills,
        "duplicate_groups": duplicates,
        "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
        "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
        "foreign_key_violations": len(
            db.execute("PRAGMA foreign_key_check").fetchall()
        ),
        "raw_audit": raw_checks,
        "parsed_raw_lineage": parsed_lineage,
        "all_record_model_identities_match_frozen": all(model_identity),
        "llm_decision_integrity_hashes_match": all(
            sha256_canonical(json.loads(row["payload_json"])) == row["integrity_hash"]
            for row in records
        ),
        "snapshot_hashes_match": all(
            freeze_snapshot(repository.core.load_snapshot(row[0])).snapshot_hash
            == row[0]
            for row in db.execute("SELECT snapshot_hash FROM decision_snapshots")
        ),
        "attempt_policy_violation_groups": attempt_policy,
        "running_cycles": running,
        "trades_missing_orders": orphan_trades,
        "attempts_without_final_decision": attempts_without_decision,
        "boundaries_before_fixture_activation": past,
        "utc_quarter_hour_aligned": all(
            (at := datetime.fromisoformat(row["scheduled_at"])).utcoffset()
            == timedelta(0)
            and at.minute % 15 == 0
            and at.second == 0
            and at.microsecond == 0
            for row in cycles
        ),
        "permanently_non_scored": True,
    }
    db.close()
    return result


def run_suite(destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError("use a fresh acceptance suite directory")
    destination.mkdir(parents=True)
    result: dict[str, Any] = {
        "audit_class": AUDIT_CLASS,
        "permanently_non_scored": True,
        "identities": config_identities(),
        "cases": {},
    }
    for name, scenario, mode, crash in (
        ("normal_progression", "normal", "valid", "none"),
        ("pending_without_eligible_bar", "pending_without_bar", "valid", "none"),
        ("ttl_exit", "ttl", "valid", "none"),
        ("adverse_first", "adverse_first", "valid", "none"),
        ("malformed_retry", "normal", "malformed_then_valid", "none"),
        ("malformed_exhaustion", "normal", "malformed_exhaustion", "none"),
        ("interrupted_before_collection", "normal", "valid", "before_collection"),
        ("interrupted_after_trade", "normal", "valid", "after_trade_before_order"),
        (
            "interrupted_after_attempt",
            "normal",
            "valid",
            "after_attempt_before_decision",
        ),
    ):
        case = destination / name
        create_case(case, scenario=scenario, response_mode=mode)
        trace = [invoke_worker(case, 0, crash=crash)]
        # Every invocation creates a new OS process, proving restart with the
        # persisted pending position, not merely constructing a new Python object.
        trace.append(invoke_worker(case, 0))
        trace.append(invoke_worker(case, 1))
        trace.append(invoke_worker(case, 1))
        trace.append(invoke_worker(case, 2))
        if scenario == "pending_without_bar":
            # Five future planned invocations, not a backfill: show pending is
            # not silently expired even after its 60-minute TTL has elapsed.
            trace.extend(invoke_worker(case, number) for number in (3, 4, 5))
        final = inspect_case(case)
        final["database_sha256"] = file_sha256(_database(case))
        result["cases"][name] = {"trace": trace, "final": final}
    case = destination / "interrupted_during_entry"
    create_case(case)
    trace = [invoke_worker(case, 0)]
    trace.append(invoke_worker(case, 1, crash="after_entry_fill_before_trade"))
    trace.append(invoke_worker(case, 1))
    trace.append(invoke_worker(case, 2))
    final = inspect_case(case)
    final["database_sha256"] = file_sha256(_database(case))
    result["cases"][case.name] = {"trace": trace, "final": final}
    blockers = []
    for name, value in result["cases"].items():
        final = value["final"]
        for field in (
            "running_cycles",
            "trades_missing_orders",
            "attempts_without_final_decision",
        ):
            if final[field]:
                blockers.append(
                    {"case": name, "invariant": field, "count": final[field]}
                )
        if (
            final["integrity"] != "ok"
            or final["foreign_key_violations"]
            or any(final["duplicate_groups"].values())
        ):
            blockers.append(
                {"case": name, "invariant": "database_or_duplicate_failure"}
            )
    result["blocking_witnesses"] = blockers
    result["disposition"] = (
        "PHASE_2_SIMULATOR_ACCEPTANCE_BLOCKED"
        if blockers
        else "PHASE_2_SIMULATOR_ACCEPTANCE_PASSED"
    )
    result["caveats"] = [
        "Offline fixture provider proves persistence/schema controls, not fresh real-provider availability.",
        "Fixture timestamps are simulated chronology, never production evidence or production backfill.",
        "Frozen simulator has no pending-entry price-trigger or pending-expiry rule; TTL begins after entry.",
        "Harness test pass can mean a known failure witness reproduced; it is not acceptance pass.",
        "Supervisor restart acceptance is reported separately by the external-supervision harness.",
    ]
    (destination / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("case", type=Path)
    worker.add_argument("boundary", type=int)
    worker.add_argument("--crash", default="none")
    suite = sub.add_parser("suite")
    suite.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(run_worker(args.case, args.boundary, crash=args.crash)))
    else:
        value = run_suite(args.destination)
        print(
            json.dumps(
                {
                    "result": str(args.destination / "result.json"),
                    "disposition": value["disposition"],
                    "blocking_witnesses": value["blocking_witnesses"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
