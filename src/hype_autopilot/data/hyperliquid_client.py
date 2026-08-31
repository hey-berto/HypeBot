from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hype_autopilot.data.models import AssetContext, BboObservation, Candle, FundingObservation, ObservationClass


class HyperliquidMarketDataClient:
    """Read-only adapter around the official Hyperliquid Python SDK."""

    def __init__(self, base_url: str = "https://api.hyperliquid.xyz") -> None:
        try:
            from hyperliquid.info import Info
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("install hyperliquid-python-sdk to collect market data") from exc
        self._info = Info(base_url=base_url, skip_ws=True)

    def candles(
        self, symbol: str, interval: str, start: datetime, end: datetime,
        observation_class: ObservationClass = ObservationClass.WARMUP,
    ) -> list[Candle]:
        rows = self._info.candles_snapshot(
            symbol,
            interval,
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
        )
        received = datetime.now(UTC)
        return [
            Candle(
                symbol=row["s"], interval=row["i"],
                open_time=datetime.fromtimestamp(row["t"] / 1000, UTC),
                close_time=datetime.fromtimestamp(row["T"] / 1000, UTC),
                open=float(row["o"]), high=float(row["h"]), low=float(row["l"]),
                close=float(row["c"]), volume=float(row["v"]),
                trade_count=int(row["n"]), received_at=received, observation_class=observation_class,
            )
            for row in rows
        ]

    def asset_context(self, symbol: str, at: datetime | None = None) -> AssetContext:
        meta, contexts = self._info.meta_and_asset_ctxs()
        index = next(i for i, item in enumerate(meta["universe"]) if item["name"] == symbol)
        row: dict[str, Any] = contexts[index]
        now = at or datetime.now(UTC)
        return AssetContext(
            symbol=symbol, source_timestamp=now, received_at=datetime.now(UTC),
            mark_price=_float(row.get("markPx")), mid_price=_float(row.get("midPx")),
            oracle_price=_float(row.get("oraclePx")), funding_rate=_float(row.get("funding")),
            open_interest=_float(row.get("openInterest")),
            day_notional_volume=_float(row.get("dayNtlVlm")),
        )

    def funding_history(
        self, symbol: str, start: datetime, end: datetime,
        observation_class: ObservationClass = ObservationClass.WARMUP,
    ) -> list[FundingObservation]:
        rows = self._info.funding_history(
            symbol, int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        )
        received = datetime.now(UTC)
        return [FundingObservation(
            symbol=row["coin"], source_timestamp=datetime.fromtimestamp(row["time"] / 1000, UTC),
            received_at=received, funding_rate=float(row["fundingRate"]),
            premium=_float(row.get("premium")), observation_class=observation_class,
        ) for row in rows]

    def bbo(self, symbol: str) -> BboObservation:
        row = self._info.l2_snapshot(symbol)
        bids, asks = row["levels"]
        now = datetime.now(UTC)
        bid = bids[0] if bids else None
        ask = asks[0] if asks else None
        return BboObservation(
            symbol=symbol,
            source_timestamp=datetime.fromtimestamp(row["time"] / 1000, UTC),
            received_at=now,
            bid_price=_float(bid and bid.get("px")), bid_size=_float(bid and bid.get("sz")),
            ask_price=_float(ask and ask.get("px")), ask_size=_float(ask and ask.get("sz")),
        )


def _float(value: Any) -> float | None:
    return None if value is None else float(value)
