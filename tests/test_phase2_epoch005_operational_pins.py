"""Isolated NON_SCORED tests for the production epoch005 pre-start gate."""

from __future__ import annotations

import grp
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.operations.phase2_epoch005_start_gate import (
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


def runtime_guard_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "deploy/operations/phase2_epoch005_runtime_worker.py"
    )
    spec = importlib.util.spec_from_file_location("epoch005_runtime_worker_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    shutil.copy2(source / "config/phase2/phase2_epoch_005.yaml", repo / "config/phase2")
    shutil.copy2(source / "prompts/phase2/llm_v2.txt", repo / "prompts/phase2")
    (repo / "src").symlink_to(source / "src", target_is_directory=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "HYPE non-scored test")
    git(repo, "config", "user.email", "non-scored@example.invalid")
    git(repo, "add", "config", "prompts")
    git(repo, "commit", "-qm", "NON_SCORED fixture")
    commit = git(repo, "rev-parse", "HEAD")
    config, config_hash = load_phase2_config(
        repo / "config/phase2/phase2_epoch_005.yaml"
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
    database = tmp_path / "NON_SCORED_epoch005_gate.sqlite3"
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
        config="config/phase2/phase2_epoch_005.yaml",
        expected_config_hash=config_hash,
        expected_prompt_hash=prompt_hash,
        expected_schema_hash=schema_hash,
        expected_db_schema_hash=db_schema_hash,
        expected_epoch=config.phase2_epoch_id,
        expected_model=config.model,
        expected_reasoning=config.reasoning_effort,
        database=str(database),
        grant=str(grant),
        expected_grant_group=grp.getgrgid(os.getgid()).gr_name,
    )
    return args, database, grant


@pytest.fixture(autouse=True)
def bypass_root_metadata_for_non_scored_fixture(monkeypatch):
    monkeypatch.setattr(
        "hype_autopilot.phase2.service._validate_grant_metadata",
        lambda *_args, **_kwargs: None,
    )


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
        ("expected_prompt_hash", "0" * 64),
        ("expected_schema_hash", "0" * 64),
        ("expected_db_schema_hash", "0" * 64),
        ("expected_epoch", "phase2_epoch_002"),
        ("expected_epoch", "phase2_epoch_003"),
        ("expected_model", "wrong-model"),
        ("expected_reasoning", "wrong-effort"),
        ("expected_branch", "wrong"),
    ):
        old = getattr(args, field)
        setattr(args, field, wrong)
        with pytest.raises(RuntimeError):
            check_identity(args, network_check=approved_network)
        setattr(args, field, old)
    bad = json.loads(grant.read_text())
    old_epoch = dict(bad)
    old_epoch["phase2_epoch_id"] = "phase2_epoch_003"
    old_epoch["experiment_id"] = "phase2_epoch_003"
    grant.write_text(json.dumps(old_epoch), encoding="utf-8")
    with pytest.raises(RuntimeError, match="authorization grant epoch mismatch"):
        check_identity(args, network_check=approved_network)
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


def test_epoch005_operational_helpers_remain_available_for_audit():
    root = Path(__file__).resolve().parents[1]
    assert (root / "deploy/operations/phase2_epoch005_start_gate.py").is_file()
    assert (root / "deploy/operations/phase2_epoch005_runtime_worker.py").is_file()


def test_health_timer_bootstraps_from_timer_activation_not_host_boot():
    root = Path(__file__).resolve().parents[1]
    timer = (
        root / "deploy/systemd/hypebot-phase2-health.timer.template"
    ).read_text()
    assert "OnActiveSec=5min" in timer
    assert "OnUnitActiveSec=5min" in timer
    assert "OnBootSec=" not in timer
    assert "Unit=hypebot-phase2-health.service" in timer


def test_runtime_path_guard_allows_only_a_validated_provider_call(monkeypatch):
    module = runtime_guard_module()
    from hype_autopilot.phase2.provider import OpenAIResponsesProvider

    calls: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    def original(self, **kwargs):
        calls.append("provider")
        return "response"

    monkeypatch.setattr(OpenAIResponsesProvider, "invoke", original)
    module.install_runtime_guard(
        network_check=approved_network,
        telemetry=lambda status, *, details: events.append((status, details)),
    )
    provider = object.__new__(OpenAIResponsesProvider)
    assert (
        provider.invoke(prompt="p", snapshot_json="{}", timeout_seconds=1) == "response"
    )
    assert calls == ["provider"]
    assert events == [("PASS", approved_network())]


@pytest.mark.parametrize(
    "failure", [RuntimeError("invalid route"), OSError("probe failed")]
)
def test_runtime_path_guard_blocks_invalid_or_unknown_path_without_fallback(
    monkeypatch, failure
):
    module = runtime_guard_module()
    from hype_autopilot.phase2.provider import OpenAIResponsesProvider, ProviderError

    calls: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    def original(self, **kwargs):
        calls.append("provider")
        return "response"

    monkeypatch.setattr(OpenAIResponsesProvider, "invoke", original)
    module.install_runtime_guard(
        network_check=lambda: (_ for _ in ()).throw(failure),
        telemetry=lambda status, *, details: events.append((status, details)),
    )
    provider = object.__new__(OpenAIResponsesProvider)
    with pytest.raises(ProviderError, match="runtime VPN path validation failed"):
        provider.invoke(prompt="p", snapshot_json="{}", timeout_seconds=1)
    assert calls == []
    assert events == [
        ("BLOCKED", {"error_class": type(failure).__name__, "error": str(failure)})
    ]


def test_runtime_path_guard_blocks_when_telemetry_cannot_persist(monkeypatch):
    module = runtime_guard_module()
    from hype_autopilot.phase2.provider import OpenAIResponsesProvider, ProviderError

    calls: list[str] = []
    monkeypatch.setattr(
        OpenAIResponsesProvider,
        "invoke",
        lambda self, **kwargs: calls.append("provider"),
    )
    module.install_runtime_guard(
        network_check=lambda: (_ for _ in ()).throw(RuntimeError("invalid route")),
        telemetry=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("log unavailable")
        ),
    )
    provider = object.__new__(OpenAIResponsesProvider)
    with pytest.raises(ProviderError, match="telemetry failed"):
        provider.invoke(prompt="p", snapshot_json="{}", timeout_seconds=1)
    assert calls == []
