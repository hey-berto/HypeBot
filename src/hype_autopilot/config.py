from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from hype_autopilot.hashing import canonical_json


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"configuration must be a mapping: {path}")
    return value


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


def validate_epoch_config(config: dict[str, Any]) -> None:
    required = {
        "epoch_id", "snapshot_schema_version", "feature_schema_version",
        "regime_version", "quant_trend_version", "quant_mean_reversion_version",
        "detector_version", "simulator_version", "regime", "funding",
        "quant_trend", "quant_mean_reversion", "simulator",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"incomplete epoch config; missing: {', '.join(missing)}")

