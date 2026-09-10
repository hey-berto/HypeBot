from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

import pytest

from hype_autopilot.phase2.config import ACTIVATION_PHRASE
from hype_autopilot.phase2.service import (
    GRANT_VERSION,
    RECEIPT_VERSION,
    consume_single_use_authorization,
    load_durable_grant,
    supervisor_parser,
    worker_parser,
)


def _receipt(path):
    payload = {
        "receipt_version": RECEIPT_VERSION,
        "phase2_epoch_id": "phase2_epoch_002",
        "experiment_id": "phase2_epoch_002",
        "activation_timestamp": datetime(2026, 9, 4, tzinfo=UTC).isoformat(),
        "manifest_hash": "a" * 64,
        "authorization_phrase": ACTIVATION_PHRASE,
        "nonce": "single-use-fixture",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_single_use_receipt_becomes_phrase_free_mode_600_grant(tmp_path):
    receipt = tmp_path / "authorization.json"
    grant = tmp_path / "durable-grant.json"
    _receipt(receipt)

    value = consume_single_use_authorization(receipt, grant)

    assert value["grant_version"] == GRANT_VERSION
    assert value["authorization_phrase_sha256"] == hashlib.sha256(
        ACTIVATION_PHRASE.encode()
    ).hexdigest()
    assert ACTIVATION_PHRASE not in grant.read_text()
    assert not receipt.exists()
    assert receipt.with_name(receipt.name + ".consumed").is_file()
    assert grant.stat().st_mode & 0o777 == 0o600
    assert load_durable_grant(grant) == value
    with pytest.raises(FileExistsError):
        consume_single_use_authorization(receipt.with_name(receipt.name + ".consumed"), grant)


def test_grant_rejects_permissive_mode_and_tampering(tmp_path):
    receipt = tmp_path / "authorization.json"
    grant = tmp_path / "durable-grant.json"
    _receipt(receipt)
    consume_single_use_authorization(receipt, grant)
    os.chmod(grant, 0o644)
    with pytest.raises(PermissionError):
        load_durable_grant(grant)
    os.chmod(grant, 0o600)
    value = json.loads(grant.read_text())
    value["authorization_phrase_sha256"] = "0" * 64
    grant.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PermissionError):
        load_durable_grant(grant)


def test_service_parsers_require_parameterized_linux_paths():
    worker = worker_parser().parse_args(
        [
            "--workspace", "/opt/hypebot/phase2", "--config", "config/phase2/phase2_epoch_002.yaml",
            "--epoch-id", "phase2_epoch_002", "--expected-commit", "a" * 40,
            "--database", "/var/lib/hypebot/phase2/phase2_epoch_002.sqlite3",
            "--data-root", "/var/lib/hypebot/phase2", "--worker-lease", "/run/lock/hypebot/phase2.writer.lock",
            "--grant", "/etc/hypebot/authorized/phase2-epoch-002.grant.json",
        ]
    )
    assert worker.workspace.as_posix() == "/opt/hypebot/phase2"
    supervisor = supervisor_parser().parse_args(
        [
            "--worker-executable", "/opt/hypebot/phase2/.venv/bin/hype-autopilot-phase2-worker",
            "--workspace", "/opt/hypebot/phase2", "--config", "config/phase2/phase2_epoch_002.yaml",
            "--epoch-id", "phase2_epoch_002", "--expected-commit", "a" * 40,
            "--database", "/var/lib/hypebot/phase2/phase2_epoch_002.sqlite3",
            "--data-root", "/var/lib/hypebot/phase2", "--worker-lease", "/run/lock/hypebot/phase2.writer.lock",
            "--supervisor-lease", "/run/lock/hypebot/phase2.supervisor.lock",
            "--grant", "/etc/hypebot/authorized/phase2-epoch-002.grant.json",
            "--event-log", "/var/log/hypebot/phase2-supervisor.jsonl",
            "--stdout-log", "/var/log/hypebot/phase2.stdout.log",
            "--stderr-log", "/var/log/hypebot/phase2.stderr.log",
        ]
    )
    assert supervisor.database.as_posix().startswith("/var/lib/hypebot/phase2/")
