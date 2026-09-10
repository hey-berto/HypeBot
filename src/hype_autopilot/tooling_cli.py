from __future__ import annotations

import argparse
import json
from pathlib import Path

from hype_autopilot.migration import inspect_runtime_identity, write_identity_json
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
