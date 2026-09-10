#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from hype_autopilot.data.collectors import (
    MarketDataCollector,
    ResilientWebsocketCollector,
)
from hype_autopilot.data.hyperliquid_client import HyperliquidMarketDataClient
from hype_autopilot.data.repository import Repository
from hype_autopilot.storage.db import connect

AUDIT_CLASS = "NON_SCORED_UBUNTU_PUBLIC_MARKET_DATA_ACCEPTANCE"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_destination(path: Path) -> Path:
    target = path.resolve()
    if target.suffix != ".sqlite3" or not target.name.startswith("NON_SCORED_"):
        raise ValueError("acceptance database must be a NON_SCORED_*.sqlite3 file")
    if "phase2_epoch_002" in target.name:
        raise ValueError("production Phase 2 database identity is forbidden")
    return target


def run(database: Path, timeout_seconds: float) -> dict[str, object]:
    target = validate_destination(database)
    if target.exists():
        raise FileExistsError("use a fresh non-scored acceptance database")
    target.parent.mkdir(parents=True, exist_ok=True)
    db = connect(target)
    repository = Repository(db)
    repository.initialize()
    rest = MarketDataCollector(
        repository, HyperliquidMarketDataClient("https://api.hyperliquid.xyz")
    )
    stop = threading.Event()
    collector = ResilientWebsocketCollector(repository, rest, poll_seconds=0.25)
    thread = threading.Thread(target=collector.run_forever, args=(stop,), daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout_seconds
    connected = False
    observations = 0
    candles = 0
    while time.monotonic() < deadline:
        connected = bool(
            db.execute(
                "SELECT 1 FROM health_events WHERE component='websocket' "
                "AND status='CONNECTED' LIMIT 1"
            ).fetchone()
        )
        observations = db.execute(
            "SELECT COUNT(*) FROM raw_market_observations"
        ).fetchone()[0]
        candles = db.execute("SELECT COUNT(*) FROM raw_candles").fetchone()[0]
        if connected and (observations or candles):
            break
        time.sleep(0.25)
    stop.set()
    thread.join(timeout=10)
    if thread.is_alive():
        raise RuntimeError("websocket collector did not stop cleanly")
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())
    statuses = [
        row[0]
        for row in db.execute(
            "SELECT status FROM health_events WHERE component='websocket' ORDER BY id"
        )
    ]
    db.close()
    result = {
        "audit_class": AUDIT_CLASS,
        "observed_at": datetime.now(UTC).isoformat(),
        "database": str(target),
        "database_sha256": file_sha256(target),
        "websocket_connected": connected,
        "websocket_health_statuses": statuses,
        "raw_market_observations": observations,
        "raw_candles": candles,
        "database_integrity": integrity,
        "foreign_key_violations": foreign_keys,
        "provider_calls": 0,
        "tool_calls": 0,
        "scored_evidence": 0,
        "live_trading_capability": False,
    }
    result["passed"] = bool(
        connected
        and (observations or candles)
        and integrity == "ok"
        and foreign_keys == 0
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.database, args.timeout_seconds)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
