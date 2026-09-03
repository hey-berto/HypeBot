from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from hype_autopilot.data.models import AssetContext
from hype_autopilot.features.models import FeatureSet
from hype_autopilot.phase2.config import ACTIVATION_PHRASE, load_phase2_config
from hype_autopilot.phase2.isolation import (
    IsolationViolation,
    validate_phase2_database_path,
)
from hype_autopilot.phase2.manifest import build_activation_manifest
from hype_autopilot.phase2.models import (
    FailClosedReason,
    LLMStructuredOutput,
    LLMStructuredOutputV2,
    ProviderResponse,
    RunnerStatus,
)
from hype_autopilot.phase2.provider import (
    ProviderError,
    ProviderTimeout,
    output_json_schema,
)
from hype_autopilot.phase2.runner import FailClosedLLMRunner, adapt_llm_decision
from hype_autopilot.phase2.storage import Phase2Repository
from hype_autopilot.regimes.models import Regime, TrendRegime, VolatilityRegime
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from hype_autopilot.snapshots.models import (
    DataQuality,
    DecisionSnapshot,
    MarketSnapshot,
)
from hype_autopilot.strategies.base import Decision

WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG_PATH = WORKSPACE / "config/phase2/phase2_epoch_001.yaml"
CONFIG_V2_PATH = WORKSPACE / "config/phase2/phase2_epoch_002.yaml"


def phase2_snapshot(*, scoreable: bool = True) -> DecisionSnapshot:
    at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    context = AssetContext(
        symbol="HYPE",
        source_timestamp=at,
        received_at=at,
        mark_price=100,
        mid_price=100,
        oracle_price=100,
        funding_rate=0.0001,
        open_interest=1_000_000,
        day_notional_volume=50_000_000,
    )
    return freeze_snapshot(
        DecisionSnapshot(
            snapshot_id=f"phase2-{'scoreable' if scoreable else 'rejected'}",
            snapshot_timestamp=at,
            created_at=at,
            epoch_id="phase2_epoch_001",
            market=MarketSnapshot(
                hype_context=context,
                hype_features=FeatureSet(last_15m_close=100, atr14_1h=2),
            ),
            regime=Regime(
                trend=TrendRegime.UP,
                volatility=VolatilityRegime.NORMAL,
                combined="UP_NORMAL",
            ),
            data_quality=DataQuality(
                required_sources_present=scoreable,
                scoreable=scoreable,
                rejection_reasons=() if scoreable else ("SYNTHETIC_REJECTION",),
            ),
            source_cutoffs={"hype_context": at},
        )
    )


def valid_output(snapshot_hash: str, **updates: object) -> str:
    payload: dict[str, object] = {
        "input_snapshot_hash": snapshot_hash,
        "output_schema_version": "LLM_OUTPUT_V1",
        "decision": "LONG",
        "confidence": "HIGH",
        "rationale_tags": ["trend-confirmed"],
        "bull_case": ["positive trend"],
        "bear_case": ["funding risk"],
        "data_conflicts": [],
        "invocation_reason": "SCHEDULED_RESEARCH",
        "entry": {"mode": "NOW", "trigger_price": None},
        "stop": {"kind": "ABSOLUTE_PRICE", "price": 95},
        "target": {"kind": "ABSOLUTE_PRICE", "price": 110},
        "invalidation": {"category": "TREND_BREAK", "reference_price": 95, "tags": []},
        "ttl_minutes": 60,
    }
    payload.update(updates)
    return json.dumps(payload)


class FakeProvider:
    def __init__(self, results: list[object], snapshot_at: datetime) -> None:
        self.results = list(results)
        self.snapshot_at = snapshot_at
        self.calls = 0
        self.last_snapshot_json: str | None = None

    def invoke(
        self, *, prompt: str, snapshot_json: str, timeout_seconds: int
    ) -> ProviderResponse:
        self.calls += 1
        self.last_snapshot_json = snapshot_json
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return ProviderResponse(
            raw_output=str(result),
            model="gpt-5.6-terra",
            model_version="gpt-5.6-terra",
            request_started_at=self.snapshot_at + timedelta(seconds=10),
            request_ended_at=self.snapshot_at + timedelta(seconds=11),
            input_tokens=100,
            cached_input_tokens=20,
            output_tokens=30,
            cost_usd=0.0005,
            tool_calls_count=0,
        )


