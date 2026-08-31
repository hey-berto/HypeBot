import threading

from hype_autopilot.data.repository import Repository
from hype_autopilot.storage.db import connect


def test_dedicated_cross_thread_wal_connection_can_persist_health(tmp_path):
    path = tmp_path / "threaded.sqlite3"
    db = connect(path, allow_cross_thread=True)
    repo = Repository(db)
    repo.initialize()
    errors = []

    def write():
        try:
            repo.health("websocket", "CONNECTED", {"test": True})
        except Exception as exc:  # pragma: no cover - assertion captures it
            errors.append(exc)

    thread = threading.Thread(target=write)
    thread.start()
    thread.join()
    assert not errors
    assert db.execute("SELECT COUNT(*) FROM health_events").fetchone()[0] == 1
