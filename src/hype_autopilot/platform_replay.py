from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from hype_autopilot.config import config_hash, load_yaml
from hype_autopilot.data.models import (
    AssetContext,
    BboObservation,
    Candle,
    FundingObservation,
    ObservationClass,
)
from hype_autopilot.data.repository import Repository
from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.snapshots.canonicalize import snapshot_payload
from hype_autopilot.strategies.quant_mean_reversion_v1 import QuantMeanReversionV1
from hype_autopilot.strategies.quant_trend_v1 import QuantTrendV1
from hype_autopilot.strategies.setup_detector_v1 import SetupDetectorV1

HISTORICAL_REPLAY_VERSION = "MAC_UBUNTU_HISTORICAL_HYPE_REPLAY_V1"


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("fixture timestamp must be UTC")
    return parsed.astimezone(UTC)


def _fixture_candles(
    source: dict[str, Any],
    *,
    snapshot_at: datetime,
    modulus: int,
    linear_step: float,
    wave_step: float,
) -> list[Candle]:
    step = timedelta(minutes=int(source["minutes"]))
    count = int(source["count"])
    start = snapshot_at - count * step
    rows = []
    for index in range(count):
        open_time = start + index * step
        close_time = open_time + step
        price = (
            float(source["base"]) + index * linear_step + (index % modulus) * wave_step
        )
        rows.append(
            Candle(
                symbol=str(source["symbol"]),
                interval=str(source["interval"]),
                open_time=open_time,
                close_time=close_time,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.1,
                volume=1000 + index,
                trade_count=10,
                received_at=close_time,
                observation_class=ObservationClass.WARMUP,
            )
        )
    return rows


def run_platform_replay(
    root: str | Path,
    fixture_path: str | Path,
    *,
    base_config_path: str | Path = "config/base.yaml",
    epoch_config_path: str | Path = "config/epoch_001.yaml",
) -> dict[str, Any]:
    project_root = Path(root).resolve()
    fixture_file = Path(fixture_path)
    if not fixture_file.is_absolute():
        fixture_file = project_root / fixture_file
    fixture = yaml.safe_load(fixture_file.read_text(encoding="utf-8"))
    if fixture.get("fixture_version") != "MAC_UBUNTU_PLATFORM_REPLAY_V1":
        raise ValueError("unexpected platform replay fixture version")
    snapshot_at = _parse_utc(str(fixture["snapshot_at"]))
    base_file = Path(base_config_path)
    if not base_file.is_absolute():
        base_file = project_root / base_file
    epoch_file = Path(epoch_config_path)
    if not epoch_file.is_absolute():
        epoch_file = project_root / epoch_file
    base = load_yaml(base_file)
    epoch = load_yaml(epoch_file)

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    repository = Repository(db)
    repository.initialize()
    raw_rows: list[dict[str, Any]] = []
    for source in fixture["sources"]:
        candles = _fixture_candles(
            source,
            snapshot_at=snapshot_at,
            modulus=int(fixture["candle_wave_modulus"]),
            linear_step=float(fixture["candle_linear_step"]),
            wave_step=float(fixture["candle_wave_step"]),
        )
        raw_rows.extend(row.model_dump(mode="python") for row in candles)
        repository.save_candles(candles)
    contexts = []
    for index in range(int(fixture["asset_context_count"])):
        at = snapshot_at - timedelta(
            minutes=15 * (int(fixture["asset_context_count"]) - 1 - index)
        )
        contexts.append(
            AssetContext(
                symbol="HYPE",
                source_timestamp=at,
                received_at=at,
                mark_price=120 + index * 0.01,
                mid_price=120 + index * 0.01,
                oracle_price=120,
                funding_rate=0.0001,
                open_interest=1_000_000 + index * 1000,
                day_notional_volume=50_000_000,
            )
        )
    for row in contexts:
        repository.save_asset_context(row)
    raw_rows.extend(row.model_dump(mode="python") for row in contexts)
    bbo = BboObservation(
        symbol="HYPE",
        source_timestamp=snapshot_at,
        received_at=snapshot_at,
        bid_price=120.0,
        bid_size=10,
        ask_price=120.01,
        ask_size=9,
    )
    repository.save_bbo(bbo)
    raw_rows.append(bbo.model_dump(mode="python"))
    funding = [
        FundingObservation(
            symbol="HYPE",
            source_timestamp=snapshot_at
            - timedelta(hours=int(fixture["funding_count"]) - 1 - index),
            received_at=snapshot_at
            - timedelta(hours=int(fixture["funding_count"]) - 1 - index),
            funding_rate=0.0001 + index * 1e-7,
            observation_class=ObservationClass.WARMUP,
        )
        for index in range(int(fixture["funding_count"]))
    ]
    repository.save_funding(funding)
    raw_rows.extend(row.model_dump(mode="python") for row in funding)

    snapshot = SnapshotBuilder(repository, base, epoch).build(
        snapshot_at,
        available_at=snapshot_at + timedelta(seconds=1),
        observation_class=ObservationClass(str(fixture["observation_class"])),
    )
    trend = QuantTrendV1().evaluate(snapshot)
    mean_reversion = QuantMeanReversionV1().evaluate(snapshot)
    detector = SetupDetectorV1().evaluate(snapshot)
    snapshot_json = snapshot_payload(snapshot)
    payload = {
        "replay_version": "MAC_UBUNTU_PLATFORM_REPLAY_V1",
        "fixture_hash": sha256_canonical(fixture),
        "raw_input_hash": sha256_canonical(raw_rows),
        "base_config_hash": config_hash(base),
        "epoch_config_hash": config_hash(epoch),
        "normalized_hype_features": snapshot.market.hype_features,
        "normalized_btc_features": snapshot.market.btc_features,
        "regime": snapshot.regime,
        "quant_decisions": (trend, mean_reversion),
        "detector_output": detector,
        "canonical_snapshot_json": snapshot_json,
        "snapshot_hash": snapshot.snapshot_hash,
    }
    return {**payload, "replay_hash": sha256_canonical(payload)}


