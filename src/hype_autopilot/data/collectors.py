from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from hype_autopilot.data.hyperliquid_client import HyperliquidMarketDataClient
from hype_autopilot.data.models import AssetContext, BboObservation, Candle, ObservationClass
from hype_autopilot.data.repository import Repository
from hype_autopilot.storage.db import connect

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
WARMUP_DAYS = {
    ("HYPE", "1m"): 3, ("HYPE", "5m"): 14, ("HYPE", "15m"): 30,
    ("HYPE", "1h"): 61, ("HYPE", "4h"): 90,
    ("BTC", "15m"): 30, ("BTC", "1h"): 61, ("BTC", "4h"): 90,
}


def retry(operation: Callable[[], T], *, attempts: int = 5, base_delay: float = 0.5) -> T:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:  # network boundary
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(base_delay * (2 ** attempt), 8.0))
    assert last is not None
    raise last


class MarketDataCollector:
    def __init__(self, repository: Repository, client: HyperliquidMarketDataClient,
                 *, symbols: dict[str, str] | None = None) -> None:
        self.repository = repository
        self.client = client
        self.symbols = symbols or {"hype": "HYPE", "btc": "BTC"}

    def warmup(self, end: datetime | None = None) -> dict[str, int]:
        end = (end or datetime.now(UTC)).astimezone(UTC)
        counts: dict[str, int] = {}
        for (symbol, interval), days in WARMUP_DAYS.items():
            rows = retry(lambda s=symbol, i=interval, d=days: self.client.candles(
                s, i, end - timedelta(days=d), end, ObservationClass.WARMUP
            ))
            counts[f"{symbol}:{interval}"] = self.repository.save_candles(rows)
        funding = retry(lambda: self.client.funding_history(
            self.symbols["hype"], end - timedelta(days=8), end, ObservationClass.WARMUP
        ))
        counts["HYPE:funding"] = self.repository.save_funding(funding)
        self.collect_context(end)
        self.detect_gaps(end)
        self.repository.health("collector", "WARMUP_COMPLETE", counts, end)
        return counts

    def collect_context(self, at: datetime | None = None) -> None:
        at = at or datetime.now(UTC)
        context = retry(lambda: self.client.asset_context(self.symbols["hype"]))
        self.repository.save_asset_context(context)
        try:
            self.repository.save_bbo(retry(lambda: self.client.bbo(self.symbols["hype"]), attempts=3))
        except Exception as exc:
            self.repository.data_quality_event("WARN", "OPTIONAL_BBO_UNAVAILABLE", {"error": repr(exc)})

    def collect_incremental(self, end: datetime | None = None,
                            observation_class: ObservationClass = ObservationClass.SOAK) -> dict[str, int]:
        end = (end or datetime.now(UTC)).astimezone(UTC)
        counts: dict[str, int] = {}
        for symbol, interval in WARMUP_DAYS:
            last = self.repository.latest_candle_close(symbol, interval)
            start = (last - timedelta(seconds=INTERVAL_SECONDS[interval])) if last else (
                end - timedelta(days=WARMUP_DAYS[(symbol, interval)])
            )
            rows = retry(lambda s=symbol, i=interval, begin=start: self.client.candles(
                s, i, begin, end, observation_class
            ))
            counts[f"{symbol}:{interval}"] = self.repository.save_candles(rows)
        funding = retry(lambda: self.client.funding_history(
            self.symbols["hype"], end - timedelta(days=8), end, observation_class
        ))
        counts["HYPE:funding"] = self.repository.save_funding(funding)
        self.collect_context(end)
        self.detect_gaps(end)
        self.repository.health("collector", "INCREMENTAL_COMPLETE", counts, end)
        return counts

    def detect_gaps(self, cutoff: datetime) -> int:
        detected = 0
        now = datetime.now(UTC).isoformat()
        for symbol, interval in WARMUP_DAYS:
            rows = self.repository.candles(symbol, interval, cutoff)
            step = timedelta(seconds=INTERVAL_SECONDS[interval])
            for previous, current in zip(rows, rows[1:]):
                expected = previous.open_time + step
                if current.open_time > expected:
                    self.repository.db.execute(
                        "INSERT OR IGNORE INTO collection_gaps "
                        "(symbol, interval, gap_start, gap_end, detected_at, status) VALUES (?, ?, ?, ?, ?, 'OPEN')",
                        (symbol, interval, expected.isoformat(), current.open_time.isoformat(), now),
                    )
                    detected += 1
                elif current.open_time < expected:
                    continue
        self.repository.db.commit()
        return detected

    def recover_gaps(self, cutoff: datetime | None = None) -> dict[str, int]:
        cutoff = cutoff or datetime.now(UTC)
        recovered: dict[str, int] = {}
        rows = self.repository.db.execute(
            "SELECT * FROM collection_gaps WHERE status = 'OPEN' ORDER BY gap_start"
        ).fetchall()
        for row in rows:
            start, end = datetime.fromisoformat(row["gap_start"]), min(datetime.fromisoformat(row["gap_end"]), cutoff)
            candles = retry(lambda r=row, s=start, e=end: self.client.candles(
                r["symbol"], r["interval"], s, e, ObservationClass.SOAK
            ))
            count = self.repository.save_candles(candles)
            check = self.repository.candles(row["symbol"], row["interval"], end, start=start)
            step = INTERVAL_SECONDS[row["interval"]]
            complete = len(check) >= max(1, int((end - start).total_seconds() / step))
            if complete:
                self.repository.db.execute(
                    "UPDATE collection_gaps SET status = 'RECOVERED', recovered_at = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(), row["id"]),
                )
            recovered[f"{row['symbol']}:{row['interval']}:{row['id']}"] = count
        self.repository.db.commit()
        return recovered


