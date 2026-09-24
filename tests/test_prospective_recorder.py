import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

from hype_autopilot.prospective_recorder import (
    BufferedIngestion,
    ProspectiveRecorder,
    QueueConfig,
)
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


def test_live_rotation_preserves_queued_events_and_lineage(tmp_path):
    active = tmp_path / "active.sqlite3"
    recorder = ProspectiveRecorder(active)
    old = recorder.start_session()
    old_gap = recorder.start_gap(old, "l2Book", "WEBSOCKET_DISCONNECTED", {"test": True})
    buffer = BufferedIngestion(recorder, QueueConfig(capacity=1))
    base = {"session_id": old, "stream": "trades", "source_timestamp": None}
    buffer.submit({**base, "payload": {"id": "old"}})
    during_results = []
    def during_rotation():
        during_results.append(buffer.submit({**base, "payload": {"id": "queued-during"}}))
        during_results.append(buffer.submit({**base, "payload": {"id": "overflow-during"}}))
    sealed, new = buffer.rotate(tmp_path / "sealed", "2026-01-02", old, rotation_hook=during_rotation)
    old_db = sqlite3.connect(sealed["archive"])
    assert old_db.execute("SELECT COUNT(*) FROM recorder_raw_events").fetchone()[0] == 1
    assert old_db.execute("SELECT payload_json FROM recorder_raw_events").fetchone()[0] == '{"id":"old"}'
    assert old_db.execute("SELECT gap_id FROM recorder_gap_events WHERE gap_id=?", (old_gap,)).fetchone()[0] == old_gap
    old_db.close()
    assert buffer.recorder.db.execute("SELECT COUNT(*) FROM recorder_raw_events").fetchone()[0] == 1
    new_event = buffer.recorder.db.execute("SELECT session_id,payload_json FROM recorder_raw_events").fetchone()
    assert dict(new_event) == {"session_id": new, "payload_json": '{"id":"queued-during"}'}
    assert during_results == ["QUEUED", "OVERFLOW_RECORDED_NOT_DURABLE"]
    assert buffer.recorder.db.execute("SELECT COUNT(*) FROM recorder_connection_events WHERE event='QUEUE_OVERFLOW'").fetchone()[0] == 1
    lineage = buffer.recorder.db.execute("SELECT reconnect_of FROM recorder_sessions WHERE session_id=?", (new,)).fetchone()[0]
    carried = buffer.recorder.db.execute("SELECT reason,metadata_json FROM recorder_gap_events").fetchone()
    assert carried["reason"] == "CARRIED_OPEN_GAP_AFTER_ROTATION" and old_gap in carried["metadata_json"]
    assert lineage == old and sealed["integrity"] == "ok" and len(sealed["sha256"]) == 64
    archive = tmp_path / "sealed" / "hype-raw-2026-01-02.sqlite3"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == sealed["sha256"]
    assert archive.stat().st_mode & 0o222 == 0
    old_again = sqlite3.connect(archive)
    all_payloads = {row[0] for row in old_again.execute("SELECT payload_json FROM recorder_raw_events")}
    old_again.close()
    all_payloads |= {row[0] for row in buffer.recorder.db.execute("SELECT payload_json FROM recorder_raw_events")}
    assert all_payloads == {'{"id":"old"}', '{"id":"queued-during"}'}
    buffer.recorder.close()
    restarted = ProspectiveRecorder(active)
    assert restarted.db.execute("SELECT COUNT(*) FROM recorder_raw_events").fetchone()[0] == 1
    assert restarted.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    restarted.close()


def test_service_rotation_updates_active_session_and_open_gap_mapping(tmp_path):
    class Info:
        def subscribe(self, subscription, callback): pass
    recorder = ProspectiveRecorder(tmp_path / "active.sqlite3")
    service = HyperliquidRecorderService(recorder, lambda _: Info())
    service.connect()
    service.disconnected("controlled-test")
    prior = service.session_id
    old_gap = service.gaps["trades"]
    sealed = service.rotate(str(tmp_path / "sealed"), "2026-01-03")
    assert service.session_id != prior
    assert service.gaps["trades"] != old_gap
    service.recorder.end_gap(service.gaps["trades"], {"subscriptions_restored": True})
    assert service.recorder.health()["open_gaps"] == 2
    assert sealed["archive"].endswith("hype-raw-2026-01-03.sqlite3")
    service.recorder.close()
