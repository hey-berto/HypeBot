from __future__ import annotations

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
