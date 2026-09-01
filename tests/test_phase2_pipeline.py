from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import copyfile

import pytest

from hype_autopilot.data.models import ObservationClass
from hype_autopilot.phase2.config import ACTIVATION_PHRASE, load_phase2_config
from hype_autopilot.phase2.manifest import build_activation_manifest
from hype_autopilot.phase2.pipeline import Phase2Pipeline
from hype_autopilot.phase2.runner import FailClosedLLMRunner
from hype_autopilot.phase2.runtime import build_phase2_runtime
from hype_autopilot.phase2.storage import Phase2Repository, phase2_database_schema_hash
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from tests.test_phase2_contract_runner import (
    FakeProvider,
    phase2_snapshot,
    valid_output,
)


def test_build_config_cannot_enter_scored_pipeline():
    config, digest = load_phase2_config("config/phase2/phase2_epoch_001.yaml")
    pipeline = object.__new__(Phase2Pipeline)
    pipeline.config = config
    pipeline.repository = None
    enabled = config.model_copy(
        update={"evidence_collection_enabled": True, "activation_authorized": True}
    )
    manifest = build_activation_manifest(
        config=enabled,
        experiment_id="synthetic-pipeline",
        activation_timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        authorization=ACTIVATION_PHRASE,
        git_commit_hash="a" * 40,
        config_hash=digest,
        prompt_hash="b" * 64,
        output_schema_hash="c" * 64,
        database_schema_hash=phase2_database_schema_hash(),
    )
    with pytest.raises(PermissionError):
        pipeline.assert_active_manifest(manifest)


def test_authorized_pipeline_fixture_scores_all_five_strategies_in_memory_only():
    base_config, digest = load_phase2_config("config/phase2/phase2_epoch_001.yaml")
    config = base_config.model_copy(
        update={"evidence_collection_enabled": True, "activation_authorized": True}
    )
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    repository = Phase2Repository(db)
    repository.initialize()
    manifest = build_activation_manifest(
        config=config,
        experiment_id="synthetic-pipeline",
        activation_timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        authorization=ACTIVATION_PHRASE,
        git_commit_hash="a" * 40,
        config_hash=digest,
        prompt_hash="b" * 64,
        output_schema_hash="c" * 64,
        database_schema_hash=phase2_database_schema_hash(),
    )
    repository.save_manifest(manifest)
    source = phase2_snapshot()
    snapshot = freeze_snapshot(
        source.model_copy(
            update={
                "snapshot_id": "synthetic-prospective-fixture",
                "observation_class": ObservationClass.SCORED_PROSPECTIVE,
                "snapshot_hash": None,
            }
        )
    )
    provider = FakeProvider(
        [valid_output(snapshot.snapshot_hash)], snapshot.snapshot_timestamp
    )
    llm_runner = FailClosedLLMRunner(
        config=config,
        provider=provider,
        repository=repository,
        prompt="snapshot only",
        experiment_id="synthetic-pipeline",
        clock=lambda: snapshot.snapshot_timestamp + timedelta(seconds=10),
    )
    pipeline = Phase2Pipeline(
        config=config,
        repository=repository,
        builder=object(),
        collector=object(),
        llm_runner=llm_runner,
        simulator=PaperSimulator(repository.core),
        git_commit_hash="a" * 40,
        config_hash=digest,
        prompt_hash="b" * 64,
        output_schema_hash="c" * 64,
        database_schema_hash=phase2_database_schema_hash(),
    )
    result = pipeline.score_snapshot(snapshot, manifest=manifest)
    assert len(result.decisions) == 5
    assert {item.strategy_id for item in result.decisions} == {
        "QUANT_TREND",
        "QUANT_MR",
        "LLM_V1",
        "HYBRID_TREND_LLM_V1",
        "HYBRID_MR_LLM_V1",
    }
    assert result.submitted_trade_count == 1
    assert db.execute("SELECT COUNT(*) FROM detector_decisions").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM llm_decisions").fetchone()[0] == 1
    assert repository.integrity() == ("ok", 0)


def test_runtime_factory_wires_isolated_database_without_network_or_scoring(
    tmp_path, monkeypatch
):
    for relative in (
        "config/base.yaml",
        "config/epoch_001.yaml",
        "config/phase2/phase2_epoch_001.yaml",
        "prompts/phase2/llm_v1.txt",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(relative, target)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    runtime = build_phase2_runtime(
        workspace_root=tmp_path,
        experiment_id="synthetic-runtime-build",
        git_commit_hash="a" * 40,
    )
    runtime.config.assert_build_only()
    database = runtime.repository.db.execute("PRAGMA database_list").fetchone()["file"]
    assert Path(database).is_relative_to(tmp_path)
    assert (
        runtime.repository.db.execute(
            "SELECT COUNT(*) FROM phase2_manifests"
        ).fetchone()[0]
        == 0
    )
    assert (
        runtime.repository.db.execute("SELECT COUNT(*) FROM llm_decisions").fetchone()[
            0
        ]
        == 0
    )
