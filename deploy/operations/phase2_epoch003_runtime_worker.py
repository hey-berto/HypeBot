#!/opt/hypebot/phase2/.venv/bin/python
"""Operational per-call VPN assertion for the epoch003 production worker.

This wrapper is deployed from the root-owned operations checkout, rather than
the frozen research checkout.  It replaces no route, changes no relay, and
never retries or falls back to a direct/provider path.  A failed assertion is
written to the operational log and prevents the underlying provider method
from being entered.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hype_autopilot.phase2.provider import ProviderError

sys.path.insert(0, str(Path(__file__).resolve().parent))
check_network_path = importlib.import_module(
    "phase2_epoch003_start_gate"
).check_network_path


TELEMETRY_PATH = Path("/var/log/hypebot/phase2-runtime-network.jsonl")


class RuntimePathBlocked(ProviderError):
    """The provider must not run because its approved path cannot be proved."""


def append_telemetry(
    status: str, *, details: dict[str, Any], path: Path = TELEMETRY_PATH
) -> None:
    """Append operational-only evidence; failure to record is itself a block."""
    record = {
        "component": "phase2_epoch003_runtime_network_guard",
        "status": status,
        "timestamp": datetime.now(UTC).isoformat(),
        **details,
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        os.write(descriptor, line.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def install_runtime_guard(
    *,
    network_check=check_network_path,
    telemetry=append_telemetry,
) -> None:
    """Wrap the installed provider immediately before each real provider call."""
    from hype_autopilot.phase2.provider import OpenAIResponsesProvider

    original_invoke = OpenAIResponsesProvider.invoke

    def guarded_invoke(self, **kwargs):  # type: ignore[no-untyped-def]
        try:
            path = network_check()
            telemetry("PASS", details=path)
        except BaseException as exc:  # Monitoring uncertainty must fail closed.
            try:
                telemetry(
                    "BLOCKED",
                    details={
                        "error_class": type(exc).__name__,
                        "error": str(exc),
                    },
                )
            except BaseException as telemetry_error:
                raise RuntimePathBlocked(
                    "runtime path validation telemetry failed"
                ) from telemetry_error
            raise RuntimePathBlocked("runtime VPN path validation failed") from exc
        return original_invoke(self, **kwargs)

    # Runner already treats ProviderError as a persisted fail-closed outcome.
    OpenAIResponsesProvider.invoke = guarded_invoke


def main() -> None:
    install_runtime_guard()
    from hype_autopilot.phase2.service import run_worker, worker_parser

    run_worker(worker_parser().parse_args())


if __name__ == "__main__":
    main()
