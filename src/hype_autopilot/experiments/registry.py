from __future__ import annotations

import sqlite3
import subprocess
from datetime import UTC, datetime
from typing import Any

from hype_autopilot.config import config_hash, validate_epoch_config
from hype_autopilot.hashing import canonical_json


def _git_hash() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def register_epoch_configuration(db: sqlite3.Connection, config: dict[str, Any]) -> str:
    validate_epoch_config(config)
    digest = config_hash(config)
    db.execute(
        "INSERT OR IGNORE INTO epoch_configurations "
        "(epoch_id, config_hash, snapshot_schema_version, feature_schema_version, quant_trend_version, "
        "quant_mean_reversion_version, detector_version, regime_version, simulator_version, "
        "git_commit_hash, config_json, registered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (config["epoch_id"], digest, config["snapshot_schema_version"], config["feature_schema_version"],
         config["quant_trend_version"], config["quant_mean_reversion_version"],
         config["detector_version"], config["regime_version"], config["simulator_version"],
         _git_hash(), canonical_json(config), datetime.now(UTC).isoformat()),
    )
    db.commit()
    return digest


def start_epoch(db: sqlite3.Connection, config: dict[str, Any]) -> str:
    validate_epoch_config(config)
    if config.get("status") not in {"DRAFT", "READY"}:
        raise ValueError("only a DRAFT or READY epoch can be started")
    digest = config_hash(config)
    now = datetime.now(UTC).isoformat()
    git_hash = _git_hash()
    db.execute(
        "INSERT INTO epochs(epoch_id, started_at, ended_at, status, snapshot_schema_version, "
        "feature_schema_version, quant_trend_version, quant_mean_reversion_version, detector_version, "
        "regime_version, simulator_version, config_hash, git_commit_hash, notes, config_json, created_at) "
        "VALUES (?, ?, NULL, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (config["epoch_id"], now, config["snapshot_schema_version"], config["feature_schema_version"],
         config["quant_trend_version"], config["quant_mean_reversion_version"],
         config["detector_version"], config["regime_version"], config["simulator_version"],
         digest, git_hash, config.get("notes"), canonical_json(config), now),
    )
    db.commit()
    return digest
