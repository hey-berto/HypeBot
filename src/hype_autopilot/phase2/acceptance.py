from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hype_autopilot.data.models import AssetContext, ObservationClass
from hype_autopilot.features.models import FeatureSet
from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.config import (
    ACTIVATION_PHRASE,
    file_sha256,
    load_phase2_config,
    resolve_inside_workspace,
)
from hype_autopilot.phase2.isolation import connect_phase2
from hype_autopilot.phase2.manifest import build_activation_manifest
from hype_autopilot.phase2.models import ProviderResponse
from hype_autopilot.phase2.pipeline import Phase2Pipeline
from hype_autopilot.phase2.provider import (
    ProviderTimeout,
    openai_provider_from_config,
    output_json_schema,
)
from hype_autopilot.phase2.runner import FailClosedLLMRunner
from hype_autopilot.phase2.scheduler import (
    planned_phase2_boundary,
    run_phase2_boundary,
)
from hype_autopilot.phase2.storage import Phase2Repository, phase2_database_schema_hash
from hype_autopilot.regimes.models import Regime, TrendRegime, VolatilityRegime
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from hype_autopilot.snapshots.models import (
    DataQuality,
    DecisionSnapshot,
    MarketSnapshot,
)

NON_SCORED_CANARY = "NON_SCORED_CANARY"
NON_SCORED_SCHEDULER_ACCEPTANCE = "NON_SCORED_SCHEDULER_ACCEPTANCE"


class _CapturingProvider:
    def __init__(self, provider: object) -> None:
        self.provider = provider
        self.raw_outputs: list[str] = []

    def invoke(
        self, *, prompt: str, snapshot_json: str, timeout_seconds: int
    ) -> ProviderResponse:
        response = self.provider.invoke(
            prompt=prompt,
            snapshot_json=snapshot_json,
            timeout_seconds=timeout_seconds,
        )
        self.raw_outputs.append(response.raw_output)
        return response


def synthetic_acceptance_snapshot(
    at: datetime, *, snapshot_id: str, epoch_id: str = "phase2_epoch_001"
) -> DecisionSnapshot:
    at = at.astimezone(UTC)
    context = AssetContext(
        symbol="HYPE",
        source_timestamp=at,
        received_at=at,
        mark_price=100.0,
        mid_price=100.0,
        oracle_price=100.0,
        funding_rate=0.0001,
        open_interest=1_000_000.0,
        day_notional_volume=50_000_000.0,
    )
    return freeze_snapshot(
        DecisionSnapshot(
            snapshot_id=snapshot_id,
            snapshot_timestamp=at,
            created_at=at,
            epoch_id=epoch_id,
            observation_class=ObservationClass.SCORED_PROSPECTIVE,
            market=MarketSnapshot(
                hype_context=context,
                hype_features=FeatureSet(last_15m_close=100.0, atr14_1h=2.0),
            ),
            regime=Regime(
                trend=TrendRegime.UP,
                volatility=VolatilityRegime.NORMAL,
                combined="UP_NORMAL",
            ),
            data_quality=DataQuality(
                required_sources_present=True,
                scoreable=True,
                rejection_reasons=(),
            ),
            source_cutoffs={"hype_context": at},
        )
    )


def _new_acceptance_repository(database: Path, workspace: Path) -> Phase2Repository:
    if database.exists():
        raise FileExistsError(
            f"acceptance database already exists; refusing a second run: {database}"
        )
    database.parent.mkdir(parents=True, exist_ok=True)
    repository = Phase2Repository(connect_phase2(database, workspace))
    repository.initialize()
    return repository


