from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import socket
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hype_autopilot.hashing import canonical_json

ALERT_VERSION = "HYPEBOT_OPERATIONAL_ALERT_V1"
ALLOWED_CLASSIFICATIONS = frozenset(
    {
        "DATABASE_INTEGRITY_FAILED",
        "EVIDENCE_AUDIT_FAILED",
        "IDENTITY_DRIFT",
        "RECOVERY_FAILED",
        "RESTART_LOOP",
        "SCHEDULER_FATAL",
        "SERVICE_FAILED",
        "SUPERVISOR_FATAL",
        "WRITER_LOCK_FAILURE",
    }
)


def _atomic_write(path: Path, payload: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(descriptor, payload.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(descriptor, (canonical_json(payload) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_operational_alert(
    *,
    component: str,
    classification: str,
    observed_at: datetime,
    hostname: str,
) -> dict[str, Any]:
    """Build an allowlisted infrastructure-only alert payload.

    Callers cannot supply arbitrary details, which prevents secrets, model output,
    research outcomes, or performance fields from entering the alert channel.
    """
    if classification not in ALLOWED_CLASSIFICATIONS:
        raise ValueError("classification is not eligible for an operational alert")
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if not component or len(component) > 160 or any(ch.isspace() for ch in component):
        raise ValueError("component must be a compact systemd/runtime identity")
    core = {
        "alert_version": ALERT_VERSION,
        "classification": classification,
        "component": component,
        "hostname": hostname,
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "severity": "FATAL",
    }
    return {
        **core,
        "alert_id": hashlib.sha256(canonical_json(core).encode("utf-8")).hexdigest(),
    }


def _webhook_sender(
    url: str, payload: dict[str, Any], bearer_token: str | None, timeout: float
) -> None:
    parsed = urlparse(url)
    local_acceptance = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    if (parsed.scheme != "https" and not local_acceptance) or parsed.username:
        raise ValueError("alert endpoint must be HTTPS or an isolated loopback test")
    headers = {"Content-Type": "application/json", "User-Agent": "hypebot-alert/1"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(
        url,
        data=(canonical_json(payload) + "\n").encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"alert endpoint returned HTTP {response.status}")


def dispatch_operational_alert(
    *,
    component: str,
    classification: str,
    audit_log: str | Path,
    state_path: str | Path,
    webhook_url: str | None,
    bearer_token: str | None = None,
    cooldown_seconds: int = 900,
    require_delivery: bool = True,
    observed_at: datetime | None = None,
    hostname: str | None = None,
    sender: Callable[[str, dict[str, Any], str | None, float], None] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Deliver one debounced fatal operational alert and append a local audit row."""
    if cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be non-negative")
    now = (observed_at or datetime.now(UTC)).astimezone(UTC)
    payload = build_operational_alert(
        component=component,
        classification=classification,
        observed_at=now,
        hostname=hostname or socket.gethostname(),
    )
    state = Path(state_path)
    audit = Path(audit_log)
    lock_path = state.with_suffix(state.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        prior: dict[str, str] = {}
        if state.exists():
            prior = json.loads(state.read_text(encoding="utf-8"))
        key = f"{component}:{classification}"
        previous = prior.get(key)
        if previous is not None:
            previous_at = datetime.fromisoformat(previous).astimezone(UTC)
            if (now - previous_at).total_seconds() < cooldown_seconds:
                result = {**payload, "delivery": "SUPPRESSED_COOLDOWN"}
                _append_jsonl(audit, result)
                return result

        if not webhook_url:
            result = {**payload, "delivery": "FAILED_NO_ENDPOINT"}
            _append_jsonl(audit, result)
            if require_delivery:
                raise RuntimeError("operational alert endpoint is not configured")
            return result

        deliver = sender or _webhook_sender
        try:
            deliver(webhook_url, payload, bearer_token, timeout_seconds)
        except Exception as exc:
            result = {
                **payload,
                "delivery": "FAILED",
                "error_class": type(exc).__name__,
            }
            _append_jsonl(audit, result)
            raise RuntimeError("operational alert delivery failed") from exc
        prior[key] = now.isoformat()
        _atomic_write(state, canonical_json(prior) + "\n")
        result = {**payload, "delivery": "DELIVERED"}
        _append_jsonl(audit, result)
        return result
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hype-autopilot-alert")
    parser.add_argument("--component", required=True)
    parser.add_argument(
        "--classification", choices=sorted(ALLOWED_CLASSIFICATIONS), required=True
    )
    parser.add_argument("--audit-log", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cooldown-seconds", type=int, default=900)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--allow-local-only", action="store_true")
    args = parser.parse_args(argv)
    result = dispatch_operational_alert(
        component=args.component,
        classification=args.classification,
        audit_log=args.audit_log,
        state_path=args.state,
        webhook_url=os.environ.get("HYPEBOT_ALERT_WEBHOOK_URL"),
        bearer_token=os.environ.get("HYPEBOT_ALERT_BEARER_TOKEN"),
        cooldown_seconds=args.cooldown_seconds,
        require_delivery=not args.allow_local_only,
        timeout_seconds=args.timeout_seconds,
    )
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