class ResilientWebsocketCollector:
    """Official-SDK websocket loop with reconnect and REST gap recovery."""

    def __init__(self, repository: Repository, rest_collector: MarketDataCollector,
                 base_url: str = "https://api.hyperliquid.xyz") -> None:
        self.repository = repository
        self.rest_collector = rest_collector
        self.base_url = base_url
        row = repository.db.execute("PRAGMA database_list").fetchone()
        self.database_path = row["file"] if row and row["file"] else None
        self._stream_repository: Repository | None = None
        self._lock = threading.RLock()

    def run_forever(self, stop: threading.Event | None = None) -> None:  # pragma: no cover - operational loop
        stop = stop or threading.Event()
        backoff = 1.0
        if not self.database_path:
            raise RuntimeError("websocket collector requires a file-backed SQLite database")
        stream_db = connect(self.database_path, allow_cross_thread=True)
        stream_repo = Repository(stream_db)
        stream_repo.initialize()
        self._stream_repository = stream_repo
        stream_rest = MarketDataCollector(
            stream_repo, HyperliquidMarketDataClient(self.base_url), symbols=self.rest_collector.symbols
        )
        while not stop.is_set():
            info = None
            try:
                from hyperliquid.info import Info
                info = Info(base_url=self.base_url, skip_ws=False)
                for symbol, interval in WARMUP_DAYS:
                    info.subscribe({"type": "candle", "coin": symbol, "interval": interval}, self._handle)
                for feed in ("bbo", "activeAssetCtx"):
                    info.subscribe({"type": feed, "coin": "HYPE"}, self._handle)
                with self._lock:
                    stream_repo.health("websocket", "CONNECTED", {"subscriptions": len(WARMUP_DAYS) + 2})
                backoff = 1.0
                while not stop.wait(1.0):
                    manager = info.ws_manager
                    if manager is None or not manager.is_alive():
                        raise ConnectionError("official SDK websocket thread stopped")
            except Exception as exc:
                with self._lock:
                    stream_repo.health("websocket", "DISCONNECTED", {"error": repr(exc), "retry_seconds": backoff})
                try:
                    stream_rest.collect_incremental()
                    stream_rest.recover_gaps()
                except Exception as recovery_exc:
                    with self._lock:
                        stream_repo.health("websocket", "RECOVERY_FAILED", {"error": repr(recovery_exc)})
                stop.wait(backoff)
                backoff = min(backoff * 2.0, 60.0)
            finally:
                if info is not None:
                    try:
                        info.disconnect_websocket()
                    except Exception:
                        pass

    def _handle(self, message: dict[str, Any]) -> None:
        try:
            if self._stream_repository is None:
                return
            repository = self._stream_repository
            channel = message.get("channel")
            data = message.get("data", message)
            received = datetime.now(UTC)
            if channel == "candle":
                close_time = datetime.fromtimestamp(data["T"] / 1000, UTC)
                if close_time > received:
                    return
                with self._lock:
                    repository.save_candles([Candle(
                    symbol=data["s"], interval=data["i"],
                    open_time=datetime.fromtimestamp(data["t"] / 1000, UTC), close_time=close_time,
                    open=float(data["o"]), high=float(data["h"]), low=float(data["l"]),
                    close=float(data["c"]), volume=float(data["v"]), trade_count=int(data["n"]),
                    received_at=received, observation_class=ObservationClass.SOAK,
                    )])
            elif channel == "bbo":
                bid, ask = data["bbo"]
                with self._lock:
                    repository.save_bbo(BboObservation(
                    symbol=data["coin"], source_timestamp=datetime.fromtimestamp(data["time"] / 1000, UTC),
                    received_at=received, bid_price=float(bid["px"]) if bid else None,
                    bid_size=float(bid["sz"]) if bid else None, ask_price=float(ask["px"]) if ask else None,
                    ask_size=float(ask["sz"]) if ask else None,
                    ))
            elif channel in {"activeAssetCtx", "activeSpotAssetCtx"}:
                row = data["ctx"]
                with self._lock:
                    repository.save_asset_context(AssetContext(
                    symbol=data["coin"], source_timestamp=received, received_at=received,
                    mark_price=_float(row.get("markPx")), mid_price=_float(row.get("midPx")),
                    oracle_price=_float(row.get("oraclePx")), funding_rate=_float(row.get("funding")),
                    open_interest=_float(row.get("openInterest")),
                    day_notional_volume=_float(row.get("dayNtlVlm")),
                    ))
        except Exception as exc:
            LOGGER.exception("websocket message rejected")
            target = self._stream_repository or self.repository
            with self._lock:
                target.data_quality_event("ERROR", "WEBSOCKET_MESSAGE_REJECTED", {"error": repr(exc)})


def _float(value: Any) -> float | None:
    return None if value is None else float(value)
