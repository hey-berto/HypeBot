#!/usr/bin/env python3
"""One-call, non-scored Ubuntu preflight for the prospective strict V2 contract.

This does not create or open a scored database. The caller must provide a new,
isolated output directory and the reviewed candidate's exact identities.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator

from hype_autopilot.data.models import ObservationClass
from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.acceptance import synthetic_acceptance_snapshot
from hype_autopilot.phase2.audit import validate_raw_provider_plaintext
from hype_autopilot.phase2.config import file_sha256, load_phase2_config
from hype_autopilot.phase2.models import LLMStructuredOutputV2
from hype_autopilot.phase2.provider import openai_provider_from_config, output_json_schema
from hype_autopilot.phase2.runner import validate_geometry
from hype_autopilot.snapshots.canonicalize import freeze_snapshot


def _write_exclusive(path: Path, content: bytes) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-config-hash", required=True)
    parser.add_argument("--expected-prompt-hash", required=True)
    parser.add_argument("--expected-schema-hash", required=True)
    args = parser.parse_args()
    root = args.workspace.resolve(strict=True)
    output_dir = args.output_dir.resolve(strict=True)
    if output_dir == root or root in output_dir.parents:
        raise RuntimeError("preflight output must be outside the source checkout")
    if any(output_dir.iterdir()):
        raise RuntimeError("preflight output directory must be empty")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OpenAI credential is unavailable")

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    tracked_changes = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()
    config, config_hash = load_phase2_config(root / "config/phase2/phase2_epoch_003.yaml")
    config.assert_build_only()
    prompt_path = root / config.prompt_path
    prompt_hash = file_sha256(prompt_path)
    schema = output_json_schema(config.output_schema_version)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    schema_hash = sha256_canonical(schema)
    if (
        commit != args.expected_commit
        or tracked_changes
        or config_hash != args.expected_config_hash
        or prompt_hash != args.expected_prompt_hash
        or schema_hash != args.expected_schema_hash
        or config.phase2_epoch_id != "phase2_epoch_003"
        or config.model != "gpt-5.6-terra"
        or config.model_version != "gpt-5.6-terra"
        or config.reasoning_effort != "medium"
        or config.output_schema_version != "LLM_OUTPUT_V2"
        or config.tools_allowed
        or not config.information_boundary.snapshot_only
        or any((
            config.information_boundary.web_browsing,
            config.information_boundary.tools,
            config.information_boundary.live_search,
            config.information_boundary.external_market_lookup,
            config.information_boundary.connectors,
            config.information_boundary.post_snapshot_data,
        ))
    ):
        raise RuntimeError("preflight frozen identity or information boundary mismatch")

    at = datetime.now(UTC)
    snapshot = synthetic_acceptance_snapshot(
        at, snapshot_id="NON_SCORED_epoch003_provider_preflight", epoch_id=config.phase2_epoch_id
    )
    snapshot = freeze_snapshot(snapshot.model_copy(update={
        "observation_class": ObservationClass.SOAK, "snapshot_hash": None
    }))
    assert snapshot.snapshot_hash is not None
    provider = openai_provider_from_config(config, workspace_root=str(root))
    report: dict[str, object] = {
        "audit_class": "NON_SCORED_PROVIDER_PREFLIGHT",
        "permanently_non_scored": True,
        "source_commit": commit,
        "epoch_id": config.phase2_epoch_id,
        "config_hash": config_hash,
        "prompt_hash": prompt_hash,
        "output_schema_hash": schema_hash,
        "snapshot_hash": snapshot.snapshot_hash,
        "snapshot_observation_class": snapshot.observation_class.value,
        "model": config.model,
        "model_version": config.model_version,
        "reasoning_effort": config.reasoning_effort,
        "tools_requested": 0,
        "request_attempts": 1,
        "retry_count": 0,
        "scored_database_opened": False,
        "scored_evidence_written": False,
    }
    try:
        response = provider.invoke(
            prompt=prompt_path.read_text(encoding="utf-8"),
            snapshot_json=canonical_json(snapshot),
            timeout_seconds=config.request_timeout_seconds,
        )
        report["provider_http_success"] = True  # invoke raises for non-2xx responses
        report["request_started_at"] = response.request_started_at.isoformat()
        report["request_ended_at"] = response.request_ended_at.isoformat()
        report["latency_ms"] = int(
            (response.request_ended_at - response.request_started_at).total_seconds() * 1000
        )
        report["response_model"] = response.model
        report["response_model_version"] = response.model_version
        report["input_tokens"] = response.input_tokens
        report["cached_input_tokens"] = response.cached_input_tokens
        report["output_tokens"] = response.output_tokens
        report["cost_usd"] = response.cost_usd
        report["tool_calls_count"] = response.tool_calls_count
        validate_raw_provider_plaintext(response.raw_output)
        raw_bytes = response.raw_output.encode("utf-8")
        _write_exclusive(output_dir / "raw_provider_response.txt", raw_bytes)
        raw_hash = sha256(raw_bytes).hexdigest()
        if sha256((output_dir / "raw_provider_response.txt").read_bytes()).hexdigest() != raw_hash:
            raise RuntimeError("persisted raw response hash mismatch")
        report["raw_response_sha256"] = raw_hash
        report["raw_response_bytes"] = len(raw_bytes)
        parsed_json = json.loads(response.raw_output)
        report["transport_schema_valid"] = validator.is_valid(parsed_json)
        parsed = LLMStructuredOutputV2.model_validate(parsed_json)
        report["local_schema_valid"] = True
        validate_geometry(parsed, snapshot)
        report["geometry_valid"] = True
        report["snapshot_lineage_valid"] = parsed.input_snapshot_hash == snapshot.snapshot_hash
        report["output_schema_version"] = parsed.output_schema_version
        if not all((
            report["transport_schema_valid"],
            report["snapshot_lineage_valid"],
            response.model == config.model,
            response.model_version == config.model_version,
            response.tool_calls_count == 0,
            response.request_started_at >= snapshot.snapshot_timestamp,
            (response.request_ended_at - snapshot.snapshot_timestamp).total_seconds()
            <= config.snapshot_to_call_staleness_seconds,
        )):
            raise RuntimeError("provider-output identity, tool or causality gate failed")
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error_class"] = type(exc).__name__
        report["error_message"] = str(exc)[:400]
    _write_exclusive(
        output_dir / "result.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    )
    print(json.dumps({k: v for k, v in report.items() if k != "error_message"}, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
