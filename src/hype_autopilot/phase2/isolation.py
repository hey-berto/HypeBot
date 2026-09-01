from __future__ import annotations

import sqlite3
from pathlib import Path


class IsolationViolation(RuntimeError):
    pass


def validate_phase2_database_path(path: str | Path, workspace_root: str | Path) -> Path:
    """Require Phase 2 databases to live inside the isolated Phase 2 worktree.

    SQLite sidecars and any path outside the isolated root are rejected before sqlite3.connect.
    This makes the active Epoch 1 database/WAL/SHM unreachable through this code path.
    """
    root = Path(workspace_root).resolve()
    target = Path(path)
    target = target if target.is_absolute() else root / target
    target = target.resolve()
    if target.suffix != ".sqlite3":
        raise IsolationViolation("Phase 2 database must use a .sqlite3 file")
    if target.name.endswith(("-wal", "-shm")):
        raise IsolationViolation("SQLite WAL/SHM paths cannot be opened directly")
    if root not in target.parents:
        raise IsolationViolation(
            "Phase 2 database must remain inside the isolated worktree"
        )
    if "phase2" not in target.as_posix().lower():
        raise IsolationViolation("Phase 2 database path must use the Phase 2 namespace")
    return target


def connect_phase2(path: str | Path, workspace_root: str | Path) -> sqlite3.Connection:
    target = validate_phase2_database_path(path, workspace_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db
