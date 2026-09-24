#!/opt/hypebot/phase2/.venv/bin/python
"""Epoch006 worker wrapper using the reviewed per-provider-call VPN guard."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase2_epoch005_runtime_worker import (
    install_runtime_guard,
)
from phase2_epoch006_start_gate import check_network_path

TELEMETRY_PATH = Path("/var/log/hypebot/phase2-runtime-network.jsonl")


def append_telemetry(
    status: str, *, details: dict[str, Any], path: Path = TELEMETRY_PATH
) -> None:
    record = {
        "component": "phase2_epoch006_runtime_network_guard",
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


def main() -> None:
    install_runtime_guard(
        network_check=check_network_path,
        telemetry=append_telemetry,
    )
    from hype_autopilot.phase2.service import run_worker, worker_parser

    run_worker(worker_parser().parse_args())


if __name__ == "__main__":
    main()
