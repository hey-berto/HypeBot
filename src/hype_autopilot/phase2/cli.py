from __future__ import annotations

import argparse
import json
from pathlib import Path

from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.acceptance import (
    run_real_provider_canary,
    run_scheduler_acceptance,
)
from hype_autopilot.phase2.config import (
    file_sha256,
    load_phase2_config,
    resolve_inside_workspace,
)
from hype_autopilot.phase2.isolation import validate_phase2_database_path
from hype_autopilot.phase2.provider import output_json_schema
from hype_autopilot.phase2.storage import phase2_database_schema_hash


def build_status(config_path: Path, workspace: Path) -> dict[str, object]:
    config, digest = load_phase2_config(config_path)
    config.assert_build_only()
    database = validate_phase2_database_path(config.database_path, workspace)
    prompt = resolve_inside_workspace(config.prompt_path, workspace)
    return {
        "phase2_epoch_id": config.phase2_epoch_id,
        "status": config.status,
        "config_hash": digest,
        "database_path": str(database),
        "prompt_exists": prompt.is_file(),
        "prompt_version": config.prompt_version,
        "prompt_hash": file_sha256(prompt),
        "model": config.model,
        "model_version": config.model_version,
        "output_schema_version": config.output_schema_version,
        "output_schema_hash": sha256_canonical(
            output_json_schema(config.output_schema_version)
        ),
        "database_schema_hash": phase2_database_schema_hash(),
        "evidence_collection_enabled": config.evidence_collection_enabled,
        "activation_authorized": config.activation_authorized,
        "safe_to_build_test": True,
        "scored_collection_started": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2 build-only validation commands"
    )
    parser.add_argument(
        "command",
        choices=["build-status", "non-scored-canary", "scheduler-acceptance"],
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config/phase2/phase2_epoch_001.yaml")
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    if args.command == "build-status":
        result = build_status(args.config, args.workspace)
    elif args.command == "non-scored-canary":
        result = run_real_provider_canary(
            workspace=args.workspace,
            config_path=args.config,
            database_path=args.database
            or Path("data/phase2/non_scored_canary.sqlite3"),
        )
    else:
        result = run_scheduler_acceptance(
            workspace=args.workspace,
            config_path=args.config,
            database_path=args.database
            or Path("data/phase2/scheduler_acceptance.sqlite3"),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
