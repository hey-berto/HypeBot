from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sys
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hype_autopilot.data.collectors import ResilientWebsocketCollector
from hype_autopilot.phase2.config import ACTIVATION_PHRASE
from hype_autopilot.phase2.manifest import Phase2Manifest
from hype_autopilot.phase2.runtime import Phase2Runtime, build_phase2_runtime
from hype_autopilot.phase2.scheduler import schedule_phase2_forever
from hype_autopilot.phase2.supervision import (
    ExclusiveProcessLease,
    SingleWriterSupervisor,
    SupervisorEvent,
)

GRANT_VERSION = "PHASE2_DURABLE_ACTIVATION_GRANT_V1"
RECEIPT_VERSION = "PHASE2_SINGLE_USE_AUTHORIZATION_RECEIPT_V1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def consume_single_use_authorization(receipt: Path, grant: Path) -> dict[str, Any]:
    """Consume one authorization receipt into a phrase-free durable grant.

    This helper is intentionally never called by service startup. Creating the
    grant is a separately authorized operational action; workers can only read
    and validate the resulting root-controlled file.
    """
    receipt = receipt.resolve(strict=True)
    grant = grant.resolve()
    if grant.exists():
        raise FileExistsError("durable activation grant already exists")
    if receipt.name.endswith(".consumed"):
        raise ValueError("authorization receipt was already consumed")
    raw = receipt.read_bytes()
    payload = json.loads(raw)
    required = {
        "receipt_version",
        "phase2_epoch_id",
        "experiment_id",
        "activation_timestamp",
        "manifest_hash",
        "authorization_phrase",
        "nonce",
    }
    if set(payload) != required or payload["receipt_version"] != RECEIPT_VERSION:
        raise ValueError("authorization receipt shape/version is invalid")
    if payload["authorization_phrase"] != ACTIVATION_PHRASE:
        raise PermissionError("exact Phase 2 activation authorization is required")
    activated = datetime.fromisoformat(
        str(payload["activation_timestamp"]).replace("Z", "+00:00")
    )
    if activated.tzinfo is None:
        raise ValueError("activation timestamp must be timezone-aware")
    durable = {
        "grant_version": GRANT_VERSION,
        "phase2_epoch_id": str(payload["phase2_epoch_id"]),
        "experiment_id": str(payload["experiment_id"]),
        "activation_timestamp": activated.astimezone(UTC).isoformat(),
        "manifest_hash": str(payload["manifest_hash"]),
        "authorization_phrase_sha256": hashlib.sha256(
            ACTIVATION_PHRASE.encode("utf-8")
        ).hexdigest(),
        "consumed_receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    grant.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(grant, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(
            descriptor,
            (json.dumps(durable, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    consumed = receipt.with_name(receipt.name + ".consumed")
    os.replace(receipt, consumed)
    return durable


def load_durable_grant(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve(strict=True)
    if target.stat().st_mode & 0o077:
        raise PermissionError("durable activation grant must not be group/world accessible")
    payload = json.loads(target.read_text(encoding="utf-8"))
    expected = hashlib.sha256(ACTIVATION_PHRASE.encode("utf-8")).hexdigest()
    if payload.get("grant_version") != GRANT_VERSION:
        raise ValueError("durable activation grant version is invalid")
    if payload.get("authorization_phrase_sha256") != expected:
        raise PermissionError("durable activation grant authorization hash is invalid")
    activated = datetime.fromisoformat(
        str(payload["activation_timestamp"]).replace("Z", "+00:00")
    )
    if activated.tzinfo is None:
        raise ValueError("grant activation timestamp must be timezone-aware")
    return payload


def _load_manifest(runtime: Phase2Runtime, grant: dict[str, Any]) -> Phase2Manifest:
    row = runtime.repository.db.execute(
        "SELECT payload_json FROM phase2_manifests WHERE manifest_hash=?",
        (grant["manifest_hash"],),
    ).fetchone()
    if row is None:
        raise PermissionError("durable grant manifest is absent from the database")
    manifest = Phase2Manifest.model_validate_json(row["payload_json"])
    for key in ("phase2_epoch_id", "experiment_id", "activation_timestamp"):
        expected = (
            manifest.activation_timestamp.isoformat()
            if key == "activation_timestamp"
            else getattr(manifest, key)
        )
        if str(grant[key]) != expected:
            raise PermissionError(f"durable grant {key} differs from the manifest")
    return manifest


def _activate_runtime(runtime: Phase2Runtime, manifest: Phase2Manifest) -> None:
    """Apply the already-persisted authorization only in process memory."""
    config = runtime.config.model_copy(
        update={"evidence_collection_enabled": True, "activation_authorized": True}
    )
    config.assert_activation(manifest.authorization_phrase)
    object.__setattr__(runtime, "config", config)
    runtime.pipeline.config = config
    runtime.pipeline.llm_runner.config = config
    runtime.pipeline.assert_active_manifest(manifest)


def wait_for_data_readiness(
    runtime: Phase2Runtime,
    *,
    worker_started_at: datetime,
    stop: threading.Event,
    timeout_seconds: float = 120.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and not stop.wait(1.0):
        connected = runtime.repository.db.execute(
            "SELECT occurred_at FROM health_events "
            "WHERE component='websocket' AND status='CONNECTED' AND occurred_at>=? "
            "ORDER BY occurred_at DESC LIMIT 1",
            (worker_started_at.isoformat(),),
        ).fetchone()
        context = runtime.repository.db.execute(
            "SELECT source_timestamp FROM raw_market_observations "
            "WHERE symbol='HYPE' ORDER BY source_timestamp DESC LIMIT 1"
        ).fetchone()
        if connected is not None and context is not None:
            observed = datetime.fromisoformat(
                context["source_timestamp"].replace("Z", "+00:00")
            ).astimezone(UTC)
            if max(0.0, (datetime.now(UTC) - observed).total_seconds()) <= 120.0:
                return
    raise RuntimeError("Phase 2 data-readiness gate was not satisfied")


def run_worker(args: argparse.Namespace) -> None:
    worker_started_at = datetime.now(UTC)
    stop = threading.Event()
    prior_term = signal.signal(signal.SIGTERM, lambda *_: stop.set())
    prior_int = signal.signal(signal.SIGINT, lambda *_: stop.set())
    runtime: Phase2Runtime | None = None
    thread: threading.Thread | None = None
    try:
        grant = load_durable_grant(args.grant)
        if grant["phase2_epoch_id"] != args.epoch_id:
            raise PermissionError("worker epoch differs from durable activation grant")
        runtime = build_phase2_runtime(
            workspace_root=args.workspace,
            experiment_id=args.epoch_id,
            config_path=args.config,
            git_commit_hash=args.expected_commit,
            database_path=args.database,
            allowed_data_root=args.data_root,
            writer_lock_path=args.worker_lease,
        )
        manifest = _load_manifest(runtime, grant)
        _activate_runtime(runtime, manifest)
        websocket = ResilientWebsocketCollector(
            runtime.repository.core, runtime.pipeline.collector
        )
        thread = threading.Thread(
            target=websocket.run_forever,
            args=(stop,),
            name="phase2-market-data-websocket",
            daemon=True,
        )
        thread.start()
        wait_for_data_readiness(
            runtime,
            worker_started_at=worker_started_at,
            stop=stop,
            timeout_seconds=args.readiness_timeout,
        )
        asyncio.run(
            schedule_phase2_forever(
                runtime.pipeline,
                manifest=manifest,
                stop=stop,
            )
        )
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=10.0)
        if runtime is not None:
            runtime.close()
        signal.signal(signal.SIGTERM, prior_term)
        signal.signal(signal.SIGINT, prior_int)


def append_supervisor_event(path: Path, event: SupervisorEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        line = json.dumps(asdict(event), sort_keys=True, separators=(",", ":")) + "\n"
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_supervisor(args: argparse.Namespace) -> None:
    worker = [
        args.worker_executable,
        "--workspace",
        str(args.workspace),
        "--config",
        str(args.config),
        "--epoch-id",
        args.epoch_id,
        "--expected-commit",
        args.expected_commit,
        "--database",
        str(args.database),
        "--data-root",
        str(args.data_root),
        "--worker-lease",
        str(args.worker_lease),
        "--grant",
        str(args.grant),
    ]
    instance = SingleWriterSupervisor(
        command=worker,
        cwd=args.workspace,
        supervisor_lease=ExclusiveProcessLease(
            args.supervisor_lease,
            role="phase2-supervisor",
            epoch_id=args.epoch_id,
        ),
        event_sink=lambda event: append_supervisor_event(args.event_log, event),
        restart_delay_seconds=args.restart_delay,
        stdout_path=args.stdout_log,
        stderr_path=args.stderr_log,
        worker_lease_path=args.worker_lease,
    )
    instance.run()


def worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hype-autopilot-phase2-worker")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--worker-lease", type=Path, required=True)
    parser.add_argument("--grant", type=Path, required=True)
    parser.add_argument("--readiness-timeout", type=float, default=120.0)
    return parser


def supervisor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hype-autopilot-phase2-supervisor")
    parser.add_argument("--worker-executable", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--worker-lease", type=Path, required=True)
    parser.add_argument("--supervisor-lease", type=Path, required=True)
    parser.add_argument("--grant", type=Path, required=True)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("--stdout-log", type=Path, required=True)
    parser.add_argument("--stderr-log", type=Path, required=True)
    parser.add_argument("--restart-delay", type=float, default=15.0)
    return parser


def worker_main() -> None:
    run_worker(worker_parser().parse_args())


def supervisor_main() -> None:
    run_supervisor(supervisor_parser().parse_args())


def authorization_main() -> None:
    parser = argparse.ArgumentParser(prog="hype-autopilot-phase2-authorize")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--grant", type=Path, required=True)
    args = parser.parse_args()
    durable = consume_single_use_authorization(args.receipt, args.grant)
    print(json.dumps({"grant_created": str(args.grant), "grant": durable}, sort_keys=True))


if __name__ == "__main__":
    worker_main()
