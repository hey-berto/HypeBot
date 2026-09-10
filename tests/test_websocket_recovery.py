from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar

from hype_autopilot.data.collectors import ResilientWebsocketCollector
from hype_autopilot.data.models import Candle, ObservationClass
from hype_autopilot.data.repository import Repository
from hype_autopilot.storage.db import connect


class ControlledStop:
    def __init__(self, stop_after_waits: int):
        self.stop_after_waits = stop_after_waits
        self.waits: list[float] = []
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        if len(self.waits) >= self.stop_after_waits:
            self.stopped = True
        return self.stopped


class RecoveryFixture:
    symbols: ClassVar[dict[str, str]] = {"hype": "HYPE", "btc": "BTC"}

    def __init__(self, repository: Repository, *, fail: bool = False):
        self.repository = repository
        self.fail = fail
        self.incremental_calls = 0
        self.gap_calls = 0
        at = datetime(2026, 9, 10, tzinfo=UTC)
        self.candle = Candle(
            symbol="HYPE",
            interval="1m",
            open_time=at,
            close_time=at + timedelta(minutes=1),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=10,
            trade_count=1,
            received_at=at + timedelta(minutes=1),
            observation_class=ObservationClass.SOAK,
        )

    def collect_incremental(self) -> None:
        self.incremental_calls += 1
        if self.fail:
            raise ConnectionError("fixture REST recovery failed")
        self.repository.save_candles([self.candle])

    def recover_gaps(self) -> None:
        self.gap_calls += 1


class DeadInfo:
    def __init__(self):
        self.ws_manager = SimpleNamespace(is_alive=lambda: False)
        self.subscriptions: list[dict[str, object]] = []
        self.disconnected = False

    def subscribe(self, subscription, _handler) -> None:
        self.subscriptions.append(subscription)

    def disconnect_websocket(self) -> None:
        self.disconnected = True


def _repository(tmp_path) -> Repository:
    db = connect(tmp_path / "NON_SCORED_WEBSOCKET.sqlite3")
    repository = Repository(db)
    repository.initialize()
    return repository


def _health(repository: Repository) -> list[tuple[str, dict[str, object]]]:
    return [
        (row["status"], json.loads(row["details_json"]))
        for row in repository.db.execute(
            "SELECT status,details_json FROM health_events "
            "WHERE component='websocket' ORDER BY id"
        )
    ]


def test_bounded_backoff_repeated_reconnect_and_idempotent_rest_catchup(tmp_path):
    repository = _repository(tmp_path)
    recovery: RecoveryFixture | None = None

    def stream_rest_factory(stream_repository):
        nonlocal recovery
        recovery = RecoveryFixture(stream_repository)
        return recovery

    def unavailable(_base_url):
        raise ConnectionError("fixture websocket unavailable")

    stop = ControlledStop(stop_after_waits=8)
    collector = ResilientWebsocketCollector(
        repository,
        RecoveryFixture(repository),
        info_factory=unavailable,
        stream_rest_factory=stream_rest_factory,
    )
    collector.run_forever(stop)

    rows = _health(repository)
    retries = [details["retry_seconds"] for status, details in rows if status == "DISCONNECTED"]
    assert retries == [1, 2, 4, 8, 16, 32, 60, 60]
    assert recovery is not None
    assert recovery.incremental_calls == recovery.gap_calls == 8
    assert repository.db.execute("SELECT COUNT(*) FROM raw_candles").fetchone()[0] == 1
    assert [status for status, _ in rows].count("REST_RECOVERY_COMPLETE") == 8


def test_sdk_thread_death_disconnects_recovers_and_shutdown_interrupts_backoff(tmp_path):
    repository = _repository(tmp_path)
    info = DeadInfo()
    recovery: RecoveryFixture | None = None

    def stream_rest_factory(stream_repository):
        nonlocal recovery
        recovery = RecoveryFixture(stream_repository)
        return recovery

    stop = ControlledStop(stop_after_waits=2)
    collector = ResilientWebsocketCollector(
        repository,
        RecoveryFixture(repository),
        info_factory=lambda _base_url: info,
        stream_rest_factory=stream_rest_factory,
        poll_seconds=0.25,
    )
    collector.run_forever(stop)

    statuses = [status for status, _ in _health(repository)]
    assert statuses == ["CONNECTED", "DISCONNECTED", "REST_RECOVERY_COMPLETE"]
    assert stop.waits == [0.25, 1]
    assert info.disconnected
    assert recovery is not None
    assert recovery.incremental_calls == recovery.gap_calls == 1


def test_rest_recovery_failure_is_distinct_and_ordered(tmp_path):
    repository = _repository(tmp_path)
    info = DeadInfo()
    stop = ControlledStop(stop_after_waits=2)
    collector = ResilientWebsocketCollector(
        repository,
        RecoveryFixture(repository),
        info_factory=lambda _base_url: info,
        stream_rest_factory=lambda stream_repository: RecoveryFixture(
            stream_repository, fail=True
        ),
        poll_seconds=0.25,
    )
    collector.run_forever(stop)

    rows = _health(repository)
    assert [status for status, _ in rows] == [
        "CONNECTED",
        "DISCONNECTED",
        "RECOVERY_FAILED",
    ]
    assert rows[-1][1]["error_class"] == "ConnectionError"
