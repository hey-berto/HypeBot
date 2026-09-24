from datetime import UTC, datetime, timedelta

from hype_autopilot.prospective_recorder import CLASSIFICATION, ProspectiveRecorder


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
    assert recorder.health() == {"classification": CLASSIFICATION, "integrity": "ok", "open_gaps": 0, "events": 2}
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
