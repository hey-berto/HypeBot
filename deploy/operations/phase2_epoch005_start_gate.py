"""Read-only, fail-closed production pre-start gate for prospective Phase 2.

This file is deployed from the separate operations checkout. It never creates a
database, grant, manifest, or research row. The scored runtime remains pinned
to its independently reviewed source commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path


def _command(*args: str) -> str:
    return subprocess.run(
        args, check=True, capture_output=True, text=True
    ).stdout.strip()


def _schema_rows(db: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    return [
        tuple(row)
        for row in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    ]


def check_network_path(
    *,
    command: Callable[..., str] = _command,
    addresses: Callable[..., list[tuple]] = socket.getaddrinfo,
    proxies: dict[str, str] | None = None,
    uid: int | None = None,
) -> dict[str, str]:
    """Require the approved Singapore tunnel for the effective service UID."""
    if proxies is None:
        proxies = urllib.request.getproxies()
    if proxies or any(
        key.lower() in {"http_proxy", "https_proxy", "all_proxy"} for key in os.environ
    ):
        raise RuntimeError("provider proxy environment is not approved")
    status = command("mullvad", "status")
    if not re.search(r"(?m)^Connected\s*$", status):
        raise RuntimeError("Mullvad is not connected")
    if not re.search(r"Relay:\s+sg-[\w-]+", status):
        raise RuntimeError("Mullvad relay is not in Singapore")
    if "Visible location:" not in status or "Singapore" not in status:
        raise RuntimeError("Mullvad visible exit is not Singapore")
    relay = command("mullvad", "relay", "get")
    if not re.search(r"Location:\s+country sg\b", relay):
        raise RuntimeError("Mullvad configured exit country is not sg")
    if not re.search(r"Multihop state:\s+disabled\b", relay):
        raise RuntimeError("Mullvad multihop is not disabled")
    lockdown = command("mullvad", "lockdown-mode", "get")
    if not re.search(r"disconnected:\s+on\b", lockdown):
        raise RuntimeError("Mullvad Lockdown mode is not enabled")
    split = command("mullvad", "split-tunnel", "list")
    if split.strip() != "Excluded PIDs:":
        raise RuntimeError("Mullvad split-tunnel exclusions are present or unreadable")
    effective_uid = os.getuid() if uid is None else uid
    resolved = addresses("api.openai.com", 443, type=socket.SOCK_STREAM)
    if not resolved:
        raise RuntimeError("OpenAI endpoint DNS resolution is empty")
    ipv4_count = 0
    for family, _, _, _, address in resolved:
        if family == socket.AF_INET:
            ipv4_count += 1
            route = command(
                "ip", "-4", "route", "get", address[0], "uid", str(effective_uid)
            )
            if not re.search(r"\bdev wg0-mullvad\b", route):
                raise RuntimeError("OpenAI IPv4 traffic is not routed through Mullvad")
        elif family == socket.AF_INET6:
            try:
                route = command(
                    "ip", "-6", "route", "get", address[0], "uid", str(effective_uid)
                )
            except subprocess.CalledProcessError:
                continue  # unreachable IPv6 is fail-closed; there is no alternate route
            if not re.search(r"\bdev wg0-mullvad\b", route):
                raise RuntimeError("OpenAI IPv6 traffic is not routed through Mullvad")
    if not ipv4_count:
        raise RuntimeError("OpenAI endpoint has no approved IPv4 route")
    visible = re.search(r"Visible location:.*IPv4:\s*([0-9.]+)", status)
    if visible is None:
        raise RuntimeError("Mullvad visible egress IPv4 is unavailable")
    return {
        "relay_country": "sg",
        "interface": "wg0-mullvad",
        "visible_ipv4": visible.group(1),
    }


def check_identity(
    args: argparse.Namespace,
    *,
    network_check: Callable[[], dict[str, str]] = check_network_path,
) -> dict[str, str]:
    root = Path(args.repo).resolve(strict=True)
    if _command("git", "-C", str(root), "rev-parse", "HEAD") != args.expected_commit:
        raise RuntimeError("source commit mismatch")
    if _command(
        "git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=no"
    ):
        raise RuntimeError("tracked source worktree is dirty")
    branch = _command("git", "-C", str(root), "branch", "--show-current") or "DETACHED"
    if branch != args.expected_branch:
        raise RuntimeError("source branch/worktree mode mismatch")
    sys.path.insert(0, str(root / "src"))
    from hype_autopilot.hashing import sha256_canonical
    from hype_autopilot.phase2.config import (
        config_manifest_fields,
        file_sha256,
        load_phase2_config,
    )
    from hype_autopilot.phase2.manifest import Phase2Manifest
    from hype_autopilot.phase2.provider import output_json_schema
    from hype_autopilot.phase2.service import load_durable_grant
    from hype_autopilot.phase2.storage import (
        Phase2Repository,
        phase2_database_schema_hash,
    )

    config_path = (root / args.config).resolve(strict=True)
    if root not in config_path.parents:
        raise RuntimeError("config is outside the pinned source worktree")
    config, config_hash = load_phase2_config(config_path)
    if (
        config_hash != args.expected_config_hash
        or config.phase2_epoch_id != args.expected_epoch
    ):
        raise RuntimeError("config hash or epoch mismatch")
    if config.evidence_collection_enabled or config.activation_authorized:
        raise RuntimeError("on-disk activation gates must remain false")
    if (
        config.model != args.expected_model
        or config.reasoning_effort != args.expected_reasoning
    ):
        raise RuntimeError("model/reasoning identity mismatch")
    prompt = (root / config.prompt_path).resolve(strict=True)
    if root not in prompt.parents or file_sha256(prompt) != args.expected_prompt_hash:
        raise RuntimeError("prompt identity mismatch")
    schema_hash = sha256_canonical(output_json_schema(config.output_schema_version))
    db_schema_hash = phase2_database_schema_hash()
    if (
        schema_hash != args.expected_schema_hash
        or db_schema_hash != args.expected_db_schema_hash
    ):
        raise RuntimeError("output/database schema identity mismatch")

    # Authoritative grant and DB are required before opening anything writable.
    grant = load_durable_grant(args.grant, expected_group=args.expected_grant_group)
    if (
        grant.get("phase2_epoch_id") != args.expected_epoch
        or grant.get("experiment_id") != args.expected_epoch
    ):
        raise RuntimeError("authorization grant epoch mismatch")
    database = Path(args.database).resolve(strict=True)
    if not database.is_file():
        raise RuntimeError("scored database is missing")
    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity failure")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("database foreign-key failure")
        rows = db.execute("SELECT payload_json FROM phase2_manifests").fetchall()
        if len(rows) != 1:
            raise RuntimeError("expected exactly one epoch005 manifest")
        manifest = Phase2Manifest.model_validate_json(rows[0]["payload_json"])
        expected = {
            "phase2_epoch_id": args.expected_epoch,
            "experiment_id": args.expected_epoch,
            "git_commit_hash": args.expected_commit,
            "config_hash": config_hash,
            "prompt_hash": args.expected_prompt_hash,
            "output_schema_hash": schema_hash,
            "database_schema_hash": db_schema_hash,
        }
        if any(getattr(manifest, key) != value for key, value in expected.items()):
            raise RuntimeError("manifest runtime identity mismatch")
        if manifest.frozen_contract != config_manifest_fields(config):
            raise RuntimeError("manifest frozen contract mismatch")
        if (
            grant.get("manifest_hash") != manifest.manifest_hash
            or grant.get("activation_timestamp")
            != manifest.activation_timestamp.isoformat()
        ):
            raise RuntimeError("authorization grant differs from manifest")
        actual_schema = _schema_rows(db)
    with sqlite3.connect(":memory:") as expected_db:
        Phase2Repository(expected_db).initialize()
        if actual_schema != _schema_rows(expected_db):
            raise RuntimeError("database DDL differs from the pinned schema")

    path = network_check()
    return {
        "status": "PASS",
        "epoch": args.expected_epoch,
        "commit": args.expected_commit,
        "config_hash": config_hash,
        "schema_hash": schema_hash,
        **path,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="phase2-epoch005-start-gate")
    for name in (
        "repo",
        "expected-commit",
        "expected-branch",
        "config",
        "expected-config-hash",
        "expected-prompt-hash",
        "expected-schema-hash",
        "expected-db-schema-hash",
        "expected-epoch",
        "expected-model",
        "expected-reasoning",
        "database",
        "grant",
        "expected-grant-group",
    ):
        result.add_argument("--" + name, required=True)
    return result


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--network-only"]:
            result = {"status": "PASS", **check_network_path()}
        else:
            result = check_identity(parser().parse_args())
        print(json.dumps(result, sort_keys=True))
    except Exception as error:  # noqa: BLE001 - fail closed on every operational gate error
        print(
            f"phase2 epoch005 startup gate failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

