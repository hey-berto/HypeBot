"""Wall-clock, externally supervised, permanently non-scored Phase 2 soak."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hype_autopilot.phase2.scheduler import planned_phase2_boundary
from hype_autopilot.phase2.supervision import (
    ExclusiveProcessLease,
    SingleWriterSupervisor,
    SupervisorEvent,
)
from tests.phase2_recovery_harness import create_case, inspect_case, run_worker

ROOT = Path(__file__).resolve().parents[1]
MINIMUM_BOUNDARIES = 24
MINIMUM_CONTINUOUS_DURATION = timedelta(hours=6)


def acceptance_threshold_met(
    *, prepared_at: datetime, observed_at: datetime, completed_boundaries: int
) -> bool:
    """The pre-cutover soak must satisfy both conservative lower bounds."""
    return (
        completed_boundaries >= MINIMUM_BOUNDARIES
        and observed_at - prepared_at >= MINIMUM_CONTINUOUS_DURATION
    )


def append(path: Path, value: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def prepare(case: Path, boundaries: int) -> None:
    if boundaries < MINIMUM_BOUNDARIES:
        raise ValueError(
            "live soak requires at least 24 consecutive scheduled boundaries"
        )
    first = planned_phase2_boundary(datetime.now(UTC))
    prepared_at = datetime.now(UTC)
    metadata = create_case(case, first_boundary=first)
    (case / "LIVE_SOAK.json").write_text(
        json.dumps(
            {
                "audit_class": "LIVE_NON_SCORED_PHASE2_RECOVERY_SOAK",
                "permanently_non_scored": True,
                "provider": "deterministic_offline_no_network",
                "first_boundary": first.isoformat(),
                "boundary_count": boundaries,
                "prepared_at": prepared_at.isoformat(),
                "minimum_acceptance_boundaries": MINIMUM_BOUNDARIES,
                "minimum_continuous_seconds": int(
                    MINIMUM_CONTINUOUS_DURATION.total_seconds()
                ),
                "required_invariants": [
                    "ZERO_DUPLICATES",
                    "ZERO_UNEXPLAINED_MISSING_BOUNDARIES",
                    "ZERO_FOREIGN_KEY_VIOLATIONS",
                    "SQLITE_QUICK_AND_INTEGRITY_OK",
                    "EXACTLY_ONE_EFFECTIVE_WRITER",
                    "SUPERVISOR_AND_SCHEDULER_CONTINUOUSLY_HEALTHY",
                    "ALL_APPLICABLE_LLM_OUTPUT_V2_VALID",
                    "ALL_RAW_RESPONSE_HASHES_MATCH",
                    "BOUNDED_RETRY_AND_BACKOFF",
                    "NO_UNEXPLAINED_PROCESS_DEATH_OR_RESTART_LOOP",
                ],
                "base_metadata": metadata,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def worker(case: Path) -> None:
    plan = json.loads((case / "LIVE_SOAK.json").read_text())
    first = datetime.fromisoformat(plan["first_boundary"])
    count = int(plan["boundary_count"])
    trace_path = case / "live-soak-trace.jsonl"
    with ExclusiveProcessLease(
        case / "live-soak-writer.lock",
        role="phase2-writer",
        epoch_id="LIVE_NON_SCORED_PHASE2_RECOVERY_SOAK",
    ):
        completed = (
            len(trace_path.read_text().splitlines()) if trace_path.exists() else 0
        )
        for index in range(completed, count):
            boundary = first + timedelta(minutes=15 * index)
            delay = max(
                0.0,
                (boundary + timedelta(seconds=2) - datetime.now(UTC)).total_seconds(),
            )
            if delay:
                time.sleep(delay)
            result = run_worker(case, index)
            append(
                trace_path,
                {
                    "wall_clock_observed_at": datetime.now(UTC).isoformat(),
                    "logical_boundary": boundary.isoformat(),
                    "result": result,
                },
            )
        prepared_at = datetime.fromisoformat(plan["prepared_at"])
        remaining = (
            prepared_at + MINIMUM_CONTINUOUS_DURATION - datetime.now(UTC)
        ).total_seconds()
        if remaining > 0:
            time.sleep(remaining)
        final = inspect_case(case)
        final["soak_acceptance"] = {
            "threshold_met": acceptance_threshold_met(
                prepared_at=prepared_at,
                observed_at=datetime.now(UTC),
                completed_boundaries=len(final["cycles"]),
            ),
            "minimum_boundaries": MINIMUM_BOUNDARIES,
            "minimum_continuous_seconds": int(
                MINIMUM_CONTINUOUS_DURATION.total_seconds()
            ),
        }
        (case / "LIVE_SOAK_COMPLETE.json").write_text(
            json.dumps(final, indent=2, sort_keys=True) + "\n"
        )


def supervisor(case: Path) -> None:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def sink(event: SupervisorEvent) -> None:
        append(case / "live-soak-supervisor-events.jsonl", asdict(event))

    instance = SingleWriterSupervisor(
        command=(sys.executable, str(Path(__file__).resolve()), "worker", str(case)),
        cwd=case,
        supervisor_lease=ExclusiveProcessLease(
            case / "live-soak-supervisor.lock",
            role="phase2-supervisor",
            epoch_id="LIVE_NON_SCORED_PHASE2_RECOVERY_SOAK",
        ),
        event_sink=sink,
        environment=environment,
        restart_delay_seconds=5.0,
    )
    instance.run()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "worker", "supervisor"))
    parser.add_argument("case", type=Path)
    parser.add_argument("--boundaries", type=int, default=3)
    args = parser.parse_args()
    case = args.case.resolve()
    if args.mode == "prepare":
        prepare(case, args.boundaries)
    elif args.mode == "worker":
        worker(case)
    else:
        supervisor(case)


if __name__ == "__main__":
    main()
