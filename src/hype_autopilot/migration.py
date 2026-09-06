from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from hype_autopilot.hashing import sha256_canonical


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_read_only_identity(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    uri = f"file:{resolved.as_posix()}?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())
        schema_rows = db.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    finally:
        db.close()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "file_sha256": file_sha256(resolved),
        "integrity": integrity,
        "foreign_key_violations": foreign_keys,
        "schema_hash": sha256_canonical(schema_rows),
        "access_mode": "READ_ONLY_IMMUTABLE",
    }


def inspect_runtime_identity(
    repo: str | Path,
    *,
    expected_commit: str,
    config_path: str | Path,
    database_path: str | Path,
    packages: tuple[str, ...] = ("hyperliquid-python-sdk", "pydantic", "numpy", "arch"),
) -> dict[str, Any]:
    root = Path(repo).resolve()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    commit = git("rev-parse", "HEAD")
    if commit != expected_commit:
        raise RuntimeError(f"source drift: expected {expected_commit}, found {commit}")
    tracked_status = git("status", "--porcelain=v1", "--untracked-files=no")
    config = Path(config_path)
    if not config.is_absolute():
        config = root / config
    database = Path(database_path)
    if not database.is_absolute():
        database = root / database
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "NOT_INSTALLED_IN_INSPECTION_ENVIRONMENT"
    payload = {
        "repository": str(root),
        "branch": git("branch", "--show-current"),
        "commit": commit,
        "tracked_worktree_clean": tracked_status == "",
        "config_path": str(config.resolve()),
        "config_sha256": file_sha256(config),
        "database": sqlite_read_only_identity(database),
        "python_version": sys.version.split()[0],
        "sqlite_version": sqlite3.sqlite_version,
        "package_versions": versions,
    }
    return {**payload, "identity_hash": sha256_canonical(payload)}


def write_identity_json(identity: dict[str, Any], destination: str | Path) -> Path:
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output
