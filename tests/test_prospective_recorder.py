from datetime import UTC, datetime, timedelta

from hype_autopilot.prospective_recorder import BufferedIngestion, CLASSIFICATION, ProspectiveRecorder, QueueConfig
from hype_recorder_service import HyperliquidRecorderService


def test_append_only_ingestion_duplicate_order_and_gap(tmp_path):
    recorder = ProspectiveRecorder(tmp_path / "recorder.sqlite3")
    session = recorder.start_session(metadata={"host_clock": "ntp"})
    at = datetime(2026, 1, 1, tzinfo=UTC)
    payload = {"coin": "HYPE", "time": 1, "px": "1", "sz": "2"}
    assert recorder.ingest(session_id=session, stream="trades", payload=payload, source_timestamp=at, source_key="1", received_at=at) == "RECORDED"
    assert recorder.ingest(session_id=session, stream="trades", payload=payload, source_timestamp=at, source_key="1", received_at=at) == "DUPLICATE"
    assert recorder.ingest(session_id=session, stream="trades", payload={**payload, "time": 0}, source_timestamp=at-timedelta(seconds=1), source_key="0", received_at=at) == "OUT_OF_ORDER"
    gap = recorder.start_gap(session, "l2Book", "WEBSOCKET_DISCONNECTED")
    recorder.end_gap(gap, {"subscriptions_restored": True})
    assert recorder.health()["integrity"] == "ok" and recorder.health()["duplicate_events"] == 1
    raw = recorder.db.execute("SELECT payload_sha256,received_at,received_monotonic_ns FROM recorder_raw_events").fetchone()
    assert len(raw["payload_sha256"]) == 64 and raw["received_at"] == at.isoformat() and raw["received_monotonic_ns"] > 0
    recorder.close()


def test_restart_creates_new_session_and_preserves_evidence(tmp_path):
    path = tmp_path / "recorder.sqlite3"
    first = ProspectiveRecorder(path)
    session = first.start_session()
    first.ingest(session_id=session, stream="funding", payload={"fundingRate": "0"}, source_timestamp=None)
    first.close()
    second = ProspectiveRecorder(path)
    second.start_session(reconnect_of=session)
    assert second.db.execute("SELECT COUNT(*) FROM recorder_sessions").fetchone()[0] == 2
    assert second.db.execute("SELECT COUNT(*) FROM recorder_raw_events").fetchone()[0] == 1
    second.close()


def test_session_identity_and_clock_health_are_immutable_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPE_RECORDER_GIT_SHA", "a" * 40)
    recorder = ProspectiveRecorder(tmp_path / "recorder.sqlite3")
    session = recorder.start_session(metadata={"process_start_utc": "x"})
    recorder.record_clock_health(session, status="UNCERTAIN", source="timesyncd", offset_seconds=None, raw={"synced": False})
    at = datetime(2026, 1, 1, tzinfo=UTC)
    recorder.ingest(session_id=session, stream="activeAssetCtx", payload={"ctx": {}}, source_timestamp=None, received_at=at)
    metadata = recorder.db.execute("SELECT metadata_json FROM recorder_sessions WHERE session_id=?", (session,)).fetchone()[0]
    assert '"git_sha":"' + "a" * 40 in metadata
    assert recorder.health()["clock_health"] == "UNCERTAIN"
    assert recorder.db.execute("SELECT source_timestamp,received_at FROM recorder_raw_events").fetchone()["source_timestamp"] is None
    recorder.close()


def test_adapter_restores_subscriptions_and_records_native_timestamps(tmp_path):
    class Info:
        def __init__(self): self.subscriptions = []
        def subscribe(self, subscription, callback): self.subscriptions.append((subscription, callback))
    recorder = ProspectiveRecorder(tmp_path / "recorder.sqlite3")
    service = HyperliquidRecorderService(recorder, lambda _: Info())
    info = service.connect()
    assert len(info.subscriptions) == 3
    service.handle({"channel": "trades", "data": [{"time": 1_700_000_000_000, "tid": 7, "px": "1"}]})
    service.disconnected("test")
    service.connect(service.session_id)
    row = recorder.db.execute("SELECT stream,source_timestamp,source_key FROM recorder_raw_events").fetchone()
    assert dict(row) == {"stream": "trades", "source_timestamp": "2023-11-14T22:13:20+00:00", "source_key": "7"}
    assert recorder.health()["open_gaps"] == 0
    recorder.close()


def test_bounded_queue_latency_recovery_and_archive_seal(tmp_path):
    recorder = ProspectiveRecorder(tmp_path / "raw.sqlite3")
    session = recorder.start_session()
    buffer = BufferedIngestion(recorder, QueueConfig(capacity=2, warning_occupancy=.5, failure_occupancy=1.0, sustained_seconds=1))
    item = {"session_id": session, "stream": "l2Book", "payload": {"time": 1}, "source_timestamp": None}
    assert buffer.submit(item) == "QUEUED"
    assert buffer.submit({**item, "payload": {"time": 2}}) == "QUEUED"
    assert buffer.submit({**item, "payload": {"time": 3}}) == "OVERFLOW_RECORDED_NOT_DURABLE"
    assert buffer.drain() == 2
    health = buffer.health()
    assert health["overflow_count"] == 1 and health["queue_depth"] == 0 and health["write_latency_seconds"]["l2Book"]["p95"] is not None
    sealed = recorder.seal_archive(tmp_path / "sealed", "2026-01-01")
    assert sealed["integrity"] == "ok" and len(sealed["sha256"]) == 64
    assert recorder.health()["integrity_bearing"]["reconnect_count"] == 0
    recorder.close()
