from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hype_autopilot.data.collectors import MarketDataCollector
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.phase2.config import Phase2Config, config_manifest_fields
from hype_autopilot.phase2.hybrid import HybridAgreementStrategy
from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.runner import FailClosedLLMRunner, adapt_llm_decision
from hype_autopilot.phase2.storage import Phase2Repository
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.snapshots.models import DecisionSnapshot
from hype_autopilot.strategies.base import StrategyDecision
from hype_autopilot.strategies.quant_mean_reversion_v1 import QuantMeanReversionV1
from hype_autopilot.strategies.quant_trend_v1 import QuantTrendV1
from hype_autopilot.strategies.setup_detector_v1 import SetupDetectorV1


@dataclass(frozen=True)
class Phase2CycleResult:
    snapshot_hash: str
    decisions: tuple[StrategyDecision, ...]
    submitted_trade_count: int


class Phase2Pipeline:
    """Prospective Phase 2 scorer; intentionally unreachable in build-only configuration."""

    def __init__(
        self,
        *,
        config: Phase2Config,
        repository: Phase2Repository,
        builder: SnapshotBuilder,
        collector: MarketDataCollector,
        llm_runner: FailClosedLLMRunner,
        simulator: PaperSimulator,
        git_commit_hash: str,
        config_hash: str,
        prompt_hash: str,
        output_schema_hash: str,
        database_schema_hash: str,
    ) -> None:
        self.config = config
        self.repository = repository
        self.builder = builder
        self.collector = collector
        self.llm_runner = llm_runner
        self.simulator = simulator
        self.git_commit_hash = git_commit_hash
        self.config_hash = config_hash
        self.prompt_hash = prompt_hash
        self.output_schema_hash = output_schema_hash
        self.database_schema_hash = database_schema_hash
        self.quant_trend = QuantTrendV1()
        self.quant_mr = QuantMeanReversionV1()
        self.detector = SetupDetectorV1()
        self.hybrid_trend = HybridAgreementStrategy(
            strategy_id="HYBRID_TREND_LLM_V1",
            strategy_version=config.hybrid_trend_version,
        )
        self.hybrid_mr = HybridAgreementStrategy(
            strategy_id="HYBRID_MR_LLM_V1",
            strategy_version=config.hybrid_mr_version,
        )

    def assert_active_manifest(self, manifest: Phase2Manifest) -> None:
        self.config.assert_activation(manifest.authorization_phrase)
        if manifest.phase2_epoch_id != self.config.phase2_epoch_id:
            raise PermissionError("manifest/config epoch mismatch")
        expected = {
            "git_commit_hash": self.git_commit_hash,
            "config_hash": self.config_hash,
            "prompt_hash": self.prompt_hash,
            "output_schema_hash": self.output_schema_hash,
            "database_schema_hash": self.database_schema_hash,
        }
        for field, value in expected.items():
            if getattr(manifest, field) != value:
                raise PermissionError(f"manifest/runtime {field} mismatch")
        if manifest.frozen_contract != config_manifest_fields(self.config):
            raise PermissionError("manifest/runtime frozen-contract mismatch")
        row = self.repository.db.execute(
            "SELECT manifest_hash FROM phase2_manifests WHERE manifest_id = ?",
            (manifest.manifest_id,),
        ).fetchone()
        if row is None or row["manifest_hash"] != manifest.manifest_hash:
            raise PermissionError("immutable activation manifest is not persisted")

    def collect_reconstruct_and_score(
        self, *, boundary: datetime, manifest: Phase2Manifest
    ) -> Phase2CycleResult:
        self.assert_active_manifest(manifest)
        self.collector.collect_incremental(
            end=boundary, observation_class=ObservationClass.SCORED_PROSPECTIVE
        )
        self.collector.recover_gaps(boundary)
        snapshot = self.builder.build(
            boundary,
            observation_class=ObservationClass.SCORED_PROSPECTIVE,
        )
        return self.score_snapshot(snapshot, manifest=manifest)

    def score_snapshot(
        self, snapshot: DecisionSnapshot, *, manifest: Phase2Manifest
    ) -> Phase2CycleResult:
        self.assert_active_manifest(manifest)
        if snapshot.observation_class != ObservationClass.SCORED_PROSPECTIVE:
            raise ValueError(
                "scored Phase 2 pipeline requires prospective observation class"
            )
        snapshot = self.repository.core.save_snapshot(snapshot)
        trend = self.repository.core.save_strategy_decision(
            self.quant_trend.evaluate(snapshot)
        )
        mean_reversion = self.repository.core.save_strategy_decision(
            self.quant_mr.evaluate(snapshot)
        )
        self.repository.core.save_detector_decision(self.detector.evaluate(snapshot))
        llm_record = self.llm_runner.evaluate(snapshot)
        llm = self.repository.core.save_strategy_decision(
            adapt_llm_decision(llm_record, self.config.llm_geometry_adapter_version)
        )
        hybrid_trend = self.repository.core.save_strategy_decision(
            self.hybrid_trend.combine(trend, llm_record)
        )
        hybrid_mr = self.repository.core.save_strategy_decision(
            self.hybrid_mr.combine(mean_reversion, llm_record)
        )
        decisions = (trend, mean_reversion, llm, hybrid_trend, hybrid_mr)
        submitted = [self.simulator.submit(decision) for decision in decisions]
        return Phase2CycleResult(
            snapshot_hash=snapshot.snapshot_hash or "",
            decisions=decisions,
            submitted_trade_count=sum(item is not None for item in submitted),
        )
