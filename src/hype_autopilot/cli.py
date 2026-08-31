from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from hype_autopilot.clock import floor_quarter_hour
from hype_autopilot.config import config_hash, load_yaml, validate_epoch_config
from hype_autopilot.data.collectors import MarketDataCollector, ResilientWebsocketCollector
from hype_autopilot.data.hyperliquid_client import HyperliquidMarketDataClient
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.data.repository import Repository
from hype_autopilot.diagnostics import detector_proxy_report, operational_status, trace_trade
from hype_autopilot.experiments.registry import register_epoch_configuration, start_epoch
from hype_autopilot.hashing import canonical_json
from hype_autopilot.logging import configure_logging
from hype_autopilot.operations import CycleRunner, schedule_forever
from hype_autopilot.simulation.engine import PaperSimulator
from hype_autopilot.snapshots.builder import SnapshotBuilder
from hype_autopilot.storage.db import connect


def _db_path(args: argparse.Namespace) -> str:
    return args.db or os.getenv("HYPE_AUTOPILOT_DB", "data/hype_autopilot.sqlite3")


def _parse_time(value: str | None) -> datetime:
    if value is None:
        return floor_quarter_hour(datetime.now(UTC))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _components(args: argparse.Namespace):
    base = load_yaml(args.config)
    epoch = load_yaml(args.epoch)
    db = connect(_db_path(args))
    repo = Repository(db)
    repo.initialize()
    register_epoch_configuration(db, epoch)
    builder = SnapshotBuilder(repo, base, epoch)
    simulator_config = epoch["simulator"]
    simulator = PaperSimulator(
        repo, latency_seconds=simulator_config["signal_to_entry_latency_seconds"],
        fee_bps_per_side=simulator_config["taker_fee_bps_per_side"],
        slippage_bps_per_side=simulator_config["slippage_bps_per_side"],
    )
    runner = CycleRunner(repo, builder, simulator)
    return base, epoch, repo, runner


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="hype-autopilot")
    parser.add_argument("--db")
    parser.add_argument("--config", default=os.getenv("HYPE_AUTOPILOT_CONFIG", "config/base.yaml"))
    parser.add_argument("--epoch", default=os.getenv("HYPE_AUTOPILOT_EPOCH", "config/epoch_001.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("show-epoch")
    sub.add_parser("start-epoch")
    collect = sub.add_parser("collect")
    collect.add_argument("--warmup", action="store_true")
    build = sub.add_parser("build-snapshot")
    build.add_argument("--at")
    build.add_argument("--observation-class", choices=["WARMUP", "SOAK"], default="SOAK")
    cycle = sub.add_parser("run-cycle")
    cycle.add_argument("--at")
    cycle.add_argument("--collect", action="store_true")
    sub.add_parser("schedule")
    sub.add_parser("status")
    sub.add_parser("detector-report")
    trace = sub.add_parser("trace-trade")
    trace.add_argument("paper_trade_id")
    sub.add_parser("validate-db")
    args = parser.parse_args(argv)

    base, epoch, repo, runner = _components(args)
    if args.command == "show-epoch":
        validate_epoch_config(epoch)
        print(canonical_json(epoch))
        print(f"config_hash={config_hash(epoch)}")
    elif args.command == "init-db":
        print(f"initialized {_db_path(args)}")
    elif args.command == "start-epoch":
        digest = start_epoch(repo.db, epoch)
        print(f"started epoch config_hash={digest}")
    elif args.command == "collect":
        collector = MarketDataCollector(repo, HyperliquidMarketDataClient(base["hyperliquid"]["base_url"]))
        result = collector.warmup() if args.warmup else collector.collect_incremental()
        if not args.warmup:
            result["recovered_gaps"] = sum(collector.recover_gaps().values())
        print(json.dumps(result, indent=2))
    elif args.command == "build-snapshot":
        snapshot = runner.builder.build(
            _parse_time(args.at), observation_class=ObservationClass(args.observation_class)
        )
        snapshot = repo.save_snapshot(snapshot)
        print(json.dumps({"snapshot_hash": snapshot.snapshot_hash,
                          "scoreable": snapshot.data_quality.scoreable,
                          "rejection_reasons": snapshot.data_quality.rejection_reasons}, indent=2))
    elif args.command == "run-cycle":
        at = _parse_time(args.at)
        if args.collect:
            collector = MarketDataCollector(repo, HyperliquidMarketDataClient(base["hyperliquid"]["base_url"]))
            collector.collect_incremental(end=datetime.now(UTC), observation_class=runner.observation_class())
            collector.recover_gaps(at)
        snapshot = runner.run(at)
        print(json.dumps({"snapshot_hash": snapshot.snapshot_hash,
                          "scoreable": snapshot.data_quality.scoreable,
                          "observation_class": snapshot.observation_class.value}, indent=2))
    elif args.command == "schedule":
        collector = MarketDataCollector(repo, HyperliquidMarketDataClient(base["hyperliquid"]["base_url"]))
        websocket = ResilientWebsocketCollector(repo, collector, base["hyperliquid"]["base_url"])
        try:
            asyncio.run(schedule_forever(runner, collector, websocket))
        except KeyboardInterrupt:
            print("scheduler stopped")
    elif args.command == "status":
        print(json.dumps({"db": str(Path(_db_path(args)).resolve()), **operational_status(repo)}, indent=2))
    elif args.command == "detector-report":
        print(json.dumps(detector_proxy_report(repo), indent=2))
    elif args.command == "trace-trade":
        print(json.dumps(trace_trade(repo, args.paper_trade_id), indent=2))
    elif args.command == "validate-db":
        integrity = repo.db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = repo.db.execute("PRAGMA foreign_key_check").fetchall()
        duplicates = repo.db.execute(
            "SELECT COUNT(*) FROM (SELECT snapshot_hash, strategy_id, strategy_version, COUNT(*) n "
            "FROM strategy_decisions GROUP BY 1,2,3 HAVING n > 1)"
        ).fetchone()[0]
        result = {"integrity": integrity, "foreign_key_errors": len(foreign_keys),
                  "duplicate_strategy_scores": duplicates}
        print(json.dumps(result))
        return 0 if integrity == "ok" and not foreign_keys and not duplicates else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
