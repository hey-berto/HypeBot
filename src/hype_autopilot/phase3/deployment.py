from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hype_autopilot.hashing import sha256_canonical


class ParityTolerances(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intended_vs_observed_quantity_relative: float = Field(gt=0)
    intended_vs_observed_quantity_absolute_lots: int = Field(ge=0)
    median_entry_slippage_error_bps: float = Field(ge=0)
    p95_entry_slippage_error_bps: float = Field(ge=0)
    maximum_single_entry_slippage_error_bps: float = Field(ge=0)
    fee_variance_bps_of_notional: float = Field(ge=0)
    funding_variance_bps_of_notional_per_settlement: float = Field(ge=0)
    p95_intent_to_confirmed_exchange_state_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_slippage_order(self) -> ParityTolerances:
        if not (
            self.median_entry_slippage_error_bps
            <= self.p95_entry_slippage_error_bps
            <= self.maximum_single_entry_slippage_error_bps
        ):
            raise ValueError("slippage tolerances must be monotonic")
        return self


class DeploymentParityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["DEPLOYMENT_PARITY_CONTRACT_V1_PROPOSAL"]
    status: Literal["DESIGN_ONLY_NOT_EXECUTABLE"]
    network: Literal["testnet"]
    minimum_consecutive_executions: int = Field(ge=20)
    maximum_unresolved_reconciliation_mismatches: Literal[0]
    protective_exit_confirmation_required: Literal[True]
    maximum_silent_partial_fill_or_cancel_divergences: Literal[0]
    tolerances: ParityTolerances
    required_measurements: tuple[str, ...]
    identity_fields: tuple[str, ...]
    permission_model: Literal["TRADE_ONLY_NO_WITHDRAWAL_PROPOSED"]
    formal_testnet_sample_authorized: Literal[False]
    mainnet_capability: Literal[False]

    @property
    def contract_hash(self) -> str:
        return sha256_canonical(self)


class DeploymentManifestTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: Literal["HYPE_DEPLOYMENT_MANIFEST_V1_PROPOSAL"]
    status: Literal["UNPOPULATED_DESIGN_ONLY"]
    network_identity: Literal["testnet"]
    strategy_commit: str
    strategy_config_hash: str
    feature_schema_identity: str
    snapshot_schema_identity: str
    llm_identity: Mapping[str, Any]
    simulator_version: str
    risk_policy_hash: str
    execution_policy_hash: str
    hyperliquid_adapter_version: str
    hyperliquid_sdk_version: str
    permission_model_identity: Literal["TESTNET_TRADE_ONLY_NO_WITHDRAWAL"]
    stop_tp_reduce_only_policy_version: str
    deployment_artifact_hash: str
    formal_testnet_parity_authorized: Literal[False]
    live_mainnet_authorized: Literal[False]

    @property
    def template_hash(self) -> str:
        return sha256_canonical(self)


def load_deployment_parity_contract(path: str | Path) -> DeploymentParityContract:
    return DeploymentParityContract.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )


def load_deployment_manifest_template(path: str | Path) -> DeploymentManifestTemplate:
    return DeploymentManifestTemplate.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )
