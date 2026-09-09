from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path


class IsolationViolation(RuntimeError):
    pass


class Phase2SQLiteConnection(sqlite3.Connection):
    """SQLite connection whose nested atomic scopes defer repository commits.

    The shared repositories intentionally commit after each ordinary operation.
    Phase 2 uses this connection subclass to group simulator state transitions
    without changing those repositories or the frozen simulator economics.
    """

    _phase2_atomic_depth = 0
    _phase2_rollback_only = False

    def commit(self) -> None:
        if self._phase2_atomic_depth == 0:
            super().commit()

    def rollback(self) -> None:
        if self._phase2_atomic_depth:
            self._phase2_rollback_only = True
            return
        super().rollback()

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        outermost = self._phase2_atomic_depth == 0
        if outermost:
            super().execute("BEGIN IMMEDIATE")
            self._phase2_rollback_only = False
        self._phase2_atomic_depth += 1
        try:
            yield
        except BaseException:
            self._phase2_atomic_depth -= 1
            if outermost:
                super().rollback()
                self._phase2_rollback_only = False
            else:
                self._phase2_rollback_only = True
            raise
        else:
            self._phase2_atomic_depth -= 1
            if outermost:
                if self._phase2_rollback_only:
                    super().rollback()
                    self._phase2_rollback_only = False
                    raise RuntimeError(
                        "Phase 2 atomic transaction was marked rollback-only"
                    )
                super().commit()


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
    db = sqlite3.connect(target, timeout=30.0, factory=Phase2SQLiteConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db
