from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from hype_autopilot.migration import inspect_runtime_identity, sqlite_read_only_identity


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_sqlite_inventory_is_read_only_and_reports_integrity(tmp_path):
    database = tmp_path / "evidence.sqlite3"
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO events(value) VALUES ('immutable')")
    db.commit()
    db.close()
    before = database.stat().st_mtime_ns
    identity = sqlite_read_only_identity(database)
    after = database.stat().st_mtime_ns
    assert identity["integrity"] == "ok"
    assert identity["foreign_key_violations"] == 0
    assert identity["access_mode"] == "READ_ONLY_IMMUTABLE"
    assert before == after
    assert len(identity["schema_hash"]) == 64


def test_runtime_inventory_fails_on_source_drift_and_records_clean_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "frozen")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "config.yaml").write_text("version: 1\n", encoding="utf-8")
    database = repo / "evidence.sqlite3"
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY)")
    db.commit()
    db.close()
    git(repo, "add", ".")
    git(repo, "commit", "-m", "frozen")
    commit = git(repo, "rev-parse", "HEAD")
    identity = inspect_runtime_identity(
        repo,
        expected_commit=commit,
        config_path="config.yaml",
        database_path="evidence.sqlite3",
        packages=(),
    )
    assert identity["commit"] == commit
    assert identity["tracked_worktree_clean"] is True
    assert identity["database"]["integrity"] == "ok"
    assert len(identity["identity_hash"]) == 64
