#!/opt/hypebot/phase2/.venv/bin/python
"""Create a fresh epoch006 manifest database and single-use receipt."""

from __future__ import annotations

import json
import os
import pwd
import secrets
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.config import (
    ACTIVATION_PHRASE,
    file_sha256,
    load_phase2_config,
)
from hype_autopilot.phase2.manifest import build_activation_manifest
from hype_autopilot.phase2.provider import output_json_schema
from hype_autopilot.phase2.service import RECEIPT_VERSION
from hype_autopilot.phase2.storage import Phase2Repository, phase2_database_schema_hash

ROOT = Path("/opt/hypebot/phase2")
CONFIG = ROOT / "config/phase2/phase2_epoch_006.yaml"
DATABASE = Path("/var/lib/hypebot/phase2/phase2_epoch_006.sqlite3")
RECEIPT = Path("/etc/hypebot/authorized/phase2-epoch-006.receipt.json")
GRANT = Path("/etc/hypebot/authorized/phase2-epoch-006.grant.json")
EXPECTED_COMMIT = "41e9e9acc261fd69d32c2d811e9ab4dd556c4620"
EXPECTED_CONFIG = "c69cf18d642aec9bd6bbaa2c85d3a571f45550be7c8a252221708af403a7f24c"
EXPECTED_PROMPT = "c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78"
EXPECTED_SCHEMA = "97318c27b3765780916efe010c3653fa8f8b097bdddd20ef711d40f41a5a1be4"
EXPECTED_DB_SCHEMA = "62b5f58020cbaf19338fbfcf8e81c6b4a8f66cc67b635d2fe622e8f6d286586a"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_exclusive(path: Path, payload: dict[str, object], mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        os.write(descriptor, (serialized + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> None:
    if os.geteuid() != 0:
        raise PermissionError("activation transaction requires root")
    forbidden = (
        DATABASE,
        Path(str(DATABASE) + "-wal"),
        Path(str(DATABASE) + "-shm"),
        RECEIPT,
        RECEIPT.with_name(RECEIPT.name + ".consumed"),
        GRANT,
    )
    if any(path.exists() for path in forbidden):
        raise FileExistsError("epoch006 activation artifact already exists")
    if git("rev-parse", "HEAD") != EXPECTED_COMMIT:
        raise RuntimeError("source commit mismatch")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree is not clean")

    config, config_hash = load_phase2_config(CONFIG)
    prompt_hash = file_sha256(ROOT / config.prompt_path)
    schema_hash = sha256_canonical(output_json_schema(config.output_schema_version))
    db_schema_hash = phase2_database_schema_hash()
    actual = (config_hash, prompt_hash, schema_hash, db_schema_hash)
    expected = (EXPECTED_CONFIG, EXPECTED_PROMPT, EXPECTED_SCHEMA, EXPECTED_DB_SCHEMA)
    if actual != expected:
        raise RuntimeError("frozen config/prompt/schema identity mismatch")
    config.assert_build_only()
    if config.phase2_epoch_id != "phase2_epoch_006":
        raise RuntimeError("epoch identity mismatch")

    activated_at = datetime.now(UTC)
    active_config = config.model_copy(
        update={"evidence_collection_enabled": True, "activation_authorized": True}
    )
    manifest = build_activation_manifest(
        config=active_config,
        experiment_id=config.phase2_epoch_id,
        activation_timestamp=activated_at,
        authorization=ACTIVATION_PHRASE,
        git_commit_hash=EXPECTED_COMMIT,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        output_schema_hash=schema_hash,
        database_schema_hash=db_schema_hash,
    )

    temporary = DATABASE.with_name(f".phase2_epoch_006.{os.getpid()}.sqlite3")
    if temporary.exists():
        raise FileExistsError("activation staging database already exists")
    try:
        with sqlite3.connect(temporary) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            repository = Phase2Repository(db)
            repository.initialize()
            repository.save_manifest(manifest)
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("activation database integrity failure")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("activation database foreign-key failure")
            count = db.execute("SELECT COUNT(*) FROM phase2_manifests").fetchone()[0]
            if count != 1:
                raise RuntimeError("activation database manifest count mismatch")
        service_user = pwd.getpwnam("hypebot")
        os.chown(temporary, service_user.pw_uid, service_user.pw_gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, DATABASE)
        fsync_directory(DATABASE.parent)
    finally:
        if temporary.exists():
            temporary.unlink()

    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "phase2_epoch_id": config.phase2_epoch_id,
        "experiment_id": config.phase2_epoch_id,
        "activation_timestamp": activated_at.isoformat(),
        "manifest_hash": manifest.manifest_hash,
        "authorization_phrase": ACTIVATION_PHRASE,
        "nonce": secrets.token_hex(32),
    }
    write_exclusive(RECEIPT, receipt, 0o600)
    fsync_directory(RECEIPT.parent)
    print(
        json.dumps(
            {
                "activation_timestamp": activated_at.isoformat(),
                "database": str(DATABASE),
                "manifest_hash": manifest.manifest_hash,
                "source_commit": EXPECTED_COMMIT,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