def make_runner(
    snapshot: DecisionSnapshot,
    results: list[object],
    config_path: Path = CONFIG_PATH,
) -> tuple[FailClosedLLMRunner, Phase2Repository, FakeProvider]:
    config, _ = load_phase2_config(config_path)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    repository = Phase2Repository(db)
    repository.initialize()
    repository.core.save_snapshot(snapshot)
    provider = FakeProvider(results, snapshot.snapshot_timestamp)
    runner = FailClosedLLMRunner(
        config=config,
        provider=provider,
        repository=repository,
        prompt="snapshot only",
        experiment_id="phase2-build-synthetic",
        clock=lambda: snapshot.snapshot_timestamp + timedelta(seconds=10),
    )
    return runner, repository, provider


def test_build_config_is_non_scored_and_path_isolated(tmp_path: Path):
    config, digest = load_phase2_config(CONFIG_PATH)
    config.assert_build_only()
    assert len(digest) == 64
    assert not config.evidence_collection_enabled
    assert not config.activation_authorized
    allowed = validate_phase2_database_path(
        "data/phase2/test.sqlite3", tmp_path / "phase2-worktree"
    )
    assert "phase2" in str(allowed)
    with pytest.raises(IsolationViolation):
        validate_phase2_database_path(
            "/tmp/hype-phase1-acceptance.sqlite3", tmp_path / "phase2-worktree"
        )
    with pytest.raises(IsolationViolation):
        validate_phase2_database_path(
            "data/phase2/test.sqlite3-wal", tmp_path / "phase2-worktree"
        )


def test_activation_manifest_requires_both_gates_and_exact_authorization():
    config, digest = load_phase2_config(CONFIG_PATH)
    enabled = config.model_copy(
        update={"evidence_collection_enabled": True, "activation_authorized": True}
    )
    at = datetime(2026, 9, 2, tzinfo=UTC)
    with pytest.raises(PermissionError):
        build_activation_manifest(
            config=enabled,
            experiment_id="x",
            activation_timestamp=at,
            authorization="Start it",
            git_commit_hash="a" * 40,
            config_hash=digest,
            prompt_hash="b" * 64,
            output_schema_hash="c" * 64,
            database_schema_hash="d" * 64,
        )
    manifest = build_activation_manifest(
        config=enabled,
        experiment_id="x",
        activation_timestamp=at,
        authorization=ACTIVATION_PHRASE,
        git_commit_hash="a" * 40,
        config_hash=digest,
        prompt_hash="b" * 64,
        output_schema_hash="c" * 64,
        database_schema_hash="d" * 64,
    )
    assert manifest.authorization_phrase == ACTIVATION_PHRASE
    assert (
        manifest.frozen_contract["pair_designations"]["LLM_V1_vs_QUANT_TREND_V1"]
        == "CONFIRMATORY"
    )
    assert (
        manifest.frozen_contract["pair_designations"]["LLM_V1_vs_QUANT_MR_V1"]
        == "CONFIRMATORY"
    )
    assert manifest.database_schema_hash == "d" * 64


def test_known_snapshot_hash_fixture_and_structured_long_short_no_trade():
    snapshot = phase2_snapshot()
    assert (
        snapshot.snapshot_hash
        == "8f130b2b6fd2ce7bac256ee54f81c920d59d261b23e252fbf3157a529416b927"
    )
    long_output = LLMStructuredOutput.model_validate_json(
        valid_output(snapshot.snapshot_hash)
    )
    short_output = LLMStructuredOutput.model_validate_json(
        valid_output(
            snapshot.snapshot_hash,
            decision="SHORT",
            stop={"kind": "ABSOLUTE_PRICE", "price": 105},
            target={"kind": "ABSOLUTE_PRICE", "price": 90},
        )
    )
    no_trade_output = LLMStructuredOutput.model_validate(
        {
            "input_snapshot_hash": snapshot.snapshot_hash,
            "output_schema_version": "LLM_OUTPUT_V1",
            "decision": "NO_TRADE",
            "confidence": "LOW",
            "entry": {"mode": "NONE", "trigger_price": None},
        }
    )
    assert long_output.decision == Decision.LONG
    assert short_output.decision == Decision.SHORT
    assert no_trade_output.decision == Decision.NO_TRADE


