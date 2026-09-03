from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from hype_autopilot.config import load_yaml
from hype_autopilot.data.collectors import MarketDataCollector
from hype_autopilot.data.hyperliquid_client import HyperliquidMarketDataClient
from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.config import (
    Phase2Config,
    file_sha256,
    load_phase2_config,
    resolve_inside_workspace,
)
from hype_autopilot.phase2.isolation import connect_phase2
from hype_autopilot.phase2.pipeline import Phase2Pipeline
from hype_autopilot.phase2.provider import (
    openai_provider_from_config,
    output_json_schema,
)
from hype_autopilot.phase2.resources import Phase2ResourceGuard
from hype_autopilot.phase2.runner import FailClosedLLMRunner
from hype_autopilot.phase2.storage import Phase2Repository, phase2_database_schema_hash
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.builder import SnapshotBuilder


@dataclass(frozen=True)
class Phase2Runtime:
    config: Phase2Config
    config_hash: str
    repository: Phase2Repository
    resource_guard: Phase2ResourceGuard
    pipeline: Phase2Pipeline

    def apply_process_isolation(self) -> None:
        self.resource_guard.apply_process_limits()


def _resolve_git_identity(root: Path, supplied: str | None) -> str:
    try:
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        if supplied is None:
            raise RuntimeError(
                "Phase 2 runtime requires a source commit identity"
            ) from None
        return supplied
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError("Phase 2 runtime requires a clean tracked worktree")
    if supplied is not None and supplied != actual:
        raise RuntimeError("supplied Phase 2 source commit does not match the worktree")
    return actual


def build_phase2_runtime(
    *,
    workspace_root: str | Path,
    experiment_id: str,
    config_path: str | Path = "config/phase2/phase2_epoch_001.yaml",
    base_path: str | Path = "config/base.yaml",
    frozen_epoch_path: str | Path = "config/epoch_001.yaml",
    git_commit_hash: str | None = None,
) -> Phase2Runtime:
    root = Path(workspace_root).resolve()
    git_commit_hash = _resolve_git_identity(root, git_commit_hash)
    config_file = resolve_inside_workspace(config_path, root)
    config, digest = load_phase2_config(config_file)
    database_path = resolve_inside_workspace(config.database_path, root)
    db = connect_phase2(database_path, root)
    repository = Phase2Repository(db)
    repository.initialize()

    base = load_yaml(resolve_inside_workspace(base_path, root))
    frozen_epoch = load_yaml(resolve_inside_workspace(frozen_epoch_path, root))
    phase2_epoch = {
        **frozen_epoch,
        "epoch_id": config.phase2_epoch_id,
        "snapshot_schema_version": config.snapshot_schema_version,
        "feature_schema_version": config.feature_schema_version,
        "regime_version": config.regime_version,
        "quant_trend_version": config.quant_trend_version,
        "quant_mean_reversion_version": config.quant_mean_reversion_version,
        "detector_version": config.detector_version,
        "simulator_version": config.simulator_version,
    }
    builder = SnapshotBuilder(repository.core, base, phase2_epoch)
    collector = MarketDataCollector(
        repository.core,
        HyperliquidMarketDataClient(base["hyperliquid"]["base_url"]),
    )
    simulator_config = phase2_epoch["simulator"]
    simulator = PaperSimulator(
        repository.core,
        latency_seconds=simulator_config["signal_to_entry_latency_seconds"],
        fee_bps_per_side=simulator_config["taker_fee_bps_per_side"],
        slippage_bps_per_side=simulator_config["slippage_bps_per_side"],
    )
    resource_guard = Phase2ResourceGuard(config.resource_isolation, database_path)
    provider = openai_provider_from_config(config, workspace_root=str(root))
    prompt_path = resolve_inside_workspace(config.prompt_path, root)
    prompt = prompt_path.read_text(encoding="utf-8")
    llm_runner = FailClosedLLMRunner(
        config=config,
        provider=provider,
        repository=repository,
        prompt=prompt,
        experiment_id=experiment_id,
        resource_guard=resource_guard,
    )
    pipeline = Phase2Pipeline(
        config=config,
        repository=repository,
        builder=builder,
        collector=collector,
        llm_runner=llm_runner,
        simulator=simulator,
        git_commit_hash=git_commit_hash,
        config_hash=digest,
        prompt_hash=file_sha256(prompt_path),
        output_schema_hash=sha256_canonical(
            output_json_schema(config.output_schema_version)
        ),
        database_schema_hash=phase2_database_schema_hash(),
    )
    return Phase2Runtime(
        config=config,
        config_hash=digest,
        repository=repository,
        resource_guard=resource_guard,
        pipeline=pipeline,
    )