def platform_replay_json(*args: Any, **kwargs: Any) -> str:
    return canonical_json(run_platform_replay(*args, **kwargs))


def _readonly_connection(database_path: str | Path) -> sqlite3.Connection:
    target = Path(database_path).resolve(strict=True)
    db = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _phase2_snapshot_config(
    project_root: Path,
    phase2_config_path: str | Path,
    frozen_epoch_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from hype_autopilot.phase2.config import load_phase2_config

    phase2_file = Path(phase2_config_path)
    if not phase2_file.is_absolute():
        phase2_file = project_root / phase2_file
    frozen_file = Path(frozen_epoch_path)
    if not frozen_file.is_absolute():
        frozen_file = project_root / frozen_file
    phase2, phase2_hash = load_phase2_config(phase2_file)
    frozen = load_yaml(frozen_file)
    snapshot_config = {
        **frozen,
        "epoch_id": phase2.phase2_epoch_id,
        "snapshot_schema_version": phase2.snapshot_schema_version,
        "feature_schema_version": phase2.feature_schema_version,
        "regime_version": phase2.regime_version,
        "quant_trend_version": phase2.quant_trend_version,
        "quant_mean_reversion_version": phase2.quant_mean_reversion_version,
        "detector_version": phase2.detector_version,
        "simulator_version": phase2.simulator_version,
    }
    return snapshot_config, {"phase2_config_hash": phase2_hash}


def export_historical_hype_fixture(
    root: str | Path,
    database_path: str | Path,
    boundary: datetime,
    *,
    phase2_config_path: str | Path = "config/phase2/phase2_epoch_002.yaml",
    base_config_path: str | Path = "config/base.yaml",
    frozen_epoch_path: str | Path = "config/epoch_001.yaml",
) -> dict[str, Any]:
    """Read one completed historical boundary into a portable raw-data fixture.

    The source connection is query-only. The returned fixture contains public
    market observations and immutable lineage only; it contains no LLM response,
    strategy outcome, trade, order, fill, performance, or credential material.
    """
    project_root = Path(root).resolve()
    boundary = boundary.astimezone(UTC).replace(second=0, microsecond=0)
    if boundary.minute % 15:
        raise ValueError("historical replay boundary must be UTC quarter-hour aligned")
    base_file = Path(base_config_path)
    if not base_file.is_absolute():
        base_file = project_root / base_file
    base = load_yaml(base_file)
    snapshot_config, phase2_identity = _phase2_snapshot_config(
        project_root, phase2_config_path, frozen_epoch_path
    )
    db = _readonly_connection(database_path)
    try:
        db.execute("BEGIN")
        cycle = db.execute(
            "SELECT started_at,completed_at,status,snapshot_hash FROM research_cycles "
            "WHERE scheduled_at=? AND observation_class='SCORED_PROSPECTIVE'",
            (boundary.isoformat(),),
        ).fetchone()
        if cycle is None or cycle["status"] != "COMPLETE" or not cycle["snapshot_hash"]:
            raise ValueError("historical boundary is not a completed scored snapshot")
        stored = db.execute(
            "SELECT canonical_json,epoch_id,observation_class FROM decision_snapshots "
            "WHERE snapshot_hash=?",
            (cycle["snapshot_hash"],),
        ).fetchone()
        if stored is None or stored["epoch_id"] != snapshot_config["epoch_id"]:
            raise ValueError("historical snapshot identity differs from replay config")
        available_at = datetime.fromisoformat(cycle["completed_at"]).astimezone(UTC)
        repository = Repository(db)
        symbols = base["symbols"]
        candles: list[Candle] = []
        for symbol, intervals in (
            (symbols["hype"], ("1m", "5m", "15m", "1h", "4h")),
            (symbols["btc"], ("15m", "1h", "4h")),
        ):
            for interval in intervals:
                candles.extend(
                    repository.candles(
                        symbol, interval, boundary, available_at=available_at
                    )
                )
        funding = repository.funding(
            symbols["hype"], boundary, available_at=available_at
        )
        contexts = repository.asset_contexts(
            symbols["hype"], boundary, available_at=available_at
        )
        bbo = repository.latest_bbo(
            symbols["hype"], boundary, available_at=available_at
        )
        raw_payload = {
            "candles": [item.model_dump(mode="json") for item in candles],
            "funding": [item.model_dump(mode="json") for item in funding],
            "asset_contexts": [item.model_dump(mode="json") for item in contexts],
            "bbo": bbo.model_dump(mode="json") if bbo else None,
        }
        fixture_core = {
            "fixture_version": HISTORICAL_REPLAY_VERSION,
            "snapshot_at": boundary.isoformat(),
            "available_at": available_at.isoformat(),
            "observation_class": stored["observation_class"],
            "source_snapshot_hash": cycle["snapshot_hash"],
            "source_snapshot_canonical_sha256": hashlib.sha256(
                stored["canonical_json"].encode("utf-8")
            ).hexdigest(),
            "base_config_hash": config_hash(base),
            **phase2_identity,
            "raw": raw_payload,
        }
        return {
            **fixture_core,
            "raw_input_hash": sha256_canonical(raw_payload),
            "fixture_hash": sha256_canonical(fixture_core),
        }
    finally:
        db.close()


def run_historical_hype_replay(
    root: str | Path,
    fixture_path: str | Path,
    *,
    phase2_config_path: str | Path = "config/phase2/phase2_epoch_002.yaml",
    base_config_path: str | Path = "config/base.yaml",
    frozen_epoch_path: str | Path = "config/epoch_001.yaml",
) -> dict[str, Any]:
    """Recompute platform-invariant deterministic outputs from historical raw data."""
    project_root = Path(root).resolve()
    fixture_file = Path(fixture_path)
    if not fixture_file.is_absolute():
        fixture_file = project_root / fixture_file
    fixture = json.loads(fixture_file.read_text(encoding="utf-8"))
    if fixture.get("fixture_version") != HISTORICAL_REPLAY_VERSION:
        raise ValueError("unexpected historical replay fixture version")
    supplied_fixture_hash = fixture.pop("fixture_hash")
    supplied_raw_hash = fixture["raw_input_hash"]
    fixture_core = {
        key: value for key, value in fixture.items() if key != "raw_input_hash"
    }
    if sha256_canonical(fixture_core) != supplied_fixture_hash:
        raise ValueError("historical replay fixture hash mismatch")
    if sha256_canonical(fixture["raw"]) != supplied_raw_hash:
        raise ValueError("historical replay raw-input hash mismatch")

    base_file = Path(base_config_path)
    if not base_file.is_absolute():
        base_file = project_root / base_file
    base = load_yaml(base_file)
    snapshot_config, phase2_identity = _phase2_snapshot_config(
        project_root, phase2_config_path, frozen_epoch_path
    )
    if config_hash(base) != fixture["base_config_hash"]:
        raise ValueError("historical replay base-config identity mismatch")
    if phase2_identity["phase2_config_hash"] != fixture["phase2_config_hash"]:
        raise ValueError("historical replay Phase 2 config identity mismatch")

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    repository = Repository(db)
    repository.initialize()
    raw = fixture["raw"]
    repository.save_candles(Candle.model_validate(item) for item in raw["candles"])
    repository.save_funding(
        FundingObservation.model_validate(item) for item in raw["funding"]
    )
    for item in raw["asset_contexts"]:
        repository.save_asset_context(AssetContext.model_validate(item))
    if raw["bbo"]:
        repository.save_bbo(BboObservation.model_validate(raw["bbo"]))
    snapshot_at = _parse_utc(fixture["snapshot_at"])
    available_at = _parse_utc(fixture["available_at"])
    snapshot = SnapshotBuilder(repository, base, snapshot_config).build(
        snapshot_at,
        available_at=available_at,
        observation_class=ObservationClass(fixture["observation_class"]),
    )
    trend = QuantTrendV1().evaluate(snapshot)
    mean_reversion = QuantMeanReversionV1().evaluate(snapshot)
    detector = SetupDetectorV1().evaluate(snapshot)
    snapshot_json = snapshot_payload(snapshot)
    deterministic = {
        "replay_version": HISTORICAL_REPLAY_VERSION,
        "fixture_hash": supplied_fixture_hash,
        "raw_input_hash": supplied_raw_hash,
        "source_snapshot_hash": fixture["source_snapshot_hash"],
        "source_snapshot_canonical_sha256": fixture["source_snapshot_canonical_sha256"],
        "base_config_hash": fixture["base_config_hash"],
        "phase2_config_hash": fixture["phase2_config_hash"],
        "snapshot_at": fixture["snapshot_at"],
        "source_counts": {
            "candles": len(raw["candles"]),
            "funding": len(raw["funding"]),
            "asset_contexts": len(raw["asset_contexts"]),
            "bbo": int(raw["bbo"] is not None),
        },
        "normalized_hype_features": snapshot.market.hype_features,
        "normalized_btc_features": snapshot.market.btc_features,
        "microstructure": snapshot.market.microstructure,
        "regime": snapshot.regime,
        "data_quality": snapshot.data_quality,
        "source_cutoffs": snapshot.source_cutoffs,
        "quant_decisions": (trend, mean_reversion),
        "detector_output": detector,
        "canonical_snapshot_json": snapshot_json,
        "snapshot_hash": snapshot.snapshot_hash,
        "matches_source_snapshot_hash": snapshot.snapshot_hash
        == fixture["source_snapshot_hash"],
    }
    return {**deterministic, "replay_hash": sha256_canonical(deterministic)}


def historical_hype_replay_json(*args: Any, **kwargs: Any) -> str:
    return canonical_json(run_historical_hype_replay(*args, **kwargs))