def test_valid_runner_persists_exact_lineage_and_is_idempotent():
    snapshot = phase2_snapshot()
    runner, repository, provider = make_runner(
        snapshot, [valid_output(snapshot.snapshot_hash)]
    )
    first = runner.evaluate(snapshot)
    second = runner.evaluate(snapshot)
    assert first == second
    assert provider.calls == 1
    assert (
        json.loads(provider.last_snapshot_json)["snapshot_hash"]
        == snapshot.snapshot_hash
    )
    assert first.runner_status == RunnerStatus.VALID
    assert first.input_snapshot_hash == snapshot.snapshot_hash
    assert first.schema_valid and first.geometry_valid and first.tool_integrity_ok
    assert first.input_tokens == 100 and first.output_tokens == 30
    assert (
        repository.db.execute(
            "SELECT COUNT(*) FROM llm_invocation_attempts"
        ).fetchone()[0]
        == 1
    )
    assert (
        repository.db.execute("SELECT COUNT(*) FROM llm_decisions").fetchone()[0] == 1
    )
    adapted = adapt_llm_decision(first, "LLM_GEOMETRY_ADAPTER_V1")
    assert adapted.decision == Decision.LONG
    assert adapted.stop_reference == 95 and adapted.target_reference == 110
    assert adapted.created_at == first.request_ended_at


def test_openai_output_schema_is_recursively_strict():
    schema = output_json_schema()

    def verify(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for value in node.values():
                verify(value)
        elif isinstance(node, list):
            for value in node:
                verify(value)

    verify(schema)


def test_v2_schema_constrains_version_at_provider_boundary():
    schema = output_json_schema("LLM_OUTPUT_V2")
    assert schema["properties"]["output_schema_version"] == {
        "const": "LLM_OUTPUT_V2",
        "title": "Output Schema Version",
        "type": "string",
    }
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate_json(
            valid_output("a" * 64, output_schema_version="OTHER")
        )


def test_v2_runner_rejects_wrong_version_in_schema_and_accepts_literal():
    snapshot = phase2_snapshot()
    invalid = valid_output(snapshot.snapshot_hash, output_schema_version="OTHER")
    runner, _, provider = make_runner(
        snapshot, [invalid, invalid], config_path=CONFIG_V2_PATH
    )
    rejected = runner.evaluate(snapshot)
    assert rejected.reason_code == FailClosedReason.RETRY_EXHAUSTED
    assert rejected.metadata["terminal_cause"] == FailClosedReason.INVALID_SCHEMA.value
    assert provider.calls == 2

    snapshot = snapshot.model_copy(update={"snapshot_id": "phase2-v2-valid"})
    snapshot = freeze_snapshot(snapshot.model_copy(update={"snapshot_hash": None}))
    valid = valid_output(snapshot.snapshot_hash, output_schema_version="LLM_OUTPUT_V2")
    runner, _, provider = make_runner(snapshot, [valid], config_path=CONFIG_V2_PATH)
    accepted = runner.evaluate(snapshot)
    assert accepted.runner_status == RunnerStatus.VALID
    assert accepted.output_schema_version == "LLM_OUTPUT_V2"
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda h: valid_output("b" * 64), FailClosedReason.SNAPSHOT_HASH_MISMATCH),
        (
            lambda h: valid_output(h, output_schema_version="OTHER"),
            FailClosedReason.UNSUPPORTED_SCHEMA_VERSION,
        ),
        (
            lambda h: valid_output(h, stop={"kind": "ABSOLUTE_PRICE", "price": 105}),
            FailClosedReason.INVALID_GEOMETRY,
        ),
    ],
)
def test_integrity_and_geometry_violations_fail_closed_without_retry(mutator, reason):
    snapshot = phase2_snapshot()
    runner, _, provider = make_runner(snapshot, [mutator(snapshot.snapshot_hash)])
    record = runner.evaluate(snapshot)
    assert record.decision == Decision.NO_TRADE
    assert record.reason_code == reason
    assert provider.calls == 1


