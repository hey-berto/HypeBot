from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hype_autopilot.phase3.deployment import (
    load_deployment_manifest_template,
    load_deployment_parity_contract,
)
from hype_autopilot.phase3.risk import (
    RiskReceiptRepository,
    connect_risk_store,
    load_risk_policy,
    risk_schema_hash,
)
from hype_autopilot.phase3.signal_lab import (
    SignalLabRepository,
    connect_signal_lab,
    load_batch_manifest,
    signal_lab_schema_hash,
)


def initialize_frozen_infrastructure(
    root: str | Path,
    *,
    implementation_commit: str,
    registered_at: datetime,
) -> Mapping[str, Any]:
    """Create only the isolated manifests/policies; never execute a Lab run."""
    project_root = Path(root).resolve()
    if len(implementation_commit) != 40 or any(
        character not in "0123456789abcdef" for character in implementation_commit
    ):
        raise ValueError("implementation_commit must be a lowercase 40-character SHA")
    if registered_at.tzinfo is None or registered_at.utcoffset() != UTC.utcoffset(
        registered_at
    ):
        raise ValueError("registered_at must be UTC")

    config_root = project_root / "config" / "phase3"
    manifest = load_batch_manifest(config_root / "candidate_signal_lab_batch1.yaml")
    policy = load_risk_policy(config_root / "risk_policy_v1.yaml")
    parity = load_deployment_parity_contract(config_root / "deployment_parity_v1.yaml")
    deployment_template = load_deployment_manifest_template(
        config_root / "deployment_manifest_template_v1.yaml"
    )

    lab_path = project_root / "data" / "phase3_signal_lab" / "lab.sqlite3"
    risk_path = project_root / "data" / "phase3_risk" / "risk.sqlite3"
    lab_db = connect_signal_lab(lab_path, project_root)
    risk_db = connect_risk_store(risk_path, project_root)
    try:
        lab = SignalLabRepository(lab_db)
        risk = RiskReceiptRepository(risk_db)
        lab.register_manifest(
            manifest,
            registered_at=registered_at,
            implementation_commit=implementation_commit,
        )
        risk.register_policy(policy)
        lab_runs = int(lab_db.execute("SELECT COUNT(*) FROM lab_runs").fetchone()[0])
        risk_receipts = int(
            risk_db.execute("SELECT COUNT(*) FROM risk_receipts").fetchone()[0]
        )
        if lab_runs != 0 or risk_receipts != 0:
            raise RuntimeError(
                "initialization cannot reuse databases containing results"
            )
        return {
            "implementation_commit": implementation_commit,
            "registered_at": registered_at.astimezone(UTC).isoformat(),
            "batch_status": manifest.status,
            "batch_manifest_hash": manifest.manifest_hash,
            "lab_schema_hash": signal_lab_schema_hash(),
            "lab_database": str(lab_path),
            "lab_integrity": lab.integrity(),
            "lab_run_count": lab_runs,
            "risk_policy_hash": policy.policy_hash,
            "risk_schema_hash": risk_schema_hash(),
            "risk_database": str(risk_path),
            "risk_integrity": risk.integrity(),
            "risk_receipt_count": risk_receipts,
            "deployment_parity_contract_hash": parity.contract_hash,
            "deployment_manifest_template_hash": deployment_template.template_hash,
            "stage1_executed": False,
            "testnet_authorized": parity.formal_testnet_sample_authorized,
            "mainnet_authorized": parity.mainnet_capability,
        }
    finally:
        lab_db.close()
        risk_db.close()
