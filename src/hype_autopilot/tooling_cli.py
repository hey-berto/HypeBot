from __future__ import annotations

import argparse
import json
from pathlib import Path

from hype_autopilot.migration import inspect_runtime_identity, write_identity_json
from hype_autopilot.phase2.operations import (
    phase2_operational_health,
    sqlite_consistent_backup,
    verify_backup,
)
from hype_autopilot.platform_replay import platform_replay_json
from hype_autopilot.review_bundle import load_review_bundle_request, write_review_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hype-autopilot-tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    bundle = sub.add_parser("review-bundle")
    bundle.add_argument("--repo", default=".")
    bundle.add_argument("--request", required=True)
    bundle.add_argument("--output", required=True)

    identity = sub.add_parser("runtime-identity")
    identity.add_argument("--repo", required=True)
    identity.add_argument("--expected-commit", required=True)
    identity.add_argument("--config", required=True)
    identity.add_argument("--database", required=True)
    identity.add_argument("--output")

    replay = sub.add_parser("platform-replay")
    replay.add_argument("--root", default=".")
    replay.add_argument(
        "--fixture", default="config/migration/platform_replay_fixture_v1.yaml"
    )
    replay.add_argument("--output")

    health = sub.add_parser("phase2-health")
    health.add_argument("--database", required=True)
    health.add_argument("--worker-lease", required=True)
    health.add_argument("--supervisor-lease", required=True)
    health.add_argument("--maximum-boundary-age-minutes", type=float, default=20.0)

    backup = sub.add_parser("sqlite-backup")
    backup.add_argument("--source", required=True)
    backup.add_argument("--destination", required=True)
    backup.add_argument("--manifest")

    verify = sub.add_parser("sqlite-verify-backup")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)

    if args.command == "review-bundle":
        output = write_review_bundle(
            args.repo, load_review_bundle_request(args.request), args.output
        )
        print(output)
    elif args.command == "runtime-identity":
        result = inspect_runtime_identity(
            args.repo,
            expected_commit=args.expected_commit,
            config_path=args.config,
            database_path=args.database,
        )
        if args.output:
            print(write_identity_json(result, args.output))
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "platform-replay":
        result = platform_replay_json(args.root, args.fixture)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result + "\n", encoding="utf-8")
            print(output)
        else:
            print(result)
    elif args.command == "phase2-health":
        result = phase2_operational_health(
            args.database,
            worker_lease=args.worker_lease,
            supervisor_lease=args.supervisor_lease,
            maximum_boundary_age_minutes=args.maximum_boundary_age_minutes,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["healthy"] else 1
    elif args.command == "sqlite-backup":
        result = sqlite_consistent_backup(args.source, args.destination)
        if args.manifest:
            Path(args.manifest).write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "sqlite-verify-backup":
        print(
            json.dumps(
                verify_backup(args.backup, args.sha256), indent=2, sort_keys=True
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
