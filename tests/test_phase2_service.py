from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hype_autopilot.phase2.config import ACTIVATION_PHRASE
from hype_autopilot.phase2.service import (
    GRANT_MODE,
    GRANT_VERSION,
    RECEIPT_VERSION,
    _validate_grant_metadata,
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


def test_single_use_receipt_becomes_phrase_free_root_group_grant(tmp_path, monkeypatch):
    receipt = tmp_path / "authorization.json"
    grant = tmp_path / "durable-grant.json"
    _receipt(receipt)

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "hype_autopilot.phase2.service.grp.getgrnam",
        lambda _: SimpleNamespace(gr_gid=os.getgid()),
    )
    monkeypatch.setattr(os, "fchown", lambda *_: None)
    value = consume_single_use_authorization(receipt, grant, grant_group="test-auth")

    assert value["grant_version"] == GRANT_VERSION
    assert (
        value["authorization_phrase_sha256"]
        == hashlib.sha256(ACTIVATION_PHRASE.encode()).hexdigest()
    )
    assert ACTIVATION_PHRASE not in grant.read_text()
    assert not receipt.exists()
    assert receipt.with_name(receipt.name + ".consumed").is_file()
    assert grant.stat().st_mode & 0o777 == GRANT_MODE
    monkeypatch.setattr(
        "hype_autopilot.phase2.service._validate_grant_metadata",
        lambda *_args, **_kwargs: None,
    )
    assert load_durable_grant(grant, expected_group="test-auth") == value
    with pytest.raises(FileExistsError):
        consume_single_use_authorization(
            receipt.with_name(receipt.name + ".consumed"),
            grant,
            grant_group="test-auth",
        )


def test_grant_metadata_rejects_wrong_owner_group_mode_and_file_type():
    valid = {"st_mode": stat.S_IFREG | GRANT_MODE, "st_uid": 0, "st_gid": 1234}
    _validate_grant_metadata(SimpleNamespace(**valid), expected_group_gid=1234)
    for changed in (
        {"st_uid": 999},
        {"st_gid": 999},
        {"st_mode": stat.S_IFREG | 0o600},
        {"st_mode": stat.S_IFREG | 0o644},
        {"st_mode": stat.S_IFLNK | GRANT_MODE},
    ):
        metadata = SimpleNamespace(**(valid | changed))
        with pytest.raises(PermissionError):
            _validate_grant_metadata(metadata, expected_group_gid=1234)


def test_grant_payload_tampering_still_fails_closed(tmp_path, monkeypatch):
    grant = tmp_path / "durable-grant.json"
    grant.write_text(
        json.dumps(
            {
                "grant_version": GRANT_VERSION,
                "authorization_phrase_sha256": "0" * 64,
                "activation_timestamp": datetime(2026, 9, 4, tzinfo=UTC).isoformat(),
            }
        )
    )
    monkeypatch.setattr(
        "hype_autopilot.phase2.service.grp.getgrnam",
        lambda _: SimpleNamespace(gr_gid=os.getgid()),
    )
    monkeypatch.setattr(
        "hype_autopilot.phase2.service._validate_grant_metadata",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(PermissionError):
        load_durable_grant(grant, expected_group="test-auth")


def test_service_parsers_require_parameterized_linux_paths():
    worker = worker_parser().parse_args(
        [
            "--workspace",
            "/opt/hypebot/phase2",
            "--config",
            "config/phase2/phase2_epoch_002.yaml",
            "--epoch-id",
            "phase2_epoch_002",
            "--expected-commit",
            "a" * 40,
            "--database",
            "/var/lib/hypebot/phase2/phase2_epoch_002.sqlite3",
            "--data-root",
            "/var/lib/hypebot/phase2",
            "--worker-lease",
            "/run/lock/hypebot/phase2.writer.lock",
            "--grant",
            "/etc/hypebot/authorized/phase2-epoch-002.grant.json",
            "--grant-group",
            "hypebot-phase2-auth",
        ]
    )
    assert worker.workspace.as_posix() == "/opt/hypebot/phase2"
    supervisor = supervisor_parser().parse_args(
        [
            "--worker-executable",
            "/opt/hypebot/phase2/.venv/bin/hype-autopilot-phase2-worker",
            "--workspace",
            "/opt/hypebot/phase2",
            "--config",
            "config/phase2/phase2_epoch_002.yaml",
            "--epoch-id",
            "phase2_epoch_002",
            "--expected-commit",
            "a" * 40,
            "--database",
            "/var/lib/hypebot/phase2/phase2_epoch_002.sqlite3",
            "--data-root",
            "/var/lib/hypebot/phase2",
            "--worker-lease",
            "/run/lock/hypebot/phase2.writer.lock",
            "--supervisor-lease",
            "/run/lock/hypebot/phase2.supervisor.lock",
            "--grant",
            "/etc/hypebot/authorized/phase2-epoch-002.grant.json",
            "--grant-group",
            "hypebot-phase2-auth",
            "--event-log",
            "/var/log/hypebot/phase2-supervisor.jsonl",
            "--stdout-log",
            "/var/log/hypebot/phase2.stdout.log",
            "--stderr-log",
            "/var/log/hypebot/phase2.stderr.log",
        ]
    )
    assert supervisor.database.as_posix().startswith("/var/lib/hypebot/phase2/")
