from pathlib import Path

from hype_autopilot.phase3.deployment import (
    load_deployment_manifest_template,
    load_deployment_parity_contract,
)

ROOT = Path(__file__).resolve().parents[1]


def test_deployment_contract_is_testnet_only_design_and_frozen_before_results():
    contract = load_deployment_parity_contract(
        ROOT / "config" / "phase3" / "deployment_parity_v1.yaml"
    )
    assert contract.status == "DESIGN_ONLY_NOT_EXECUTABLE"
    assert contract.network == "testnet"
    assert contract.minimum_consecutive_executions == 20
    assert contract.maximum_unresolved_reconciliation_mismatches == 0
    assert contract.protective_exit_confirmation_required is True
    assert contract.maximum_silent_partial_fill_or_cancel_divergences == 0
    assert contract.formal_testnet_sample_authorized is False
    assert contract.mainnet_capability is False
    assert len(contract.contract_hash) == 64


def test_deployment_manifest_template_cannot_authorize_trading():
    template = load_deployment_manifest_template(
        ROOT / "config" / "phase3" / "deployment_manifest_template_v1.yaml"
    )
    assert template.status == "UNPOPULATED_DESIGN_ONLY"
    assert template.network_identity == "testnet"
    assert template.permission_model_identity == "TESTNET_TRADE_ONLY_NO_WITHDRAWAL"
    assert template.formal_testnet_parity_authorized is False
    assert template.live_mainnet_authorized is False
    assert len(template.template_hash) == 64
