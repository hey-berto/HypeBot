"""Isolated NON_SCORED tests for the production epoch003 pre-start gate."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.operations.phase2_epoch003_start_gate import (
    check_identity,
    check_network_path,
)
from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.config import (
    ACTIVATION_PHRASE,
    file_sha256,
    load_phase2_config,
)
from hype_autopilot.phase2.manifest import build_activation_manifest
from hype_autopilot.phase2.provider import output_json_schema
from hype_autopilot.phase2.service import GRANT_VERSION
from hype_autopilot.phase2.storage import Phase2Repository, phase2_database_schema_hash


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def fixture(tmp_path: Path):
    source = Path(__file__).resolve().parents[1]
    repo = tmp_path / "NON_SCORED_source"
    (repo / "config/phase2").mkdir(parents=True)
    (repo / "prompts/phase2").mkdir(parents=True)
    shutil.copy2(source / "config/phase2/phase2_epoch_003.yaml", repo / "config/phase2")
    shutil.copy2(source / "prompts/phase2/llm_v2.txt", repo / "prompts/phase2")
    (repo / "src").symlink_to(source / "src", target_is_directory=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "HYPE non-scored test")
    git(repo, "config", "user.email", "non-scored@example.invalid")
    git(repo, "add", "config", "prompts")
    git(repo, "commit", "-qm", "NON_SCORED fixture")
    commit = git(repo, "rev-parse", "HEAD")
    config, config_hash = load_phase2_config(
        repo / "config/phase2/phase2_epoch_003.yaml"
    )
    prompt_hash = file_sha256(repo / config.prompt_path)
    schema_hash = sha256_canonical(output_json_schema(config.output_schema_version))
    db_schema_hash = phase2_database_schema_hash()
    activation = datetime(2026, 9, 14, tzinfo=UTC)
    manifest = build_activation_manifest(
        config=config.model_copy(
            update={"evidence_collection_enabled": True, "activation_authorized": True}
        ),
        experiment_id=config.phase2_epoch_id,
        activation_timestamp=activation,
        authorization=ACTIVATION_PHRASE,
        git_commit_hash=commit,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        output_schema_hash=schema_hash,
        database_schema_hash=db_schema_hash,
    )
    database = tmp_path / "NON_SCORED_epoch003_gate.sqlite3"
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        repository = Phase2Repository(db)
        repository.initialize()
        repository.save_manifest(manifest)
    grant = tmp_path / "NON_SCORED_grant.json"
    grant.write_text(
        json.dumps(
            {
                "grant_version": GRANT_VERSION,
                "phase2_epoch_id": config.phase2_epoch_id,
                "experiment_id": config.phase2_epoch_id,
                "activation_timestamp": activation.isoformat(),
                "manifest_hash": manifest.manifest_hash,
                "authorization_phrase_sha256": hashlib.sha256(
                    ACTIVATION_PHRASE.encode()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    grant.chmod(0o600)
    args = SimpleNamespace(
        repo=str(repo),
        expected_commit=commit,
        expected_branch=git(repo, "branch", "--show-current"),
        config="config/phase2/phase2_epoch_003.yaml",
        expected_config_hash=config_hash,
        expected_prompt_hash=prompt_hash,
        expected_schema_hash=schema_hash,
        expected_db_schema_hash=db_schema_hash,
        expected_epoch=config.phase2_epoch_id,
        expected_model=config.model,
        expected_reasoning=config.reasoning_effort,
        database=str(database),
        grant=str(grant),
    )
    return args, database, grant


def approved_network():
    return {
        "relay_country": "sg",
        "interface": "wg0-mullvad",
        "visible_ipv4": "146.70.199.207",
    }


def test_identity_fail_closed_without_scored_db_mutation(fixture):
    args, database, grant = fixture
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    assert check_identity(args, network_check=approved_network)["status"] == "PASS"
    for field, wrong in (
        ("expected_commit", "0" * 40),
        ("expected_config_hash", "0" * 64),
        ("expected_schema_hash", "0" * 64),
        ("expected_db_schema_hash", "0" * 64),
        ("expected_epoch", "phase2_epoch_002"),
        ("expected_branch", "wrong"),
    ):
        old = getattr(args, field)
        setattr(args, field, wrong)
        with pytest.raises(RuntimeError):
            check_identity(args, network_check=approved_network)
        setattr(args, field, old)
    bad = json.loads(grant.read_text())
    bad["authorization_phrase_sha256"] = "0" * 64
    grant.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(PermissionError):
        check_identity(args, network_check=approved_network)
    grant.unlink()
    with pytest.raises(FileNotFoundError):
        check_identity(args, network_check=approved_network)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_network_failure_blocks_prestart(fixture):
    args, database, _ = fixture
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    def unavailable():
        raise RuntimeError("VPN route unavailable")

    with pytest.raises(RuntimeError, match="VPN route unavailable"):
        check_identity(args, network_check=unavailable)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_mullvad_gate_rejects_wrong_state_and_proxy(monkeypatch):
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    outputs = {
        (
            "mullvad",
            "status",
        ): "Connected\nRelay: sg-sin-wg-101\nVisible location: Singapore, Singapore. IPv4: 146.70.199.207",
        ("mullvad", "relay", "get"): "Location: country sg\nMultihop state: disabled",
        (
            "mullvad",
            "lockdown-mode",
            "get",
        ): "Block traffic when the VPN is disconnected: on",
        ("mullvad", "split-tunnel", "list"): "Excluded PIDs:",
        (
            "ip",
            "-4",
            "route",
            "get",
            "172.66.0.243",
            "uid",
            "999",
        ): "172.66.0.243 dev wg0-mullvad src 10.0.0.1 uid 999",
    }
    command = lambda *parts: outputs[parts]
    addresses = lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.66.0.243", 443))
    ]
    assert (
        check_network_path(command=command, addresses=addresses, proxies={}, uid=999)[
            "interface"
        ]
        == "wg0-mullvad"
    )
    changes = (
        (("mullvad", "status"), "Disconnected"),
        (("mullvad", "relay", "get"), "Location: country us\nMultihop state: disabled"),
        (
            ("mullvad", "lockdown-mode", "get"),
            "Block traffic when the VPN is disconnected: off",
        ),
        (
            ("ip", "-4", "route", "get", "172.66.0.243", "uid", "999"),
            "172.66.0.243 dev wlp3s0",
        ),
    )
    for key, wrong in changes:
        old = outputs[key]
        outputs[key] = wrong
        with pytest.raises(RuntimeError):
            check_network_path(
                command=command, addresses=addresses, proxies={}, uid=999
            )
        outputs[key] = old
    with pytest.raises(RuntimeError, match="proxy"):
        check_network_path(
            command=command,
            addresses=addresses,
            proxies={"https": "proxy.invalid"},
            uid=999,
        )


def test_unit_pins_epoch003():
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy/systemd/hypebot-phase2.service.template").read_text()
    health = (
        root / "deploy/systemd/hypebot-phase2-health.service.template"
    ).read_text()
    assert "phase2_epoch_002" not in service + health
    assert "phase2_epoch_003" in service + health
    assert "63d2d4ecc1f135ae94e517164095b5b0a2c2ba5b" in service
    assert "phase2_epoch003_start_gate.py" in service
    assert "ConditionPathExists=" not in service
