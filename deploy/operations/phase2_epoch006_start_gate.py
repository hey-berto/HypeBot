#!/opt/hypebot/phase2/.venv/bin/python
"""Epoch006 entrypoint for the reviewed read-only Phase 2 start gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase2_epoch005_start_gate import (
    check_identity,
    check_network_path,
    parser,
)

if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--network-only"]:
            result = {"status": "PASS", **check_network_path()}
        else:
            result = check_identity(parser().parse_args())
        print(json.dumps(result, sort_keys=True))
    except Exception as error:  # noqa: BLE001 - every gate failure is fatal
        print(
            f"phase2 epoch006 startup gate failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
