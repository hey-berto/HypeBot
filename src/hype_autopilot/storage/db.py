from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: str | Path, *, allow_cross_thread: bool = False) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30.0, check_same_thread=not allow_cross_thread)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db
