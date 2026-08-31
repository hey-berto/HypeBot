from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, Iterable

from hype_autopilot.data.models import AssetContext, BboObservation, Candle, FundingObservation
from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.simulation.models import PaperTrade
from hype_autopilot.snapshots.canonicalize import freeze_snapshot, snapshot_payload
from hype_autopilot.snapshots.models import DecisionSnapshot
from hype_autopilot.storage.schema import SCHEMA
from hype_autopilot.strategies.base import StrategyDecision
from hype_autopilot.strategies.setup_detector_v1 import DetectorDecision


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class Repository:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def initialize(self) -> None:
        self.db.executescript(SCHEMA)
        self.db.commit()

    def save_candles(self, candles: Iterable[Candle]) -> int:
        inserted = 0
        for candle in candles:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO raw_candles "
                "(symbol, interval, open_time, close_time, open, high, low, close, volume, trade_count, "
                "received_at, observation_class, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (candle.symbol, candle.interval, _iso(candle.open_time), _iso(candle.close_time),
                 candle.open, candle.high, candle.low, candle.close, candle.volume, candle.trade_count,
                 _iso(candle.received_at), candle.observation_class.value,
                 sha256_canonical(candle.model_dump(mode="python", exclude={"received_at", "observation_class"}))),
            )
            inserted += cursor.rowcount
        self.db.commit()
        return inserted

    def candles(self, symbol: str, interval: str, cutoff: datetime, *,
                available_at: datetime | None = None, start: datetime | None = None) -> list[Candle]:
        available_at = available_at or datetime.now(UTC)
        params: list[Any] = [symbol, interval, _iso(cutoff), _iso(available_at)]
        start_sql = ""
        if start is not None:
            start_sql = " AND open_time >= ?"
            params.append(_iso(start))
        rows = self.db.execute(
            "WITH ranked AS (SELECT *, ROW_NUMBER() OVER "
            "(PARTITION BY symbol, interval, open_time ORDER BY received_at DESC, id DESC) AS rn "
            "FROM raw_candles WHERE symbol = ? AND interval = ? AND close_time <= ? AND received_at <= ?"
            f"{start_sql}) SELECT * FROM ranked WHERE rn = 1 ORDER BY close_time", params,
        ).fetchall()
        return [Candle(
            symbol=row["symbol"], interval=row["interval"], open_time=_dt(row["open_time"]),
            close_time=_dt(row["close_time"]), open=row["open"], high=row["high"], low=row["low"],
            close=row["close"], volume=row["volume"], trade_count=row["trade_count"],
            received_at=_dt(row["received_at"]), observation_class=row["observation_class"],
        ) for row in rows]

    def latest_candle_close(self, symbol: str, interval: str) -> datetime | None:
        row = self.db.execute(
            "SELECT MAX(close_time) AS close_time FROM raw_candles WHERE symbol = ? AND interval = ?",
            (symbol, interval),
        ).fetchone()
        return _dt(row["close_time"]) if row and row["close_time"] else None

    def save_asset_context(self, context: AssetContext) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO raw_market_observations "
            "(symbol, source_timestamp, received_at, payload_json, content_hash) VALUES (?, ?, ?, ?, ?)",
            (context.symbol, _iso(context.source_timestamp), _iso(context.received_at),
             canonical_json(context),
             sha256_canonical(context.model_dump(mode="python", exclude={"received_at"}))),
        )
        self.db.commit()
        return bool(cursor.rowcount)

    def latest_asset_context(self, symbol: str, cutoff: datetime, *,
                             available_at: datetime | None = None) -> AssetContext | None:
        row = self.db.execute(
            "SELECT payload_json FROM raw_market_observations WHERE symbol = ? "
            "AND source_timestamp <= ? AND received_at <= ? ORDER BY source_timestamp DESC, id DESC LIMIT 1",
            (symbol, _iso(cutoff), _iso(available_at or datetime.now(UTC))),
        ).fetchone()
        return AssetContext.model_validate(json.loads(row["payload_json"])) if row else None

    def asset_contexts(self, symbol: str, cutoff: datetime, *,
                       available_at: datetime | None = None,
                       start: datetime | None = None) -> list[AssetContext]:
        params: list[Any] = [symbol, _iso(cutoff), _iso(available_at or datetime.now(UTC))]
        start_sql = ""
        if start:
            start_sql = " AND source_timestamp >= ?"
            params.append(_iso(start))
        rows = self.db.execute(
            "SELECT payload_json FROM raw_market_observations WHERE symbol = ? "
            "AND source_timestamp <= ? AND received_at <= ?" + start_sql +
            " ORDER BY source_timestamp, id", params,
        ).fetchall()
        return [AssetContext.model_validate(json.loads(row["payload_json"])) for row in rows]

    def save_bbo(self, bbo: BboObservation) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO raw_bbo_observations "
            "(symbol, source_timestamp, received_at, payload_json, content_hash) VALUES (?, ?, ?, ?, ?)",
            (bbo.symbol, _iso(bbo.source_timestamp), _iso(bbo.received_at),
             canonical_json(bbo), sha256_canonical(bbo.model_dump(mode="python", exclude={"received_at"}))),
        )
        self.db.commit()
        return bool(cursor.rowcount)

    def latest_bbo(self, symbol: str, cutoff: datetime, *,
                   available_at: datetime | None = None) -> BboObservation | None:
        row = self.db.execute(
            "SELECT payload_json FROM raw_bbo_observations WHERE symbol = ? "
            "AND source_timestamp <= ? AND received_at <= ? ORDER BY source_timestamp DESC, id DESC LIMIT 1",
            (symbol, _iso(cutoff), _iso(available_at or datetime.now(UTC))),
        ).fetchone()
        return BboObservation.model_validate(json.loads(row["payload_json"])) if row else None

    def save_funding(self, observations: Iterable[FundingObservation]) -> int:
        inserted = 0
        for item in observations:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO raw_funding_observations "
                "(symbol, source_timestamp, received_at, funding_rate, premium, observation_class, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.symbol, _iso(item.source_timestamp), _iso(item.received_at), item.funding_rate,
                 item.premium, item.observation_class.value,
                 sha256_canonical(item.model_dump(mode="python", exclude={"received_at", "observation_class"}))),
            )
            inserted += cursor.rowcount
        self.db.commit()
        return inserted

    def funding(self, symbol: str, cutoff: datetime, *, available_at: datetime | None = None,
                start: datetime | None = None) -> list[FundingObservation]:
        params: list[Any] = [symbol, _iso(cutoff), _iso(available_at or datetime.now(UTC))]
        start_sql = ""
        if start:
            start_sql = " AND source_timestamp >= ?"
            params.append(_iso(start))
        rows = self.db.execute(
            "WITH ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol, source_timestamp "
            "ORDER BY received_at DESC, id DESC) AS rn FROM raw_funding_observations "
            "WHERE symbol = ? AND source_timestamp <= ? AND received_at <= ?"
            f"{start_sql}) SELECT * FROM ranked WHERE rn = 1 ORDER BY source_timestamp", params,
        ).fetchall()
        return [FundingObservation(
            symbol=row["symbol"], source_timestamp=_dt(row["source_timestamp"]),
            received_at=_dt(row["received_at"]), funding_rate=row["funding_rate"],
            premium=row["premium"], observation_class=row["observation_class"],
        ) for row in rows]

    def save_feature_observation(self, at: datetime, symbol: str, version: str, value: object) -> None:
        payload = canonical_json(value)
        self.db.execute(
            "INSERT OR IGNORE INTO feature_observations "
            "(snapshot_timestamp, symbol, feature_schema_version, payload_json, content_hash) VALUES (?, ?, ?, ?, ?)",
            (_iso(at), symbol, version, payload, sha256_canonical(value)),
        )
        self.db.commit()

    def save_snapshot(self, snapshot: DecisionSnapshot) -> DecisionSnapshot:
        frozen = freeze_snapshot(snapshot)
        payload = snapshot_payload(frozen)
        try:
            self.db.execute(
                "INSERT INTO decision_snapshots "
                "(snapshot_hash, snapshot_id, snapshot_timestamp, epoch_id, observation_class, scoreable, "
                "canonical_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (frozen.snapshot_hash, frozen.snapshot_id, _iso(frozen.snapshot_timestamp), frozen.epoch_id,
                 frozen.observation_class.value, int(frozen.data_quality.scoreable), payload, _iso(frozen.created_at)),
            )
            self.db.executemany(
                "INSERT INTO snapshot_source_references(snapshot_hash, source_name, source_timestamp) VALUES (?, ?, ?)",
                [(frozen.snapshot_hash, name, _iso(cutoff)) for name, cutoff in frozen.source_cutoffs.items()],
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT snapshot_hash, canonical_json FROM decision_snapshots WHERE epoch_id = ? "
                "AND snapshot_timestamp = ? AND observation_class = ?",
                (frozen.epoch_id, _iso(frozen.snapshot_timestamp), frozen.observation_class.value),
            ).fetchone()
            if row is None or row["snapshot_hash"] != frozen.snapshot_hash or row["canonical_json"] != payload:
                raise RuntimeError("existing immutable snapshot differs from rebuilt inputs")
            return self.load_snapshot(row["snapshot_hash"])
        return frozen

    def load_snapshot(self, digest: str) -> DecisionSnapshot:
        row = self.db.execute(
            "SELECT canonical_json FROM decision_snapshots WHERE snapshot_hash = ?", (digest,)
        ).fetchone()
        if row is None:
            raise KeyError(digest)
        value = json.loads(row["canonical_json"])
        value["snapshot_hash"] = digest
        return DecisionSnapshot.model_validate(value)

    def snapshot_for_cycle_key(self, epoch_id: str, at: datetime, observation_class: str) -> DecisionSnapshot | None:
        row = self.db.execute(
            "SELECT snapshot_hash FROM decision_snapshots WHERE epoch_id = ? AND snapshot_timestamp = ? "
            "AND observation_class = ?", (epoch_id, _iso(at), observation_class),
        ).fetchone()
        return self.load_snapshot(row["snapshot_hash"]) if row else None

    def save_strategy_decision(self, decision: StrategyDecision) -> StrategyDecision:
        payload = canonical_json(decision)
        try:
            self.db.execute(
                "INSERT INTO strategy_decisions "
                "(decision_id, snapshot_hash, strategy_id, strategy_version, decision, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (decision.decision_id, decision.snapshot_hash, decision.strategy_id,
                 decision.strategy_version, decision.decision.value, payload),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json FROM strategy_decisions WHERE snapshot_hash = ? AND strategy_id = ? "
                "AND strategy_version = ?",
                (decision.snapshot_hash, decision.strategy_id, decision.strategy_version),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise RuntimeError("duplicate strategy scoring differs from original decision")
            return StrategyDecision.model_validate(json.loads(row["payload_json"]))
        return decision

    def load_strategy_decision(self, decision_id: str) -> StrategyDecision:
        row = self.db.execute(
            "SELECT payload_json FROM strategy_decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return StrategyDecision.model_validate(json.loads(row["payload_json"]))

    def save_detector_decision(self, decision: DetectorDecision) -> DetectorDecision:
        payload = canonical_json(decision)
        try:
            self.db.execute(
                "INSERT INTO detector_decisions(snapshot_hash, detector_version, trigger, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (decision.snapshot_hash, decision.detector_version, decision.trigger.value, payload),
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json FROM detector_decisions WHERE snapshot_hash = ? AND detector_version = ?",
                (decision.snapshot_hash, decision.detector_version),
            ).fetchone()
            if row is None or row["payload_json"] != payload:
                raise RuntimeError("duplicate detector scoring differs from original decision")
        return decision

    def active_trade(self, strategy_id: str) -> PaperTrade | None:
        row = self.db.execute(
            "SELECT payload_json FROM paper_trades WHERE strategy_id = ? "
            "AND status IN ('PENDING_ENTRY', 'OPEN') ORDER BY signal_time LIMIT 1", (strategy_id,),
        ).fetchone()
        return PaperTrade.model_validate(json.loads(row["payload_json"])) if row else None

    def trade_for_decision(self, decision_id: str) -> PaperTrade | None:
        row = self.db.execute(
            "SELECT payload_json FROM paper_trades WHERE strategy_decision_id = ? ORDER BY signal_time LIMIT 1",
            (decision_id,),
        ).fetchone()
        return PaperTrade.model_validate(json.loads(row["payload_json"])) if row else None

    def active_trades(self) -> list[PaperTrade]:
        rows = self.db.execute(
            "SELECT payload_json FROM paper_trades WHERE status IN ('PENDING_ENTRY', 'OPEN') ORDER BY signal_time"
        ).fetchall()
        return [PaperTrade.model_validate(json.loads(row["payload_json"])) for row in rows]

    def save_trade(self, trade: PaperTrade) -> PaperTrade:
        payload = canonical_json(trade)
        values = (
            trade.paper_trade_id, trade.strategy_decision_id, trade.strategy_id, trade.snapshot_hash,
            trade.direction, _iso(trade.signal_time), _iso(trade.entry_time) if trade.entry_time else None,
            trade.entry_price, trade.stop_price, trade.target_price, trade.current_stop_price,
            trade.highest_price, trade.lowest_price, _iso(trade.exit_time) if trade.exit_time else None,
            trade.exit_price, trade.exit_reason.value if trade.exit_reason else None, trade.fees,
            trade.slippage_cost, trade.funding_cost, trade.gross_pnl, trade.net_pnl,
            trade.return_pct, trade.r_multiple, trade.status.value, _iso(trade.last_processed_at),
            json.dumps(list(trade.flags)), payload,
        )
        self.db.execute(
            "INSERT INTO paper_trades VALUES (" + ",".join("?" for _ in values) + ") "
            "ON CONFLICT(paper_trade_id) DO UPDATE SET "
            "entry_time=excluded.entry_time, entry_price=excluded.entry_price, stop_price=excluded.stop_price, "
            "target_price=excluded.target_price, current_stop_price=excluded.current_stop_price, "
            "highest_price=excluded.highest_price, lowest_price=excluded.lowest_price, "
            "exit_time=excluded.exit_time, exit_price=excluded.exit_price, exit_reason=excluded.exit_reason, "
            "fees=excluded.fees, slippage_cost=excluded.slippage_cost, funding_cost=excluded.funding_cost, "
            "gross_pnl=excluded.gross_pnl, net_pnl=excluded.net_pnl, return_pct=excluded.return_pct, "
            "r_multiple=excluded.r_multiple, status=excluded.status, last_processed_at=excluded.last_processed_at, "
            "flags_json=excluded.flags_json, payload_json=excluded.payload_json", values,
        )
        self.db.commit()
        return trade

    def save_order(self, order_id: str, trade: PaperTrade, eligible_at: datetime) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO paper_orders "
            "(order_id, paper_trade_id, strategy_decision_id, created_at, eligible_at, status, flags_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_id, trade.paper_trade_id, trade.strategy_decision_id, _iso(trade.signal_time),
             _iso(eligible_at), trade.status.value, json.dumps(list(trade.flags))),
        )
        self.db.commit()

    def update_order(self, trade: PaperTrade) -> None:
        self.db.execute(
            "UPDATE paper_orders SET status = ?, fill_time = ?, fill_price = ?, flags_json = ? "
            "WHERE strategy_decision_id = ?",
            (trade.status.value, _iso(trade.entry_time) if trade.entry_time else None,
             trade.entry_price, json.dumps(list(trade.flags)), trade.strategy_decision_id),
        )
        self.db.commit()

    def save_fill(self, trade_id: str, at: datetime, fill_type: str, price: float,
                  fee: float, slippage_cost: float, details: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO paper_fills "
            "(paper_trade_id, fill_time, fill_type, price, fee, slippage_cost, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade_id, _iso(at), fill_type, price, fee, slippage_cost, canonical_json(details)),
        )
        self.db.commit()

    def health(self, component: str, status: str, details: dict[str, Any], at: datetime | None = None) -> None:
        self.db.execute(
            "INSERT INTO health_events(occurred_at, component, status, details_json) VALUES (?, ?, ?, ?)",
            (_iso(at or datetime.now(UTC)), component, status, canonical_json(details)),
        )
        self.db.commit()

    def data_quality_event(self, severity: str, code: str, details: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO data_quality_events(occurred_at, severity, code, details_json) VALUES (?, ?, ?, ?)",
            (_iso(datetime.now(UTC)), severity, code, canonical_json(details)),
        )
        self.db.commit()

    def begin_cycle(self, cycle_id: str, scheduled_at: datetime, observation_class: str) -> sqlite3.Row | None:
        existing = self.db.execute(
            "SELECT * FROM research_cycles WHERE scheduled_at = ? AND observation_class = ?",
            (_iso(scheduled_at), observation_class),
        ).fetchone()
        if existing:
            return existing
        self.db.execute(
            "INSERT INTO research_cycles(cycle_id, scheduled_at, observation_class, started_at, status, details_json) "
            "VALUES (?, ?, ?, ?, 'RUNNING', '{}')",
            (cycle_id, _iso(scheduled_at), observation_class, _iso(datetime.now(UTC))),
        )
        self.db.commit()
        return None

    def finish_cycle(self, cycle_id: str, status: str, snapshot_hash: str | None,
                     details: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE research_cycles SET completed_at = ?, status = ?, snapshot_hash = ?, details_json = ? "
            "WHERE cycle_id = ?",
            (_iso(datetime.now(UTC)), status, snapshot_hash, canonical_json(details), cycle_id),
        )
        self.db.commit()

    def latest_cycle_time(self, observation_class: str) -> datetime | None:
        row = self.db.execute(
            "SELECT MAX(scheduled_at) AS scheduled_at FROM research_cycles WHERE observation_class = ?",
            (observation_class,),
        ).fetchone()
        return datetime.fromisoformat(row["scheduled_at"]) if row and row["scheduled_at"] else None
