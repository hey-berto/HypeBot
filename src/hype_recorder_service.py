"""Separate-host Hyperliquid recorder service. No trading or wallet code."""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

from hype_autopilot.prospective_recorder import BufferedIngestion, ProspectiveRecorder


class HyperliquidRecorderService:
    subscriptions = (
        {"type": "trades", "coin": "HYPE"},
        {"type": "l2Book", "coin": "HYPE"},
        {"type": "activeAssetCtx", "coin": "HYPE"},
    )

    def __init__(self, recorder: ProspectiveRecorder, info_factory) -> None:
        self.recorder, self.info_factory = recorder, info_factory
        self.buffer = BufferedIngestion(recorder)
        self.session_id: str | None = None
        self.gaps: dict[str, str] = {}

    def connect(self, reconnect_of: str | None = None) -> object:
        self.session_id = self.recorder.start_session(reconnect_of=reconnect_of)
        self.recorder.observe_timedatectl(self.session_id)
        info = self.info_factory("https://api.hyperliquid.xyz")
        for subscription in self.subscriptions:
            info.subscribe(subscription, self.handle)
        self.recorder.connection_event(self.session_id, "SUBSCRIPTIONS_RESTORED", {"count": len(self.subscriptions)})
        for gap in self.gaps.values():
            self.recorder.end_gap(gap, {"subscriptions_restored": True})
        self.gaps.clear()
        return info

    def disconnected(self, reason: str) -> None:
        assert self.session_id
        self.recorder.connection_event(self.session_id, "DISCONNECTED", {"reason": reason})
        for stream in ("trades", "l2Book", "activeAssetCtx"):
            self.gaps.setdefault(stream, self.recorder.start_gap(self.session_id, stream, "WEBSOCKET_DISCONNECTED", {"reason": reason}))

    def handle(self, message: dict) -> None:
        assert self.session_id
        channel, data = message.get("channel"), message.get("data", message)
        stream = {"trades": "trades", "l2Book": "l2Book", "activeAssetCtx": "activeAssetCtx"}.get(channel)
        if stream is None:
            return
        items = data if stream == "trades" and isinstance(data, list) else [data]
        for item in items:
            millis = item.get("time") if isinstance(item, dict) else None
            source = datetime.fromtimestamp(millis / 1000, UTC) if millis is not None else None
            source_key = str(item.get("tid") or item.get("hash") or item.get("time") or "")
            self.buffer.submit({"session_id": self.session_id, "stream": stream, "payload": item, "source_timestamp": source, "source_key": source_key or None})
        self.buffer.drain()

    def poll_funding(self, info: object) -> None:
        assert self.session_id
        now = datetime.now(UTC)
        for row in info.funding_history("HYPE", int((now - timedelta(hours=2)).timestamp() * 1000), int(now.timestamp() * 1000)):
            source = datetime.fromtimestamp(row["time"] / 1000, UTC)
            self.buffer.submit({"session_id": self.session_id, "stream": "funding", "payload": row, "source_timestamp": source, "source_key": str(row["time"])})
        self.buffer.drain()


def main() -> None:
    database = os.environ["HYPE_RECORDER_DATABASE"]
    from hyperliquid.info import Info
    recorder = ProspectiveRecorder(database)
    service = HyperliquidRecorderService(recorder, lambda base_url: Info(base_url=base_url, skip_ws=False))
    prior = None
    stop = threading.Event()
    try:
        while not stop.is_set():
            info = service.connect(prior)
            prior = service.session_id
            while not stop.wait(30):
                if getattr(info, "ws_manager", None) is None or not info.ws_manager.is_alive():
                    service.disconnected("WEBSOCKET_NOT_ALIVE")
                    break
                service.poll_funding(info)
            info.disconnect_websocket()
    finally:
        recorder.close()


if __name__ == "__main__":
    main()