def test_malformed_output_retries_once_then_succeeds():
    snapshot = phase2_snapshot()
    runner, repository, provider = make_runner(
        snapshot, ["not-json", valid_output(snapshot.snapshot_hash)]
    )
    record = runner.evaluate(snapshot)
    assert record.runner_status == RunnerStatus.VALID
    assert record.retry_count == 1 and provider.calls == 2
    assert (
        repository.db.execute(
            "SELECT COUNT(*) FROM llm_invocation_attempts"
        ).fetchone()[0]
        == 2
    )


def test_malformed_or_invalid_schema_retry_exhaustion_fails_closed():
    for results, terminal_cause in (
        (["not-json", "still-not-json"], FailClosedReason.MALFORMED_JSON),
        (["{}", "{}"], FailClosedReason.INVALID_SCHEMA),
    ):
        snapshot = phase2_snapshot()
        runner, repository, provider = make_runner(snapshot, results)
        record = runner.evaluate(snapshot)
        assert record.reason_code == FailClosedReason.RETRY_EXHAUSTED
        assert record.metadata["terminal_cause"] == terminal_cause.value
        assert provider.calls == 2
        assert (
            repository.db.execute(
                "SELECT COUNT(*) FROM llm_invocation_attempts"
            ).fetchone()[0]
            == 2
        )


def test_tool_call_violation_is_immediate_fail_closed():
    snapshot = phase2_snapshot()
    runner, _, provider = make_runner(snapshot, [valid_output(snapshot.snapshot_hash)])
    original = provider.invoke

    def with_tool(**kwargs):
        return original(**kwargs).model_copy(update={"tool_calls_count": 1})

    provider.invoke = with_tool
    record = runner.evaluate(snapshot)
    assert record.reason_code == FailClosedReason.TOOL_INTEGRITY_VIOLATION
    assert provider.calls == 1 and not record.tool_integrity_ok


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (ProviderTimeout("timeout"), FailClosedReason.TIMEOUT),
        (ProviderError("api"), FailClosedReason.API_MODEL_ERROR),
    ],
)
def test_provider_failures_are_recorded_and_fail_closed(result, reason):
    snapshot = phase2_snapshot()
    runner, repository, provider = make_runner(snapshot, [result])
    record = runner.evaluate(snapshot)
    assert record.reason_code == reason and provider.calls == 1
    assert (
        repository.db.execute(
            "SELECT COUNT(*) FROM llm_invocation_attempts"
        ).fetchone()[0]
        == 1
    )


def test_stale_or_rejected_snapshot_never_calls_provider():
    for snapshot in (phase2_snapshot(scoreable=False), phase2_snapshot()):
        runner, _, provider = make_runner(
            snapshot, [valid_output(snapshot.snapshot_hash)]
        )
        if snapshot.data_quality.scoreable:
            runner.clock = lambda value=snapshot: (
                value.snapshot_timestamp + timedelta(seconds=121)
            )
            expected = FailClosedReason.STALE_SNAPSHOT
        else:
            expected = FailClosedReason.SNAPSHOT_NOT_SCOREABLE
        record = runner.evaluate(snapshot)
        assert record.reason_code == expected
        assert provider.calls == 0


def test_phase2_tables_are_immutable_and_database_is_clean():
    snapshot = phase2_snapshot()
    runner, repository, _ = make_runner(
        snapshot, [valid_output(snapshot.snapshot_hash)]
    )
    runner.evaluate(snapshot)
    with pytest.raises(sqlite3.DatabaseError):
        repository.db.execute("UPDATE llm_decisions SET decision='SHORT'")
    assert repository.integrity() == ("ok", 0)
