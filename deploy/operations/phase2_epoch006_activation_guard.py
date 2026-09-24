#!/opt/hypebot/phase2/.venv/bin/python
"""Small fail-closed helpers for the epoch006 activation orchestrator."""

from __future__ import annotations

import argparse


def validate_monotonic_timer_deadline(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.lower() == "infinity" or normalized == "0":
        raise ValueError("health timer has no finite monotonic next elapse")
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timer-deadline", required=True)
    args = parser.parse_args()
    print(validate_monotonic_timer_deadline(args.timer_deadline))


if __name__ == "__main__":
    main()