def _git_head(workspace: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run_real_provider_canary(
    *,
    workspace: str | Path,
    database_path: str | Path,
    config_path: str | Path = "config/phase2/phase2_epoch_001.yaml",
) -> dict[str, Any]:
    """Make one real, permanently non-scored structured-output call."""
    root = Path(workspace).resolve()
    resolved_config = resolve_inside_workspace(config_path, root)
    config, config_hash = load_phase2_config(resolved_config)
    config.assert_build_only()
    database = resolve_inside_workspace(database_path, root)
    repository = _new_acceptance_repository(database, root)
    at = datetime.now(UTC)
    snapshot = synthetic_acceptance_snapshot(
        at,
        snapshot_id=f"non-scored-real-provider-canary-{database.stem}",
        epoch_id=config.phase2_epoch_id,
    )
    repository.core.save_snapshot(snapshot)
    prompt_path = resolve_inside_workspace(config.prompt_path, root)
    prompt = prompt_path.read_text(encoding="utf-8")
    provider = _CapturingProvider(
        openai_provider_from_config(config, workspace_root=str(root))
    )
    runner = FailClosedLLMRunner(
        config=config,
        provider=provider,
        repository=repository,
        prompt=prompt,
        experiment_id=NON_SCORED_CANARY,
    )
    record = runner.evaluate(snapshot)
    attempts = repository.db.execute(
        "SELECT experiment_id, provider_status, error_code, tool_calls_count, "
        "started_at, ended_at, raw_output_hash, raw_output_plaintext, "
        "raw_capture_status FROM llm_invocation_attempts ORDER BY attempt"
    ).fetchall()
    integrity, foreign_keys = repository.integrity()
    return {
        "audit_class": NON_SCORED_CANARY,
        "database": str(database),
        "source_commit": _git_head(root),
        "config_hash": config_hash,
        "prompt_hash": file_sha256(prompt_path),
        "output_schema_hash": sha256_canonical(
            output_json_schema(config.output_schema_version)
        ),
        "raw_outputs": provider.raw_outputs,
        "database_schema_hash": phase2_database_schema_hash(),
        "snapshot_hash": snapshot.snapshot_hash,
        "model": record.model,
        "model_version": record.model_version,
        "runner_status": record.runner_status.value,
        "reason_code": record.reason_code.value,
        "decision": record.decision.value,
        "schema_valid": record.schema_valid,
        "geometry_valid": record.geometry_valid,
        "tool_calls_count": record.tool_calls_count,
        "tool_integrity_ok": record.tool_integrity_ok,
        "retry_count": record.retry_count,
        "request_started_at": record.request_started_at.isoformat(),
        "request_ended_at": record.request_ended_at.isoformat(),
        "latency_ms": record.latency_ms,
        "snapshot_to_call_age_seconds": record.snapshot_to_call_age_seconds,
        "input_tokens": record.input_tokens,
        "cached_input_tokens": record.cached_input_tokens,
        "output_tokens": record.output_tokens,
        "model_cost_usd": record.model_cost_usd,
        "attempts": [dict(row) for row in attempts],
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "manifests": repository.db.execute(
            "SELECT COUNT(*) FROM phase2_manifests"
        ).fetchone()[0],
        "scored_cycles": repository.db.execute(
            "SELECT COUNT(*) FROM research_cycles"
        ).fetchone()[0],
        "strategy_decisions": repository.db.execute(
            "SELECT COUNT(*) FROM strategy_decisions"
        ).fetchone()[0],
        "paper_trades": repository.db.execute(
            "SELECT COUNT(*) FROM paper_trades"
        ).fetchone()[0],
        "permanently_non_scored": True,
    }


class _AcceptanceClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _SyntheticCollector:
    def __init__(self) -> None:
        self.trace: list[dict[str, str]] = []

    def collect_incremental(self, *, end: datetime, observation_class: str) -> None:
        self.trace.append(
            {
                "action": "collect_incremental",
                "boundary": end.isoformat(),
                "observation_class": str(observation_class),
            }
        )

    def recover_gaps(self, boundary: datetime) -> None:
        self.trace.append({"action": "recover_gaps", "boundary": boundary.isoformat()})


class _SyntheticBuilder:
    def __init__(
        self, clock: _AcceptanceClock, epoch_id: str = "phase2_epoch_001"
    ) -> None:
        self.clock = clock
        self.epoch_id = epoch_id

    def build(self, boundary: datetime, *, observation_class: str) -> DecisionSnapshot:
        self.clock.value = boundary + timedelta(seconds=5)
        return synthetic_acceptance_snapshot(
            boundary,
            snapshot_id=f"scheduler-acceptance-{boundary.isoformat()}",
            epoch_id=self.epoch_id,
        )


class _TwoBoundaryProvider:
    def __init__(self, output_schema_version: str) -> None:
        self.calls = 0
        self.output_schema_version = output_schema_version

    def invoke(
        self, *, prompt: str, snapshot_json: str, timeout_seconds: int
    ) -> ProviderResponse:
        self.calls += 1
        snapshot = json.loads(snapshot_json)
        at = datetime.fromisoformat(snapshot["snapshot_timestamp"])
        if self.calls == 1:
            raise ProviderTimeout("injected acceptance timeout")
        raw = {
            "input_snapshot_hash": snapshot["snapshot_hash"],
            "output_schema_version": self.output_schema_version,
            "decision": "NO_TRADE",
            "confidence": "LOW",
            "rationale_tags": ["scheduler-recovery-acceptance"],
            "bull_case": [],
            "bear_case": [],
            "data_conflicts": [],
            "invocation_reason": NON_SCORED_SCHEDULER_ACCEPTANCE,
            "entry": {"mode": "NONE", "trigger_price": None},
            "stop": None,
            "target": None,
            "invalidation": None,
            "ttl_minutes": None,
        }
        return ProviderResponse(
            raw_output=json.dumps(raw),
            model="gpt-5.6-terra",
            model_version="gpt-5.6-terra",
            request_started_at=at + timedelta(seconds=10),
            request_ended_at=at + timedelta(seconds=11),
            input_tokens=100,
            cached_input_tokens=20,
            output_tokens=30,
            cost_usd=0.0005,
            tool_calls_count=0,
        )


def run_scheduler_acceptance(
    *,
    workspace: str | Path,
    database_path: str | Path,
    config_path: str | Path = "config/phase2/phase2_epoch_001.yaml",
) -> dict[str, Any]:
    """Exercise actual boundary orchestration in an isolated non-scored database."""
    root = Path(workspace).resolve()
    resolved_config = resolve_inside_workspace(config_path, root)
    frozen_config, config_hash = load_phase2_config(resolved_config)
    frozen_config.assert_build_only()
    config = frozen_config.model_copy(
        update={"evidence_collection_enabled": True, "activation_authorized": True}
    )
    database = resolve_inside_workspace(database_path, root)
    repository = _new_acceptance_repository(database, root)
    prompt_path = resolve_inside_workspace(config.prompt_path, root)
    prompt_hash = file_sha256(prompt_path)
    schema_hash = sha256_canonical(output_json_schema(config.output_schema_version))
    db_schema_hash = phase2_database_schema_hash()
    commit = _git_head(root)
    first_boundary = planned_phase2_boundary(datetime(2026, 9, 2, 0, 7, 30, tzinfo=UTC))
    second_boundary = first_boundary + timedelta(minutes=15)
    manifest = build_activation_manifest(
        config=config,
        experiment_id=NON_SCORED_SCHEDULER_ACCEPTANCE,
        activation_timestamp=first_boundary,
        authorization=ACTIVATION_PHRASE,
        git_commit_hash=commit,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        output_schema_hash=schema_hash,
        database_schema_hash=db_schema_hash,
    )
    repository.save_manifest(manifest)
    clock = _AcceptanceClock(first_boundary + timedelta(seconds=5))
    collector = _SyntheticCollector()
    provider = _TwoBoundaryProvider(config.output_schema_version)

    def build_pipeline() -> Phase2Pipeline:
        return Phase2Pipeline(
            config=config,
            repository=repository,
            builder=_SyntheticBuilder(clock, config.phase2_epoch_id),
            collector=collector,
            llm_runner=FailClosedLLMRunner(
                config=config,
                provider=provider,
                repository=repository,
                prompt=(root / config.prompt_path).read_text(encoding="utf-8"),
                experiment_id=NON_SCORED_SCHEDULER_ACCEPTANCE,
                clock=clock,
            ),
            simulator=PaperSimulator(repository.core),
            git_commit_hash=commit,
            config_hash=config_hash,
            prompt_hash=prompt_hash,
            output_schema_hash=schema_hash,
            database_schema_hash=db_schema_hash,
        )

    first_pipeline = build_pipeline()
    first_status = run_phase2_boundary(
        first_pipeline, manifest=manifest, boundary=first_boundary
    )
    duplicate_status = run_phase2_boundary(
        first_pipeline, manifest=manifest, boundary=first_boundary
    )
    restarted_pipeline = build_pipeline()
    restart_duplicate_status = run_phase2_boundary(
        restarted_pipeline, manifest=manifest, boundary=first_boundary
    )
    second_status = run_phase2_boundary(
        restarted_pipeline, manifest=manifest, boundary=second_boundary
    )
    cycles = repository.db.execute(
        "SELECT scheduled_at, status, snapshot_hash, details_json FROM research_cycles "
        "ORDER BY scheduled_at"
    ).fetchall()
    decisions = repository.db.execute(
        "SELECT timestamp, runner_status, reason_code, payload_json FROM llm_decisions "
        "ORDER BY timestamp"
    ).fetchall()
    strategy_counts = repository.db.execute(
        "SELECT snapshot_hash, COUNT(*) AS decision_count, "
        "SUM(CASE WHEN strategy_id LIKE 'HYBRID_%' THEN 1 ELSE 0 END) AS hybrid_count "
        "FROM strategy_decisions GROUP BY snapshot_hash ORDER BY snapshot_hash"
    ).fetchall()
    integrity, foreign_keys = repository.integrity()
    return {
        "audit_class": NON_SCORED_SCHEDULER_ACCEPTANCE,
        "database": str(database),
        "source_commit": commit,
        "config_hash": config_hash,
        "planned_from": "2026-09-02T00:07:30+00:00",
        "boundaries": [first_boundary.isoformat(), second_boundary.isoformat()],
        "statuses": {
            "first": first_status,
            "same_process_duplicate": duplicate_status,
            "restart_duplicate": restart_duplicate_status,
            "post_failure_next_boundary": second_status,
        },
        "provider_calls": provider.calls,
        "cycles": [
            {
                "scheduled_for": row["scheduled_at"],
                "status": row["status"],
                "snapshot_hash": row["snapshot_hash"],
                "details": json.loads(row["details_json"]),
            }
            for row in cycles
        ],
        "llm_decisions": [
            {
                "timestamp": row["timestamp"],
                "runner_status": row["runner_status"],
                "reason_code": row["reason_code"],
                "invocation_reason": json.loads(row["payload_json"])[
                    "invocation_reason"
                ],
            }
            for row in decisions
        ],
        "strategy_counts": [dict(row) for row in strategy_counts],
        "collector_trace": collector.trace,
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "production_config_gates": {
            "evidence_collection_enabled": frozen_config.evidence_collection_enabled,
            "activation_authorized": frozen_config.activation_authorized,
        },
        "permanently_non_scored": True,
    }
