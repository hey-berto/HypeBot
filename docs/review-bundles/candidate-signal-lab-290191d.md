# HYPE Autopilot code review bundle

## Immutable identity

- Generator: `CODE_REVIEW_BUNDLE_V1` at `74542204ab7c843cf6b2e1013e95aa10a473d8e4`
- Bundle content hash: `0d67fb3ea988eec9ee7041294babaa02abdacb478c428a7f854b73d68bd05f14`
- Repository: `https://github.com/hey-berto/HypeBot`
- Branch: `codex/phase3-analysis-gate`
- Base commit: `50d0c86d7d452e61f6ddf7ea5b70ef12f5005850`
- Reviewed commit: `290191dbe20ef40f83c0d8c240f04c8d61593aac`
- Generated at: `2026-09-06T05:30:00+00:00`
- Change category: `ANALYSIS_TOOLING`

## Review summary

**Purpose:** Add an isolated preregistered HYPE Candidate Signal Lab, deterministic risk receipts, and design-only deployment parity controls.

**Architecture impact:** Adds isolated Phase 3 analysis and safety namespaces; active research runtime paths are not imported or changed.

**Research-defining impact:** NONE for active Phase 1/2; freezes a future separately authorized Batch 1 analysis contract.

**Operational/runtime impact:** NONE; no scheduler, supervisor, wallet, exchange, testnet, or live path is added.

## Changed files

- config/phase3/candidate_signal_lab_batch1.yaml
- config/phase3/deployment_manifest_template_v1.yaml
- config/phase3/deployment_parity_v1.yaml
- config/phase3/risk_policy_v1.yaml
- docs/candidate-signal-lab-source-readiness-audit.md
- docs/candidate-signal-lab.md
- docs/counterfactual-risk-ledger-design.md
- docs/deployment-parity-contract.md
- src/hype_autopilot/phase3/__init__.py
- src/hype_autopilot/phase3/deployment.py
- src/hype_autopilot/phase3/infrastructure.py
- src/hype_autopilot/phase3/risk.py
- src/hype_autopilot/phase3/signal_lab.py
- tests/test_phase3_deployment_design.py
- tests/test_phase3_infrastructure.py
- tests/test_phase3_risk.py
- tests/test_phase3_signal_lab.py

## Config, schema, prompt and model identities

- New Candidate Signal Lab Batch 1 manifest and isolated Lab schema.
- New deterministic RiskReceipt policy and isolated schema.
- New design-only parity contract and deployment-manifest template.
- No active prompt, model, snapshot schema, strategy, simulator, or comparison change.

## Dependencies

- NONE

## Migrations and persistent state

- New ignored isolated Lab DB with one manifest, seven hypotheses and zero runs.
- New ignored isolated Risk DB with one policy and zero receipts.

## Verification

- `PYTHONPATH=src python -m pytest -q` — PASS; 103 passed, 0 failed
- `ruff check src/hype_autopilot/phase3 tests/test_phase3_*.py` — PASS; 1 passed, 0 failed
- `PYTHONPATH=src python -m compileall -q src` — PASS; 1 passed, 0 failed

## Known limitations and unresolved issues

- Historical open-interest coverage requires the official asset-context archive.
- Historical fills require official archive retrieval and proven taker-side semantics.
- Batch 1 has not run; no alpha result exists.

## Security and secrets

- No LLM, API-key, wallet, signing, withdrawal, order-submission, or network execution path.
- Lab and Risk SQLite paths reject active evidence database identities.

The generator scanned its structured request and focused diff for private-key,
provider-key and common token signatures before emitting this artifact. It does
not read `.env` files.

## Focused unified patch

```diff
diff --git a/config/phase3/candidate_signal_lab_batch1.yaml b/config/phase3/candidate_signal_lab_batch1.yaml
new file mode 100644
index 0000000..b037864
--- /dev/null
+++ b/config/phase3/candidate_signal_lab_batch1.yaml
@@ -0,0 +1,143 @@
+lab_version: CANDIDATE_SIGNAL_LAB_V1
+batch_id: HYPE_BATCH_001
+status: FROZEN_NOT_EXECUTED
+data_domain: HISTORICAL_NON_PHASE2_ONLY
+production_epoch_access: PROHIBITED
+llm_calls_allowed: false
+stage2_enabled: false
+normalization:
+  method: trailing_zscore
+  long_window_intervals: 96
+  short_window_intervals: 24
+  event_threshold_abs_z: 1.0
+horizons:
+  primary_minutes: [60, 240]
+  exploratory_minutes: [15, 720]
+split:
+  method: chronological_contiguous
+  fit_fraction: 0.60
+  boundary_rule: floor_n_times_fit_fraction
+  definition_reuse: identical_fit_and_confirmation
+multiple_testing:
+  method: benjamini_hochberg
+  family_id: HYPE_BATCH_001_PRIMARY
+  family_size: 14
+  fdr_q: 0.10
+  members: seven_hypotheses_times_two_primary_horizons
+  exploratory_horizons_can_promote: false
+statistics:
+  effect: mean_directional_forward_return
+  uncertainty: stationary_block_bootstrap
+  bootstrap_resamples: 5000
+  confidence_level: 0.90
+  minimum_fit_events: 30
+  minimum_confirmation_events: 20
+  clustering_warning_abs_lag1: 0.30
+promotion:
+  primary_rule: at_least_one_primary_horizon_bh_passes
+  fit_confirmation_sign_agreement_required_per_passing_horizon: true
+  data_quality_required: true
+  stage1_is_not_profitability_evidence: true
+source_policy:
+  required_timezone: UTC
+  expected_interval_minutes: 15
+  no_forward_fill: true
+  exact_provenance_required: true
+  liquidation_data_included: false
+  phase2_database_reads_allowed: false
+code_identity:
+  base_commit: 50d0c86d7d452e61f6ddf7ea5b70ef12f5005850
+  implementation_commit: REQUIRED_BEFORE_BATCH_EXECUTION
+  manifest_hash_algorithm: sha256_canonical
+hypotheses:
+  - hypothesis_id: HYPE_FUNDING_EXTREME_CHANGE_V1
+    family: funding
+    definition: "signal_t = -z96(funding_t) - 0.5*z24(funding_t - funding_t-1)"
+    directionality: contrarian_to_extreme_and_accelerating_funding
+    thresholds: {event_abs_z: 1.0, funding_z_window: 96, change_z_window: 24}
+    sibling_variant_count: 1
+    source_fields: [timestamp, funding_rate]
+    provenance: Hyperliquid historical funding observations with provider timestamps
+    causal_cutoff: values_with_timestamp_lte_t_only
+    horizons_minutes: [15, 60, 240, 720]
+    designation: PRIMARY_FAMILY
+    split_id: CHRONOLOGICAL_60_40_V1
+    multiple_testing_family_id: HYPE_BATCH_001_PRIMARY
+  - hypothesis_id: HYPE_FUNDING_OI_INTERACTION_V1
+    family: funding_open_interest
+    definition: "signal_t = -z96(funding_t) * max(z24(log(OI_t/OI_t-1)), 0)"
+    directionality: contrarian_when_funding_extreme_and_open_interest_expands
+    thresholds: {event_abs_z: 1.0, funding_z_window: 96, oi_change_z_window: 24}
+    sibling_variant_count: 1
+    source_fields: [timestamp, funding_rate, open_interest]
+    provenance: Hyperliquid funding and open-interest observations sampled at causal cutoffs
+    causal_cutoff: values_with_timestamp_lte_t_only
+    horizons_minutes: [15, 60, 240, 720]
+    designation: PRIMARY_FAMILY
+    split_id: CHRONOLOGICAL_60_40_V1
+    multiple_testing_family_id: HYPE_BATCH_001_PRIMARY
+  - hypothesis_id: HYPE_AGGRESSOR_CVD_V1
+    family: order_flow
+    definition: "signal_t = z96(sum4(buy_notional_t - sell_notional_t))"
+    directionality: continuation_with_aggressor_notional_imbalance
+    thresholds: {event_abs_z: 1.0, aggregation_intervals: 4, z_window: 96}
+    sibling_variant_count: 1
+    source_fields: [timestamp, aggressor_buy_notional, aggressor_sell_notional]
+    provenance: Hyperliquid trade prints classified by taker/aggressor side and aggregated [t-15m,t)
+    causal_cutoff: trades_with_timestamp_lt_interval_end_lte_t
+    horizons_minutes: [15, 60, 240, 720]
+    designation: PRIMARY_FAMILY
+    split_id: CHRONOLOGICAL_60_40_V1
+    multiple_testing_family_id: HYPE_BATCH_001_PRIMARY
+  - hypothesis_id: HYPE_TRADE_COUNT_DELTA_V1
+    family: order_flow
+    definition: "signal_t = z96(mean4((buy_count_t-sell_count_t)/(buy_count_t+sell_count_t)))"
+    directionality: continuation_with_aggressor_trade_count_imbalance
+    thresholds: {event_abs_z: 1.0, aggregation_intervals: 4, z_window: 96}
+    sibling_variant_count: 1
+    source_fields: [timestamp, buy_trade_count, sell_trade_count]
+    provenance: Hyperliquid trade prints classified by taker/aggressor side and aggregated [t-15m,t)
+    causal_cutoff: trades_with_timestamp_lt_interval_end_lte_t
+    horizons_minutes: [15, 60, 240, 720]
+    designation: PRIMARY_FAMILY
+    split_id: CHRONOLOGICAL_60_40_V1
+    multiple_testing_family_id: HYPE_BATCH_001_PRIMARY
+  - hypothesis_id: HYPE_PRICE_CVD_DIVERGENCE_V1
+    family: order_flow_divergence
+    definition: "signal_t = z96(sum4(buy_notional_t-sell_notional_t)) - z96(log(HYPE_close_t/HYPE_close_t-4))"
+    directionality: price_converges_toward_aggressor_flow
+    thresholds: {event_abs_z: 1.0, comparison_intervals: 4, z_window: 96}
+    sibling_variant_count: 1
+    source_fields: [timestamp, hype_close, aggressor_buy_notional, aggressor_sell_notional]
+    provenance: Hyperliquid HYPE candles and causally classified trade prints
+    causal_cutoff: completed_candle_and_trades_timestamped_lte_t_only
+    horizons_minutes: [15, 60, 240, 720]
+    designation: PRIMARY_FAMILY
+    split_id: CHRONOLOGICAL_60_40_V1
+    multiple_testing_family_id: HYPE_BATCH_001_PRIMARY
+  - hypothesis_id: HYPE_BTC_RELATIVE_MOMENTUM_V1
+    family: cross_asset
+    definition: "signal_t = z96(log(HYPE_close_t/HYPE_close_t-4) - log(BTC_close_t/BTC_close_t-4))"
+    directionality: continuation_of_hype_specific_relative_momentum
+    thresholds: {event_abs_z: 1.0, momentum_intervals: 4, z_window: 96}
+    sibling_variant_count: 1
+    source_fields: [timestamp, hype_close, btc_close]
+    provenance: timestamp-aligned completed Hyperliquid HYPE and BTC candles
+    causal_cutoff: completed_candles_with_close_time_lte_t_only
+    horizons_minutes: [15, 60, 240, 720]
+    designation: PRIMARY_FAMILY
+    split_id: CHRONOLOGICAL_60_40_V1
+    multiple_testing_family_id: HYPE_BATCH_001_PRIMARY
+  - hypothesis_id: HYPE_OI_PRICE_DIVERGENCE_V1
+    family: open_interest_divergence
+    definition: "signal_t = z96(log(HYPE_close_t/HYPE_close_t-4)) - z96(log(OI_t/OI_t-4))"
+    directionality: unlevered_price_move_continuation_relative_to_oi_growth
+    thresholds: {event_abs_z: 1.0, comparison_intervals: 4, z_window: 96}
+    sibling_variant_count: 1
+    source_fields: [timestamp, hype_close, open_interest]
+    provenance: completed Hyperliquid HYPE candles and timestamped open-interest observations
+    causal_cutoff: values_with_timestamp_lte_t_only
+    horizons_minutes: [15, 60, 240, 720]
+    designation: PRIMARY_FAMILY
+    split_id: CHRONOLOGICAL_60_40_V1
+    multiple_testing_family_id: HYPE_BATCH_001_PRIMARY
diff --git a/src/hype_autopilot/phase3/deployment.py b/src/hype_autopilot/phase3/deployment.py
new file mode 100644
index 0000000..119a088
--- /dev/null
+++ b/src/hype_autopilot/phase3/deployment.py
@@ -0,0 +1,94 @@
+from __future__ import annotations
+
+from collections.abc import Mapping
+from pathlib import Path
+from typing import Any, Literal
+
+import yaml
+from pydantic import BaseModel, ConfigDict, Field, model_validator
+
+from hype_autopilot.hashing import sha256_canonical
+
+
+class ParityTolerances(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    intended_vs_observed_quantity_relative: float = Field(gt=0)
+    intended_vs_observed_quantity_absolute_lots: int = Field(ge=0)
+    median_entry_slippage_error_bps: float = Field(ge=0)
+    p95_entry_slippage_error_bps: float = Field(ge=0)
+    maximum_single_entry_slippage_error_bps: float = Field(ge=0)
+    fee_variance_bps_of_notional: float = Field(ge=0)
+    funding_variance_bps_of_notional_per_settlement: float = Field(ge=0)
+    p95_intent_to_confirmed_exchange_state_seconds: float = Field(gt=0)
+
+    @model_validator(mode="after")
+    def validate_slippage_order(self) -> ParityTolerances:
+        if not (
+            self.median_entry_slippage_error_bps
+            <= self.p95_entry_slippage_error_bps
+            <= self.maximum_single_entry_slippage_error_bps
+        ):
+            raise ValueError("slippage tolerances must be monotonic")
+        return self
+
+
+class DeploymentParityContract(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    contract_version: Literal["DEPLOYMENT_PARITY_CONTRACT_V1_PROPOSAL"]
+    status: Literal["DESIGN_ONLY_NOT_EXECUTABLE"]
+    network: Literal["testnet"]
+    minimum_consecutive_executions: int = Field(ge=20)
+    maximum_unresolved_reconciliation_mismatches: Literal[0]
+    protective_exit_confirmation_required: Literal[True]
+    maximum_silent_partial_fill_or_cancel_divergences: Literal[0]
+    tolerances: ParityTolerances
+    required_measurements: tuple[str, ...]
+    identity_fields: tuple[str, ...]
+    permission_model: Literal["TRADE_ONLY_NO_WITHDRAWAL_PROPOSED"]
+    formal_testnet_sample_authorized: Literal[False]
+    mainnet_capability: Literal[False]
+
+    @property
+    def contract_hash(self) -> str:
+        return sha256_canonical(self)
+
+
+class DeploymentManifestTemplate(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    manifest_version: Literal["HYPE_DEPLOYMENT_MANIFEST_V1_PROPOSAL"]
+    status: Literal["UNPOPULATED_DESIGN_ONLY"]
+    network_identity: Literal["testnet"]
+    strategy_commit: str
+    strategy_config_hash: str
+    feature_schema_identity: str
+    snapshot_schema_identity: str
+    llm_identity: Mapping[str, Any]
+    simulator_version: str
+    risk_policy_hash: str
+    execution_policy_hash: str
+    hyperliquid_adapter_version: str
+    hyperliquid_sdk_version: str
+    permission_model_identity: Literal["TESTNET_TRADE_ONLY_NO_WITHDRAWAL"]
+    stop_tp_reduce_only_policy_version: str
+    deployment_artifact_hash: str
+    formal_testnet_parity_authorized: Literal[False]
+    live_mainnet_authorized: Literal[False]
+
+    @property
+    def template_hash(self) -> str:
+        return sha256_canonical(self)
+
+
+def load_deployment_parity_contract(path: str | Path) -> DeploymentParityContract:
+    return DeploymentParityContract.model_validate(
+        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
+    )
+
+
+def load_deployment_manifest_template(path: str | Path) -> DeploymentManifestTemplate:
+    return DeploymentManifestTemplate.model_validate(
+        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
+    )
diff --git a/src/hype_autopilot/phase3/infrastructure.py b/src/hype_autopilot/phase3/infrastructure.py
new file mode 100644
index 0000000..2156e2c
--- /dev/null
+++ b/src/hype_autopilot/phase3/infrastructure.py
@@ -0,0 +1,94 @@
+from __future__ import annotations
+
+from collections.abc import Mapping
+from datetime import UTC, datetime
+from pathlib import Path
+from typing import Any
+
+from hype_autopilot.phase3.deployment import (
+    load_deployment_manifest_template,
+    load_deployment_parity_contract,
+)
+from hype_autopilot.phase3.risk import (
+    RiskReceiptRepository,
+    connect_risk_store,
+    load_risk_policy,
+    risk_schema_hash,
+)
+from hype_autopilot.phase3.signal_lab import (
+    SignalLabRepository,
+    connect_signal_lab,
+    load_batch_manifest,
+    signal_lab_schema_hash,
+)
+
+
+def initialize_frozen_infrastructure(
+    root: str | Path,
+    *,
+    implementation_commit: str,
+    registered_at: datetime,
+) -> Mapping[str, Any]:
+    """Create only the isolated manifests/policies; never execute a Lab run."""
+    project_root = Path(root).resolve()
+    if len(implementation_commit) != 40 or any(
+        character not in "0123456789abcdef" for character in implementation_commit
+    ):
+        raise ValueError("implementation_commit must be a lowercase 40-character SHA")
+    if registered_at.tzinfo is None or registered_at.utcoffset() != UTC.utcoffset(
+        registered_at
+    ):
+        raise ValueError("registered_at must be UTC")
+
+    config_root = project_root / "config" / "phase3"
+    manifest = load_batch_manifest(config_root / "candidate_signal_lab_batch1.yaml")
+    policy = load_risk_policy(config_root / "risk_policy_v1.yaml")
+    parity = load_deployment_parity_contract(config_root / "deployment_parity_v1.yaml")
+    deployment_template = load_deployment_manifest_template(
+        config_root / "deployment_manifest_template_v1.yaml"
+    )
+
+    lab_path = project_root / "data" / "phase3_signal_lab" / "lab.sqlite3"
+    risk_path = project_root / "data" / "phase3_risk" / "risk.sqlite3"
+    lab_db = connect_signal_lab(lab_path, project_root)
+    risk_db = connect_risk_store(risk_path, project_root)
+    try:
+        lab = SignalLabRepository(lab_db)
+        risk = RiskReceiptRepository(risk_db)
+        lab.register_manifest(
+            manifest,
+            registered_at=registered_at,
+            implementation_commit=implementation_commit,
+        )
+        risk.register_policy(policy)
+        lab_runs = int(lab_db.execute("SELECT COUNT(*) FROM lab_runs").fetchone()[0])
+        risk_receipts = int(
+            risk_db.execute("SELECT COUNT(*) FROM risk_receipts").fetchone()[0]
+        )
+        if lab_runs != 0 or risk_receipts != 0:
+            raise RuntimeError(
+                "initialization cannot reuse databases containing results"
+            )
+        return {
+            "implementation_commit": implementation_commit,
+            "registered_at": registered_at.astimezone(UTC).isoformat(),
+            "batch_status": manifest.status,
+            "batch_manifest_hash": manifest.manifest_hash,
+            "lab_schema_hash": signal_lab_schema_hash(),
+            "lab_database": str(lab_path),
+            "lab_integrity": lab.integrity(),
+            "lab_run_count": lab_runs,
+            "risk_policy_hash": policy.policy_hash,
+            "risk_schema_hash": risk_schema_hash(),
+            "risk_database": str(risk_path),
+            "risk_integrity": risk.integrity(),
+            "risk_receipt_count": risk_receipts,
+            "deployment_parity_contract_hash": parity.contract_hash,
+            "deployment_manifest_template_hash": deployment_template.template_hash,
+            "stage1_executed": False,
+            "testnet_authorized": parity.formal_testnet_sample_authorized,
+            "mainnet_authorized": parity.mainnet_capability,
+        }
+    finally:
+        lab_db.close()
+        risk_db.close()
diff --git a/src/hype_autopilot/phase3/risk.py b/src/hype_autopilot/phase3/risk.py
new file mode 100644
index 0000000..7926117
--- /dev/null
+++ b/src/hype_autopilot/phase3/risk.py
@@ -0,0 +1,566 @@
+from __future__ import annotations
+
+import json
+import math
+import sqlite3
+from collections.abc import Mapping
+from datetime import UTC, datetime, timedelta
+from enum import StrEnum
+from pathlib import Path
+from typing import Any, Literal
+
+import yaml
+from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
+
+from hype_autopilot.hashing import canonical_json, sha256_canonical
+
+
+class RiskDisposition(StrEnum):
+    APPROVED = "APPROVED"
+    MODIFIED = "MODIFIED"
+    REJECTED = "REJECTED"
+
+
+class GateAction(StrEnum):
+    PASS = "PASS"
+    MODIFY = "MODIFY"
+    REJECT = "REJECT"
+
+
+class RiskReason(StrEnum):
+    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
+    MISSING_ACCOUNT_STATE = "MISSING_ACCOUNT_STATE"
+    MALFORMED_ACCOUNT_STATE = "MALFORMED_ACCOUNT_STATE"
+    ASSET_NOT_ALLOWED = "ASSET_NOT_ALLOWED"
+    KILL_SWITCH = "KILL_SWITCH"
+    MAX_POSITION = "MAX_POSITION"
+    MAX_LEVERAGE = "MAX_LEVERAGE"
+    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
+    MAX_DRAWDOWN = "MAX_DRAWDOWN"
+    MAX_TRADES_DAY = "MAX_TRADES_DAY"
+    STALE_DATA = "STALE_DATA"
+    MARGIN_LIMIT = "MARGIN_LIMIT"
+    PROTECTIVE_EXIT_UNAVAILABLE = "PROTECTIVE_EXIT_UNAVAILABLE"
+    ORDER_RATE_LIMIT = "ORDER_RATE_LIMIT"
+
+
+class RiskPolicy(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    policy_id: str
+    policy_version: str
+    asset_allowlist: tuple[str, ...]
+    max_position_notional_usd: float = Field(gt=0)
+    max_leverage: float = Field(gt=0)
+    daily_loss_limit_usd: float = Field(gt=0)
+    max_drawdown_fraction: float = Field(gt=0, lt=1)
+    max_trades_per_utc_day: int = Field(gt=0)
+    maximum_account_state_age_seconds: int = Field(gt=0)
+    maximum_margin_usage_fraction: float = Field(gt=0, le=1)
+    protective_exit_required: bool
+    maximum_orders_per_minute: int = Field(gt=0)
+    kill_switch_default: bool
+    network_access: Literal[False]
+    wallet_access: Literal[False]
+    withdrawal_capability: Literal[False]
+    order_submission_capability: Literal[False]
+
+    @property
+    def policy_hash(self) -> str:
+        return sha256_canonical(self)
+
+
+def load_risk_policy(path: str | Path) -> RiskPolicy:
+    return RiskPolicy.model_validate(
+        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
+    )
+
+
+class TradeIntent(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    trade_intent_id: str
+    timestamp: datetime
+    strategy_id: str
+    strategy_version: str
+    snapshot_hash: str | None = None
+    decision_hash: str | None = None
+    asset: str
+    side: Literal["LONG", "SHORT"]
+    proposed_notional_usd: float = Field(gt=0)
+    proposed_leverage: float = Field(gt=0)
+    entry_price: float = Field(gt=0)
+    stop_price: float = Field(gt=0)
+    target_price: float = Field(gt=0)
+    ttl_seconds: int = Field(gt=0)
+    research_metadata: Mapping[str, str] = Field(default_factory=dict)
+
+    @model_validator(mode="after")
+    def validate_geometry(self) -> TradeIntent:
+        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != timedelta(0):
+            raise ValueError("trade intent timestamp must be UTC")
+        if self.side == "LONG" and not (
+            self.stop_price < self.entry_price < self.target_price
+        ):
+            raise ValueError("LONG geometry requires stop < entry < target")
+        if self.side == "SHORT" and not (
+            self.target_price < self.entry_price < self.stop_price
+        ):
+            raise ValueError("SHORT geometry requires target < entry < stop")
+        return self
+
+
+class AccountState(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    observed_at: datetime
+    equity_usd: float = Field(gt=0)
+    available_margin_usd: float = Field(ge=0)
+    used_margin_usd: float = Field(ge=0)
+    asset_position_notional_usd: float = Field(ge=0)
+    realized_pnl_utc_day_usd: float
+    drawdown_fraction: float = Field(ge=0)
+    trades_utc_day: int = Field(ge=0)
+    orders_last_minute: int = Field(ge=0)
+
+    @model_validator(mode="after")
+    def validate_account_state(self) -> AccountState:
+        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(
+            0
+        ):
+            raise ValueError("account state timestamp must be UTC")
+        numeric = self.model_dump(mode="python", exclude={"observed_at"})
+        if any(not math.isfinite(float(value)) for value in numeric.values()):
+            raise ValueError("account state values must be finite")
+        if self.used_margin_usd > self.equity_usd:
+            raise ValueError("used margin cannot exceed equity")
+        if self.available_margin_usd > self.equity_usd:
+            raise ValueError("available margin cannot exceed equity")
+        return self
+
+
+class RiskGateResult(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    sequence: int
+    reason_code: RiskReason
+    action: GateAction
+    observed_value: float | bool | str | None
+    limit_value: float | bool | str | None
+    resulting_notional_usd: float
+    resulting_leverage: float
+
+
+class RiskReceipt(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    receipt_version: Literal["RISK_RECEIPT_V1"] = "RISK_RECEIPT_V1"
+    trade_intent_id: str
+    timestamp: datetime
+    strategy_id: str
+    strategy_version: str
+    originating_snapshot_hash: str | None
+    originating_decision_hash: str | None
+    asset: str
+    side: str
+    proposed_notional_usd: float
+    proposed_leverage: float
+    entry_price: float
+    stop_price: float
+    target_price: float
+    ttl_seconds: int
+    account_state_snapshot_hash: str
+    risk_policy_id: str
+    risk_policy_version: str
+    risk_policy_hash: str
+    ordered_gate_results: tuple[RiskGateResult, ...]
+    disposition: RiskDisposition
+    allowed_notional_usd: float
+    allowed_leverage: float
+    rejection_reason_codes: tuple[RiskReason, ...]
+    kill_switch_state: bool
+    stale_or_missing_data_state: str
+    validation_latency_us: int = Field(ge=0)
+    receipt_hash: str
+
+
+def _gate(
+    sequence: int,
+    reason: RiskReason,
+    action: GateAction,
+    observed: float | bool | str | None,
+    limit: float | bool | str | None,
+    notional: float,
+    leverage: float,
+) -> RiskGateResult:
+    return RiskGateResult(
+        sequence=sequence,
+        reason_code=reason,
+        action=action,
+        observed_value=observed,
+        limit_value=limit,
+        resulting_notional_usd=max(0.0, notional),
+        resulting_leverage=max(0.0, leverage),
+    )
+
+
+def _invalid_account_hash(account_state: object) -> str:
+    try:
+        return sha256_canonical({"invalid_account_state": account_state})
+    except (TypeError, ValueError):
+        return sha256_canonical({"invalid_account_state": type(account_state).__name__})
+
+
+def evaluate_risk(
+    intent: TradeIntent,
+    account_state: AccountState | Mapping[str, Any] | None,
+    policy: RiskPolicy,
+    *,
+    supplied_policy_hash: str,
+    now: datetime,
+    kill_switch_state: bool | None = None,
+    protective_exit_available: bool,
+    validation_latency_us: int = 0,
+) -> RiskReceipt:
+    """Pure fail-closed risk evaluation. It has no network, wallet or order path."""
+    if now.tzinfo is None or now.utcoffset() != timedelta(0):
+        raise ValueError("risk evaluation timestamp must be UTC")
+    kill_switch = (
+        policy.kill_switch_default if kill_switch_state is None else kill_switch_state
+    )
+    gates: list[RiskGateResult] = []
+    allowed_notional = intent.proposed_notional_usd
+    allowed_leverage = intent.proposed_leverage
+
+    def add(
+        reason: RiskReason,
+        action: GateAction,
+        observed: float | bool | str | None,
+        limit: float | bool | str | None,
+    ) -> None:
+        gates.append(
+            _gate(
+                len(gates) + 1,
+                reason,
+                action,
+                observed,
+                limit,
+                allowed_notional,
+                allowed_leverage,
+            )
+        )
+
+    if supplied_policy_hash != policy.policy_hash:
+        add(
+            RiskReason.POLICY_HASH_MISMATCH,
+            GateAction.REJECT,
+            supplied_policy_hash,
+            policy.policy_hash,
+        )
+
+    parsed_account: AccountState | None = None
+    account_hash: str
+    data_state = "CURRENT"
+    if account_state is None:
+        account_hash = _invalid_account_hash(None)
+        data_state = "MISSING"
+        add(RiskReason.MISSING_ACCOUNT_STATE, GateAction.REJECT, "MISSING", "REQUIRED")
+    else:
+        try:
+            parsed_account = (
+                account_state
+                if isinstance(account_state, AccountState)
+                else AccountState.model_validate(account_state)
+            )
+            account_hash = sha256_canonical(parsed_account)
+        except ValidationError:
+            account_hash = _invalid_account_hash(account_state)
+            data_state = "MALFORMED"
+            add(
+                RiskReason.MALFORMED_ACCOUNT_STATE,
+                GateAction.REJECT,
+                "MALFORMED",
+                "VALID_ACCOUNT_STATE_REQUIRED",
+            )
+
+    add(
+        RiskReason.ASSET_NOT_ALLOWED,
+        GateAction.PASS
+        if intent.asset in policy.asset_allowlist
+        else GateAction.REJECT,
+        intent.asset,
+        ",".join(policy.asset_allowlist),
+    )
+    add(
+        RiskReason.KILL_SWITCH,
+        GateAction.REJECT if kill_switch else GateAction.PASS,
+        kill_switch,
+        False,
+    )
+
+    if parsed_account is not None:
+        age = (now - parsed_account.observed_at).total_seconds()
+        stale = age < 0 or age > policy.maximum_account_state_age_seconds
+        if stale:
+            data_state = "STALE"
+        add(
+            RiskReason.STALE_DATA,
+            GateAction.REJECT if stale else GateAction.PASS,
+            age,
+            float(policy.maximum_account_state_age_seconds),
+        )
+        if allowed_leverage > policy.max_leverage:
+            allowed_leverage = policy.max_leverage
+            add(
+                RiskReason.MAX_LEVERAGE,
+                GateAction.MODIFY,
+                intent.proposed_leverage,
+                policy.max_leverage,
+            )
+        else:
+            add(
+                RiskReason.MAX_LEVERAGE,
+                GateAction.PASS,
+                allowed_leverage,
+                policy.max_leverage,
+            )
+        remaining_position = max(
+            0.0,
+            policy.max_position_notional_usd
+            - parsed_account.asset_position_notional_usd,
+        )
+        if allowed_notional > remaining_position:
+            allowed_notional = remaining_position
+            add(
+                RiskReason.MAX_POSITION,
+                GateAction.MODIFY if allowed_notional > 0 else GateAction.REJECT,
+                intent.proposed_notional_usd
+                + parsed_account.asset_position_notional_usd,
+                policy.max_position_notional_usd,
+            )
+        else:
+            add(
+                RiskReason.MAX_POSITION,
+                GateAction.PASS,
+                allowed_notional + parsed_account.asset_position_notional_usd,
+                policy.max_position_notional_usd,
+            )
+        add(
+            RiskReason.DAILY_LOSS_LIMIT,
+            GateAction.REJECT
+            if parsed_account.realized_pnl_utc_day_usd <= -policy.daily_loss_limit_usd
+            else GateAction.PASS,
+            parsed_account.realized_pnl_utc_day_usd,
+            -policy.daily_loss_limit_usd,
+        )
+        add(
+            RiskReason.MAX_DRAWDOWN,
+            GateAction.REJECT
+            if parsed_account.drawdown_fraction >= policy.max_drawdown_fraction
+            else GateAction.PASS,
+            parsed_account.drawdown_fraction,
+            policy.max_drawdown_fraction,
+        )
+        add(
+            RiskReason.MAX_TRADES_DAY,
+            GateAction.REJECT
+            if parsed_account.trades_utc_day >= policy.max_trades_per_utc_day
+            else GateAction.PASS,
+            float(parsed_account.trades_utc_day),
+            float(policy.max_trades_per_utc_day),
+        )
+        required_margin = (
+            allowed_notional / allowed_leverage if allowed_leverage else math.inf
+        )
+        projected_margin_fraction = (
+            parsed_account.used_margin_usd + required_margin
+        ) / parsed_account.equity_usd
+        margin_failed = (
+            required_margin > parsed_account.available_margin_usd
+            or projected_margin_fraction > policy.maximum_margin_usage_fraction
+        )
+        add(
+            RiskReason.MARGIN_LIMIT,
+            GateAction.REJECT if margin_failed else GateAction.PASS,
+            projected_margin_fraction,
+            policy.maximum_margin_usage_fraction,
+        )
+        add(
+            RiskReason.ORDER_RATE_LIMIT,
+            GateAction.REJECT
+            if parsed_account.orders_last_minute >= policy.maximum_orders_per_minute
+            else GateAction.PASS,
+            float(parsed_account.orders_last_minute),
+            float(policy.maximum_orders_per_minute),
+        )
+    add(
+        RiskReason.PROTECTIVE_EXIT_UNAVAILABLE,
+        GateAction.REJECT
+        if policy.protective_exit_required and not protective_exit_available
+        else GateAction.PASS,
+        protective_exit_available,
+        policy.protective_exit_required,
+    )
+
+    rejected = tuple(
+        gate.reason_code for gate in gates if gate.action == GateAction.REJECT
+    )
+    if rejected:
+        disposition = RiskDisposition.REJECTED
+        allowed_notional = 0.0
+        allowed_leverage = 0.0
+    elif any(gate.action == GateAction.MODIFY for gate in gates):
+        disposition = RiskDisposition.MODIFIED
+    else:
+        disposition = RiskDisposition.APPROVED
+    base = {
+        "receipt_version": "RISK_RECEIPT_V1",
+        "trade_intent_id": intent.trade_intent_id,
+        "timestamp": intent.timestamp,
+        "strategy_id": intent.strategy_id,
+        "strategy_version": intent.strategy_version,
+        "originating_snapshot_hash": intent.snapshot_hash,
+        "originating_decision_hash": intent.decision_hash,
+        "asset": intent.asset,
+        "side": intent.side,
+        "proposed_notional_usd": intent.proposed_notional_usd,
+        "proposed_leverage": intent.proposed_leverage,
+        "entry_price": intent.entry_price,
+        "stop_price": intent.stop_price,
+        "target_price": intent.target_price,
+        "ttl_seconds": intent.ttl_seconds,
+        "account_state_snapshot_hash": account_hash,
+        "risk_policy_id": policy.policy_id,
+        "risk_policy_version": policy.policy_version,
+        "risk_policy_hash": policy.policy_hash,
+        "ordered_gate_results": tuple(gates),
+        "disposition": disposition,
+        "allowed_notional_usd": allowed_notional,
+        "allowed_leverage": allowed_leverage,
+        "rejection_reason_codes": rejected,
+        "kill_switch_state": kill_switch,
+        "stale_or_missing_data_state": data_state,
+        "validation_latency_us": validation_latency_us,
+    }
+    return RiskReceipt(**base, receipt_hash=sha256_canonical(base))
+
+
+RISK_SCHEMA = """
+CREATE TABLE IF NOT EXISTS risk_policies (
+  policy_hash TEXT PRIMARY KEY, policy_id TEXT NOT NULL, policy_version TEXT NOT NULL,
+  payload_json TEXT NOT NULL, UNIQUE(policy_id, policy_version)
+);
+CREATE TABLE IF NOT EXISTS risk_receipts (
+  receipt_hash TEXT PRIMARY KEY, trade_intent_id TEXT NOT NULL,
+  timestamp TEXT NOT NULL, disposition TEXT NOT NULL,
+  risk_policy_hash TEXT NOT NULL REFERENCES risk_policies(policy_hash),
+  account_state_snapshot_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
+  UNIQUE(trade_intent_id, risk_policy_hash)
+);
+CREATE TRIGGER IF NOT EXISTS immutable_risk_policies_update BEFORE UPDATE ON risk_policies
+BEGIN SELECT RAISE(ABORT, 'risk policies are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_risk_policies_delete BEFORE DELETE ON risk_policies
+BEGIN SELECT RAISE(ABORT, 'risk policies are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_risk_receipts_update BEFORE UPDATE ON risk_receipts
+BEGIN SELECT RAISE(ABORT, 'risk receipts are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_risk_receipts_delete BEFORE DELETE ON risk_receipts
+BEGIN SELECT RAISE(ABORT, 'risk receipts are immutable'); END;
+"""
+
+
+def risk_schema_hash() -> str:
+    return sha256_canonical({"schema": RISK_SCHEMA})
+
+
+def connect_risk_store(
+    path: str | Path, isolated_root: str | Path
+) -> sqlite3.Connection:
+    root = Path(isolated_root).resolve()
+    required_root = (root / "data" / "phase3_risk").resolve()
+    resolved = Path(path).resolve()
+    if required_root != resolved.parent and required_root not in resolved.parents:
+        raise ValueError("Risk Receipt database must remain under data/phase3_risk")
+    lowered = str(resolved).lower()
+    if "phase2_epoch" in lowered or "epoch_001.sqlite3" in lowered:
+        raise ValueError("production evidence databases are prohibited")
+    resolved.parent.mkdir(parents=True, exist_ok=True)
+    db = sqlite3.connect(resolved)
+    db.row_factory = sqlite3.Row
+    db.execute("PRAGMA foreign_keys = ON")
+    db.executescript(RISK_SCHEMA)
+    db.commit()
+    return db
+
+
+class RiskReceiptRepository:
+    def __init__(self, db: sqlite3.Connection) -> None:
+        self.db = db
+
+    def register_policy(self, policy: RiskPolicy) -> None:
+        payload = canonical_json(policy)
+        try:
+            self.db.execute(
+                "INSERT INTO risk_policies VALUES (?,?,?,?)",
+                (policy.policy_hash, policy.policy_id, policy.policy_version, payload),
+            )
+            self.db.commit()
+        except sqlite3.IntegrityError:
+            self.db.rollback()
+            row = self.db.execute(
+                "SELECT payload_json FROM risk_policies WHERE policy_id=? AND policy_version=?",
+                (policy.policy_id, policy.policy_version),
+            ).fetchone()
+            if row is None or row["payload_json"] != payload:
+                raise RuntimeError("immutable risk policy conflict")
+
+    def save_receipt(self, receipt: RiskReceipt) -> None:
+        receipt_fields = receipt.model_dump(mode="python")
+        claimed_hash = str(receipt_fields.pop("receipt_hash"))
+        if sha256_canonical(receipt_fields) != claimed_hash:
+            raise ValueError("risk receipt hash does not match its payload")
+        payload = canonical_json(receipt)
+        try:
+            self.db.execute(
+                "INSERT INTO risk_receipts VALUES (?,?,?,?,?,?,?)",
+                (
+                    receipt.receipt_hash,
+                    receipt.trade_intent_id,
+                    receipt.timestamp.astimezone(UTC).isoformat(),
+                    receipt.disposition.value,
+                    receipt.risk_policy_hash,
+                    receipt.account_state_snapshot_hash,
+                    payload,
+                ),
+            )
+            self.db.commit()
+        except sqlite3.IntegrityError:
+            self.db.rollback()
+            row = self.db.execute(
+                "SELECT payload_json FROM risk_receipts WHERE trade_intent_id=? AND risk_policy_hash=?",
+                (receipt.trade_intent_id, receipt.risk_policy_hash),
+            ).fetchone()
+            if row is None or row["payload_json"] != payload:
+                raise RuntimeError("immutable risk receipt conflict")
+
+    def load_receipt(self, receipt_hash: str) -> RiskReceipt | None:
+        row = self.db.execute(
+            "SELECT payload_json FROM risk_receipts WHERE receipt_hash=?",
+            (receipt_hash,),
+        ).fetchone()
+        if not row:
+            return None
+        receipt = RiskReceipt.model_validate(json.loads(row["payload_json"]))
+        receipt_fields = receipt.model_dump(mode="python")
+        claimed_hash = str(receipt_fields.pop("receipt_hash"))
+        if (
+            claimed_hash != receipt_hash
+            or sha256_canonical(receipt_fields) != claimed_hash
+        ):
+            raise RuntimeError("persisted risk receipt hash verification failed")
+        return receipt
+
+    def integrity(self) -> tuple[str, int]:
+        return (
+            str(self.db.execute("PRAGMA integrity_check").fetchone()[0]),
+            len(self.db.execute("PRAGMA foreign_key_check").fetchall()),
+        )
diff --git a/src/hype_autopilot/phase3/signal_lab.py b/src/hype_autopilot/phase3/signal_lab.py
new file mode 100644
index 0000000..f745607
--- /dev/null
+++ b/src/hype_autopilot/phase3/signal_lab.py
@@ -0,0 +1,854 @@
+from __future__ import annotations
+
+import math
+import sqlite3
+from collections.abc import Mapping, Sequence
+from dataclasses import dataclass
+from datetime import UTC, datetime, timedelta
+from enum import StrEnum
+from math import floor, log
+from pathlib import Path
+from typing import Any, Literal
+from uuid import NAMESPACE_URL, uuid5
+
+import numpy as np
+import yaml
+from arch.bootstrap import StationaryBootstrap
+from pydantic import BaseModel, ConfigDict, Field, model_validator
+
+from hype_autopilot.hashing import canonical_json, sha256_canonical
+from hype_autopilot.phase3.gate import automatic_stationary_block_length
+
+
+class LabOutcome(StrEnum):
+    PASS_STAGE_1 = "PASS_STAGE_1"
+    WEAK = "WEAK"
+    REJECT = "REJECT"
+    DATA_INVALID = "DATA_INVALID"
+
+
+class HypothesisDefinition(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    hypothesis_id: str
+    family: str
+    definition: str
+    directionality: str
+    thresholds: Mapping[str, float | int]
+    sibling_variant_count: int = Field(ge=1)
+    source_fields: tuple[str, ...]
+    provenance: str
+    causal_cutoff: str
+    horizons_minutes: tuple[int, ...]
+    designation: str
+    split_id: str
+    multiple_testing_family_id: str
+
+
+class BatchManifest(BaseModel):
+    model_config = ConfigDict(extra="allow", frozen=True)
+
+    lab_version: Literal["CANDIDATE_SIGNAL_LAB_V1"]
+    batch_id: Literal["HYPE_BATCH_001"]
+    status: Literal["FROZEN_NOT_EXECUTED"]
+    data_domain: Literal["HISTORICAL_NON_PHASE2_ONLY"]
+    production_epoch_access: Literal["PROHIBITED"]
+    llm_calls_allowed: Literal[False]
+    stage2_enabled: Literal[False]
+    normalization: Mapping[str, Any]
+    horizons: Mapping[str, Any]
+    split: Mapping[str, Any]
+    multiple_testing: Mapping[str, Any]
+    statistics: Mapping[str, Any]
+    promotion: Mapping[str, Any]
+    source_policy: Mapping[str, Any]
+    code_identity: Mapping[str, Any]
+    hypotheses: tuple[HypothesisDefinition, ...]
+
+    @model_validator(mode="after")
+    def validate_batch_freeze(self) -> BatchManifest:
+        expected = (
+            "HYPE_FUNDING_EXTREME_CHANGE_V1",
+            "HYPE_FUNDING_OI_INTERACTION_V1",
+            "HYPE_AGGRESSOR_CVD_V1",
+            "HYPE_TRADE_COUNT_DELTA_V1",
+            "HYPE_PRICE_CVD_DIVERGENCE_V1",
+            "HYPE_BTC_RELATIVE_MOMENTUM_V1",
+            "HYPE_OI_PRICE_DIVERGENCE_V1",
+        )
+        if tuple(item.hypothesis_id for item in self.hypotheses) != expected:
+            raise ValueError(
+                "Batch 1 must contain the seven hypotheses in frozen order"
+            )
+        if tuple(self.horizons["primary_minutes"]) != (60, 240):
+            raise ValueError("primary horizons are frozen at 1h and 4h")
+        if tuple(self.horizons["exploratory_minutes"]) != (15, 720):
+            raise ValueError("exploratory horizons are frozen at 15m and 12h")
+        if self.multiple_testing.get("method") != "benjamini_hochberg":
+            raise ValueError("Batch 1 multiple testing method is frozen to BH-FDR")
+        if int(self.multiple_testing.get("family_size", 0)) != 14:
+            raise ValueError("Batch 1 primary BH-FDR family must contain 14 tests")
+        if float(self.split.get("fit_fraction", 0.0)) != 0.60:
+            raise ValueError("Batch 1 chronological split is frozen at 60/40")
+        if self.source_policy.get("phase2_database_reads_allowed") is not False:
+            raise ValueError("the Candidate Signal Lab cannot read Phase 2 databases")
+        return self
+
+    @property
+    def manifest_hash(self) -> str:
+        return sha256_canonical(self)
+
+
+def load_batch_manifest(path: str | Path) -> BatchManifest:
+    return BatchManifest.model_validate(
+        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
+    )
+
+
+class SourceProvenance(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    source_id: str
+    provider: str
+    dataset: str
+    retrieval_timestamp: datetime
+    coverage_start: datetime
+    coverage_end: datetime
+    source_hash: str = Field(min_length=64, max_length=64)
+    timestamp_semantics: str
+    aggressor_side_semantics: str | None = None
+    reproducible_locator: str
+
+    @model_validator(mode="after")
+    def validate_time_range(self) -> SourceProvenance:
+        for value in (
+            self.retrieval_timestamp,
+            self.coverage_start,
+            self.coverage_end,
+        ):
+            if value.tzinfo is None or value.utcoffset() != timedelta(0):
+                raise ValueError(
+                    "source provenance timestamps must be normalized to UTC"
+                )
+        if self.coverage_end <= self.coverage_start:
+            raise ValueError("source coverage must have positive duration")
+        if not self.reproducible_locator.strip():
+            raise ValueError("a reproducible source locator is required")
+        return self
+
+
+class MarketObservation(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    timestamp: datetime
+    hype_close: float
+    btc_close: float | None = None
+    funding_rate: float | None = None
+    open_interest: float | None = None
+    aggressor_buy_notional: float | None = None
+    aggressor_sell_notional: float | None = None
+    buy_trade_count: int | None = Field(default=None, ge=0)
+    sell_trade_count: int | None = Field(default=None, ge=0)
+    average_buy_trade_notional: float | None = None
+    average_sell_trade_notional: float | None = None
+    regime: str | None = None
+
+    @model_validator(mode="after")
+    def validate_values(self) -> MarketObservation:
+        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != timedelta(0):
+            raise ValueError("market timestamps must be normalized to UTC")
+        numeric = self.model_dump(mode="python", exclude={"timestamp", "regime"})
+        for name, value in numeric.items():
+            if value is not None and not math.isfinite(float(value)):
+                raise ValueError(f"{name} must be finite")
+        if self.hype_close <= 0 or (self.btc_close is not None and self.btc_close <= 0):
+            raise ValueError("close prices must be positive")
+        if self.open_interest is not None and self.open_interest <= 0:
+            raise ValueError("open interest must be positive")
+        return self
+
+
+class AggressorSide(StrEnum):
+    BUY = "BUY"
+    SELL = "SELL"
+
+
+class TradePrint(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    trade_id: str
+    timestamp: datetime
+    price: float = Field(gt=0)
+    size: float = Field(gt=0)
+    aggressor_side: AggressorSide
+
+    @model_validator(mode="after")
+    def validate_timestamp(self) -> TradePrint:
+        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != timedelta(0):
+            raise ValueError("trade timestamps must be normalized to UTC")
+        return self
+
+
+@dataclass(frozen=True)
+class TradeFlowAggregate:
+    interval_start: datetime
+    interval_end: datetime
+    aggressor_buy_notional: float
+    aggressor_sell_notional: float
+    buy_trade_count: int
+    sell_trade_count: int
+    average_buy_trade_notional: float | None
+    average_sell_trade_notional: float | None
+    semantics: str
+    aggregate_hash: str
+
+
+def aggregate_trade_flow(
+    trades: Sequence[TradePrint], interval_start: datetime, interval_end: datetime
+) -> TradeFlowAggregate:
+    if interval_start.tzinfo is None or interval_end.tzinfo is None:
+        raise ValueError("aggregation interval must be timezone-aware")
+    start = interval_start.astimezone(UTC)
+    end = interval_end.astimezone(UTC)
+    if end <= start:
+        raise ValueError("aggregation interval must have positive duration")
+    seen: set[str] = set()
+    buys: list[float] = []
+    sells: list[float] = []
+    for trade in sorted(trades, key=lambda item: (item.timestamp, item.trade_id)):
+        if trade.trade_id in seen:
+            raise ValueError(f"duplicate trade id: {trade.trade_id}")
+        seen.add(trade.trade_id)
+        if not (start <= trade.timestamp < end):
+            continue
+        notional = trade.price * trade.size
+        (buys if trade.aggressor_side == AggressorSide.BUY else sells).append(notional)
+    payload = {
+        "interval_start": start,
+        "interval_end": end,
+        "aggressor_buy_notional": sum(buys),
+        "aggressor_sell_notional": sum(sells),
+        "buy_trade_count": len(buys),
+        "sell_trade_count": len(sells),
+        "average_buy_trade_notional": sum(buys) / len(buys) if buys else None,
+        "average_sell_trade_notional": sum(sells) / len(sells) if sells else None,
+        "semantics": "BUY_IS_TAKER_BUY_SELL_IS_TAKER_SELL_INTERVAL_START_INCLUSIVE_END_EXCLUSIVE",
+    }
+    return TradeFlowAggregate(**payload, aggregate_hash=sha256_canonical(payload))
+
+
+@dataclass(frozen=True)
+class DataQualityReport:
+    valid: bool
+    issues: tuple[str, ...]
+    missing_timestamps: tuple[str, ...]
+    available_fields: tuple[str, ...]
+    hypothesis_status: Mapping[str, str]
+    report_hash: str
+
+
+def validate_market_data(
+    observations: Sequence[MarketObservation],
+    provenance: SourceProvenance,
+    manifest: BatchManifest,
+) -> DataQualityReport:
+    issues: list[str] = []
+    if not observations:
+        issues.append("EMPTY_DATASET")
+    timestamps = [item.timestamp for item in observations]
+    if timestamps != sorted(timestamps):
+        issues.append("NON_MONOTONIC_TIMESTAMPS")
+    if len(set(timestamps)) != len(timestamps):
+        issues.append("DUPLICATE_TIMESTAMPS")
+    if timestamps and min(timestamps) < provenance.coverage_start:
+        issues.append("OBSERVATION_BEFORE_PROVENANCE_COVERAGE")
+    if timestamps and max(timestamps) > provenance.coverage_end:
+        issues.append("OBSERVATION_AFTER_PROVENANCE_COVERAGE")
+    expected_delta = timedelta(
+        minutes=int(manifest.source_policy["expected_interval_minutes"])
+    )
+    missing: list[str] = []
+    if timestamps:
+        cursor = min(timestamps)
+        observed = set(timestamps)
+        while cursor <= max(timestamps):
+            if cursor not in observed:
+                missing.append(cursor.isoformat())
+            cursor += expected_delta
+    if missing:
+        issues.append("MISSING_INTERVALS")
+    available = tuple(
+        sorted(
+            name
+            for name in MarketObservation.model_fields
+            if name not in {"timestamp", "regime"}
+            and observations
+            and all(getattr(row, name) is not None for row in observations)
+        )
+    )
+    hypothesis_status: dict[str, str] = {}
+    for hypothesis in manifest.hypotheses:
+        absent = sorted(
+            set(hypothesis.source_fields) - ({"timestamp"} | set(available))
+        )
+        if absent:
+            hypothesis_status[hypothesis.hypothesis_id] = (
+                "DATA_INVALID:MISSING_FIELDS:" + ",".join(absent)
+            )
+        elif (
+            hypothesis.family.startswith("order_flow")
+            and not provenance.aggressor_side_semantics
+        ):
+            hypothesis_status[hypothesis.hypothesis_id] = (
+                "DATA_INVALID:AGGRESSOR_SIDE_SEMANTICS_UNPROVEN"
+            )
+        else:
+            hypothesis_status[hypothesis.hypothesis_id] = "DATA_VALID"
+    payload = {
+        "issues": sorted(set(issues)),
+        "missing_timestamps": missing,
+        "available_fields": available,
+        "hypothesis_status": hypothesis_status,
+        "provenance_hash": sha256_canonical(provenance),
+        "input_hash": sha256_canonical(observations),
+    }
+    return DataQualityReport(
+        valid=not issues,
+        issues=tuple(payload["issues"]),
+        missing_timestamps=tuple(missing),
+        available_fields=available,
+        hypothesis_status=hypothesis_status,
+        report_hash=sha256_canonical(payload),
+    )
+
+
+def _rolling_z(values: Sequence[float | None], index: int, window: int) -> float | None:
+    if index + 1 < window:
+        return None
+    sample = values[index + 1 - window : index + 1]
+    if any(value is None or not math.isfinite(float(value)) for value in sample):
+        return None
+    array = np.asarray(sample, dtype=float)
+    deviation = float(np.std(array, ddof=1))
+    if deviation == 0:
+        return 0.0
+    return (float(array[-1]) - float(np.mean(array))) / deviation
+
+
+def _log_change(values: Sequence[float | None], index: int, lag: int) -> float | None:
+    if index < lag or values[index] is None or values[index - lag] is None:
+        return None
+    current = float(values[index])
+    previous = float(values[index - lag])
+    if current <= 0 or previous <= 0:
+        return None
+    return log(current / previous)
+
+
+def compute_signal_series(
+    observations: Sequence[MarketObservation], hypothesis_id: str
+) -> tuple[float | None, ...]:
+    fields = {
+        name: [getattr(row, name) for row in observations]
+        for name in MarketObservation.model_fields
+        if name != "timestamp"
+    }
+    funding_changes: list[float | None] = [None]
+    funding = fields["funding_rate"]
+    for index in range(1, len(observations)):
+        if funding[index] is None or funding[index - 1] is None:
+            funding_changes.append(None)
+        else:
+            funding_changes.append(float(funding[index]) - float(funding[index - 1]))
+    cvd4: list[float | None] = []
+    count4: list[float | None] = []
+    for index in range(len(observations)):
+        if index < 3:
+            cvd4.append(None)
+            count4.append(None)
+            continue
+        buy = fields["aggressor_buy_notional"][index - 3 : index + 1]
+        sell = fields["aggressor_sell_notional"][index - 3 : index + 1]
+        if any(value is None for value in (*buy, *sell)):
+            cvd4.append(None)
+        else:
+            cvd4.append(sum(float(v) for v in buy) - sum(float(v) for v in sell))
+        buy_count = fields["buy_trade_count"][index - 3 : index + 1]
+        sell_count = fields["sell_trade_count"][index - 3 : index + 1]
+        if any(value is None for value in (*buy_count, *sell_count)):
+            count4.append(None)
+        else:
+            ratios = []
+            for b, s in zip(buy_count, sell_count, strict=True):
+                total = int(b) + int(s)
+                ratios.append((int(b) - int(s)) / total if total else 0.0)
+            count4.append(float(np.mean(ratios)))
+    hype_return4 = [
+        _log_change(fields["hype_close"], index, 4)
+        for index in range(len(observations))
+    ]
+    btc_return4 = [
+        _log_change(fields["btc_close"], index, 4) for index in range(len(observations))
+    ]
+    oi_change1 = [
+        _log_change(fields["open_interest"], index, 1)
+        for index in range(len(observations))
+    ]
+    oi_change4 = [
+        _log_change(fields["open_interest"], index, 4)
+        for index in range(len(observations))
+    ]
+    output: list[float | None] = []
+    for index in range(len(observations)):
+        if hypothesis_id == "HYPE_FUNDING_EXTREME_CHANGE_V1":
+            a = _rolling_z(funding, index, 96)
+            b = _rolling_z(funding_changes, index, 24)
+            value = -a - 0.5 * b if a is not None and b is not None else None
+        elif hypothesis_id == "HYPE_FUNDING_OI_INTERACTION_V1":
+            a = _rolling_z(funding, index, 96)
+            b = _rolling_z(oi_change1, index, 24)
+            value = -a * max(b, 0.0) if a is not None and b is not None else None
+        elif hypothesis_id == "HYPE_AGGRESSOR_CVD_V1":
+            value = _rolling_z(cvd4, index, 96)
+        elif hypothesis_id == "HYPE_TRADE_COUNT_DELTA_V1":
+            value = _rolling_z(count4, index, 96)
+        elif hypothesis_id == "HYPE_PRICE_CVD_DIVERGENCE_V1":
+            a = _rolling_z(cvd4, index, 96)
+            b = _rolling_z(hype_return4, index, 96)
+            value = a - b if a is not None and b is not None else None
+        elif hypothesis_id == "HYPE_BTC_RELATIVE_MOMENTUM_V1":
+            spread = [
+                h - b if h is not None and b is not None else None
+                for h, b in zip(hype_return4, btc_return4, strict=True)
+            ]
+            value = _rolling_z(spread, index, 96)
+        elif hypothesis_id == "HYPE_OI_PRICE_DIVERGENCE_V1":
+            a = _rolling_z(hype_return4, index, 96)
+            b = _rolling_z(oi_change4, index, 96)
+            value = a - b if a is not None and b is not None else None
+        else:
+            raise KeyError(f"unknown frozen hypothesis: {hypothesis_id}")
+        output.append(value)
+    return tuple(output)
+
+
+def build_forward_returns(
+    observations: Sequence[MarketObservation], horizons_minutes: Sequence[int]
+) -> tuple[Mapping[int, float | None], ...]:
+    by_timestamp = {row.timestamp: row.hype_close for row in observations}
+    output: list[dict[int, float | None]] = []
+    for row in observations:
+        horizon_values: dict[int, float | None] = {}
+        for minutes in horizons_minutes:
+            future = by_timestamp.get(row.timestamp + timedelta(minutes=minutes))
+            horizon_values[int(minutes)] = (
+                log(float(future) / row.hype_close) if future is not None else None
+            )
+        output.append(horizon_values)
+    return tuple(output)
+
+
+def benjamini_hochberg(
+    p_values: Mapping[str, float], q: float
+) -> dict[str, dict[str, float | bool]]:
+    if not 0 < q < 1:
+        raise ValueError("BH-FDR q must be between zero and one")
+    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
+    m = len(ordered)
+    largest_rejected = 0
+    for rank, (_, p_value) in enumerate(ordered, start=1):
+        if not 0 <= p_value <= 1:
+            raise ValueError("p-values must be in [0,1]")
+        if p_value <= q * rank / m:
+            largest_rejected = rank
+    adjusted: dict[str, float] = {}
+    running = 1.0
+    for rank in range(m, 0, -1):
+        key, p_value = ordered[rank - 1]
+        running = min(running, p_value * m / rank)
+        adjusted[key] = running
+    return {
+        key: {
+            "p_value": value,
+            "adjusted_p_value": adjusted[key],
+            "rejected": rank <= largest_rejected,
+        }
+        for rank, (key, value) in enumerate(ordered, start=1)
+    }
+
+
+@dataclass(frozen=True)
+class PartitionEffect:
+    count: int
+    mean: float | None
+    confidence_interval: tuple[float, float] | None
+    p_value: float
+    block_length: int
+    lag1_autocorrelation: float
+
+
+def _partition_effect(
+    values: Sequence[float], *, repetitions: int, confidence_level: float, seed: int
+) -> PartitionEffect:
+    if not values:
+        return PartitionEffect(0, None, None, 1.0, 1, 0.0)
+    data = np.asarray(values, dtype=float)
+    observed = float(np.mean(data))
+    block = automatic_stationary_block_length(data)
+    if len(data) == 1 or float(np.var(data)) == 0.0:
+        p_value = 1.0 / (repetitions + 1) if observed > 0 else 1.0
+        ci = (observed, observed)
+    else:
+        rng = np.random.default_rng(seed)
+        direct = StationaryBootstrap(block, data, seed=rng)
+        means = np.asarray(
+            [
+                float(np.mean(positional[0]))
+                for positional, _ in direct.bootstrap(repetitions)
+            ]
+        )
+        alpha = 1.0 - confidence_level
+        ci = tuple(float(v) for v in np.quantile(means, [alpha / 2, 1 - alpha / 2]))
+        centered = data - observed
+        null = StationaryBootstrap(
+            block, centered, seed=np.random.default_rng(seed + 10_000)
+        )
+        null_means = [
+            float(np.mean(positional[0]))
+            for positional, _ in null.bootstrap(repetitions)
+        ]
+        p_value = (1 + sum(value >= observed for value in null_means)) / (
+            repetitions + 1
+        )
+    lag1 = (
+        float(np.corrcoef(data[:-1], data[1:])[0, 1])
+        if len(data) > 2 and float(np.var(data)) > 0
+        else 0.0
+    )
+    if not math.isfinite(lag1):
+        lag1 = 0.0
+    return PartitionEffect(len(data), observed, ci, p_value, block, lag1)
+
+
+def _directional_values(
+    signals: Sequence[float | None],
+    returns: Sequence[float | None],
+    indices: range,
+    threshold: float,
+) -> list[float]:
+    return [
+        math.copysign(1.0, float(signals[index])) * float(returns[index])
+        for index in indices
+        if signals[index] is not None
+        and returns[index] is not None
+        and abs(float(signals[index])) >= threshold
+    ]
+
+
+def screen_batch(
+    observations: Sequence[MarketObservation],
+    provenance: SourceProvenance,
+    manifest: BatchManifest,
+    *,
+    repetitions_override: int | None = None,
+) -> dict[str, Any]:
+    """Run the frozen Stage-1 screen; callers must separately authorize real data use."""
+    if provenance.dataset.startswith("phase2_epoch_"):
+        raise PermissionError(
+            "active or historical Phase 2 evidence is outside Lab scope"
+        )
+    quality = validate_market_data(observations, provenance, manifest)
+    horizons = tuple(manifest.horizons["primary_minutes"]) + tuple(
+        manifest.horizons["exploratory_minutes"]
+    )
+    forward = build_forward_returns(observations, horizons)
+    split_at = floor(len(observations) * float(manifest.split["fit_fraction"]))
+    repetitions = repetitions_override or int(
+        manifest.statistics["bootstrap_resamples"]
+    )
+    threshold = float(manifest.normalization["event_threshold_abs_z"])
+    reports: dict[str, dict[str, Any]] = {}
+    primary_p_values: dict[str, float] = {}
+    for hypothesis_index, hypothesis in enumerate(manifest.hypotheses):
+        signals = compute_signal_series(observations, hypothesis.hypothesis_id)
+        status = quality.hypothesis_status[hypothesis.hypothesis_id]
+        horizon_reports: dict[str, Any] = {}
+        for horizon_index, minutes in enumerate(horizons):
+            returns = [item[int(minutes)] for item in forward]
+            fit_values = _directional_values(
+                signals, returns, range(split_at), threshold
+            )
+            confirmation_values = _directional_values(
+                signals, returns, range(split_at, len(observations)), threshold
+            )
+            seed = 1_000 + hypothesis_index * 100 + horizon_index
+            fit_effect = _partition_effect(
+                fit_values,
+                repetitions=repetitions,
+                confidence_level=float(manifest.statistics["confidence_level"]),
+                seed=seed,
+            )
+            confirmation_effect = _partition_effect(
+                confirmation_values,
+                repetitions=repetitions,
+                confidence_level=float(manifest.statistics["confidence_level"]),
+                seed=seed + 50,
+            )
+            sign_agreement = (
+                fit_effect.count > 0
+                and confirmation_effect.count > 0
+                and fit_effect.mean is not None
+                and confirmation_effect.mean is not None
+                and math.copysign(1.0, fit_effect.mean)
+                == math.copysign(1.0, confirmation_effect.mean)
+            )
+            test_id = f"{hypothesis.hypothesis_id}:{minutes}m"
+            if int(minutes) in manifest.horizons["primary_minutes"]:
+                primary_p_values[test_id] = (
+                    confirmation_effect.p_value if status == "DATA_VALID" else 1.0
+                )
+            horizon_reports[str(minutes)] = {
+                "designation": "PRIMARY"
+                if int(minutes) in manifest.horizons["primary_minutes"]
+                else "EXPLORATORY",
+                "fit": fit_effect.__dict__,
+                "confirmation": confirmation_effect.__dict__,
+                "fit_confirmation_sign_agreement": sign_agreement,
+                "severe_clustering_warning": abs(
+                    confirmation_effect.lag1_autocorrelation
+                )
+                >= float(manifest.statistics["clustering_warning_abs_lag1"]),
+            }
+        reports[hypothesis.hypothesis_id] = {
+            "data_status": status,
+            "horizons": horizon_reports,
+        }
+    if len(primary_p_values) != int(manifest.multiple_testing["family_size"]):
+        raise RuntimeError("primary BH-FDR family does not match the frozen size")
+    bh = benjamini_hochberg(primary_p_values, float(manifest.multiple_testing["fdr_q"]))
+    for hypothesis in manifest.hypotheses:
+        report = reports[hypothesis.hypothesis_id]
+        qualifying: list[str] = []
+        weak = False
+        for minutes in manifest.horizons["primary_minutes"]:
+            key = f"{hypothesis.hypothesis_id}:{minutes}m"
+            item = report["horizons"][str(minutes)]
+            item["bh_fdr"] = bh[key]
+            fit = item["fit"]
+            confirmation = item["confirmation"]
+            enough = fit["count"] >= int(
+                manifest.statistics["minimum_fit_events"]
+            ) and confirmation["count"] >= int(
+                manifest.statistics["minimum_confirmation_events"]
+            )
+            positive_stable = (
+                item["fit_confirmation_sign_agreement"]
+                and fit["mean"] is not None
+                and confirmation["mean"] is not None
+                and fit["mean"] > 0
+                and confirmation["mean"] > 0
+            )
+            if enough and positive_stable:
+                weak = True
+            if enough and positive_stable and bh[key]["rejected"]:
+                qualifying.append(key)
+        if report["data_status"] != "DATA_VALID" or not quality.valid:
+            outcome = LabOutcome.DATA_INVALID
+        elif qualifying:
+            outcome = LabOutcome.PASS_STAGE_1
+        elif weak:
+            outcome = LabOutcome.WEAK
+        else:
+            outcome = LabOutcome.REJECT
+        report["outcome"] = outcome.value
+        report["qualifying_primary_tests"] = qualifying
+        report["exploratory_can_promote"] = False
+    payload = {
+        "lab_version": manifest.lab_version,
+        "batch_id": manifest.batch_id,
+        "manifest_hash": manifest.manifest_hash,
+        "input_hash": sha256_canonical(observations),
+        "provenance_hash": sha256_canonical(provenance),
+        "data_quality": quality.__dict__,
+        "split_index": split_at,
+        "bh_family_id": manifest.multiple_testing["family_id"],
+        "bh_family_size": len(primary_p_values),
+        "reports": reports,
+        "stage2_executed": False,
+        "active_epoch_evidence": False,
+    }
+    return {**payload, "result_hash": sha256_canonical(payload)}
+
+
+LAB_SCHEMA = """
+CREATE TABLE IF NOT EXISTS lab_manifests (
+  manifest_hash TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE,
+  status TEXT NOT NULL, payload_json TEXT NOT NULL,
+  registered_at TEXT NOT NULL, implementation_commit TEXT NOT NULL
+);
+CREATE TABLE IF NOT EXISTS lab_hypotheses (
+  hypothesis_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL REFERENCES lab_manifests(manifest_hash),
+  family TEXT NOT NULL, sibling_variant_count INTEGER NOT NULL,
+  payload_json TEXT NOT NULL, definition_hash TEXT NOT NULL UNIQUE
+);
+CREATE TABLE IF NOT EXISTS lab_sources (
+  source_hash TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,
+  payload_json TEXT NOT NULL, quality_report_json TEXT NOT NULL,
+  quality_report_hash TEXT NOT NULL UNIQUE
+);
+CREATE TABLE IF NOT EXISTS lab_runs (
+  run_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL REFERENCES lab_manifests(manifest_hash),
+  source_hash TEXT NOT NULL REFERENCES lab_sources(source_hash),
+  run_class TEXT NOT NULL CHECK(run_class IN ('SYNTHETIC_FIXTURE','AUTHORIZED_HISTORICAL_STAGE1')),
+  started_at TEXT NOT NULL, implementation_commit TEXT NOT NULL,
+  result_hash TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL
+);
+CREATE TRIGGER IF NOT EXISTS immutable_lab_manifests_update BEFORE UPDATE ON lab_manifests
+BEGIN SELECT RAISE(ABORT, 'lab manifests are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_lab_manifests_delete BEFORE DELETE ON lab_manifests
+BEGIN SELECT RAISE(ABORT, 'lab manifests are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_lab_hypotheses_update BEFORE UPDATE ON lab_hypotheses
+BEGIN SELECT RAISE(ABORT, 'lab hypotheses are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_lab_hypotheses_delete BEFORE DELETE ON lab_hypotheses
+BEGIN SELECT RAISE(ABORT, 'lab hypotheses are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_lab_sources_update BEFORE UPDATE ON lab_sources
+BEGIN SELECT RAISE(ABORT, 'lab sources are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_lab_sources_delete BEFORE DELETE ON lab_sources
+BEGIN SELECT RAISE(ABORT, 'lab sources are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_lab_runs_update BEFORE UPDATE ON lab_runs
+BEGIN SELECT RAISE(ABORT, 'lab runs are immutable'); END;
+CREATE TRIGGER IF NOT EXISTS immutable_lab_runs_delete BEFORE DELETE ON lab_runs
+BEGIN SELECT RAISE(ABORT, 'lab runs are immutable'); END;
+"""
+
+
+def signal_lab_schema_hash() -> str:
+    return sha256_canonical({"schema": LAB_SCHEMA})
+
+
+def connect_signal_lab(
+    path: str | Path, isolated_root: str | Path
+) -> sqlite3.Connection:
+    root = Path(isolated_root).resolve()
+    required_root = (root / "data" / "phase3_signal_lab").resolve()
+    resolved = Path(path).resolve()
+    if required_root != resolved.parent and required_root not in resolved.parents:
+        raise ValueError("Signal Lab database must remain under data/phase3_signal_lab")
+    lowered = str(resolved).lower()
+    if "phase2_epoch" in lowered or "epoch_001.sqlite3" in lowered:
+        raise ValueError("production evidence databases are prohibited")
+    resolved.parent.mkdir(parents=True, exist_ok=True)
+    db = sqlite3.connect(resolved)
+    db.row_factory = sqlite3.Row
+    db.execute("PRAGMA foreign_keys = ON")
+    db.executescript(LAB_SCHEMA)
+    db.commit()
+    return db
+
+
+class SignalLabRepository:
+    def __init__(self, db: sqlite3.Connection) -> None:
+        self.db = db
+
+    def register_manifest(
+        self,
+        manifest: BatchManifest,
+        *,
+        registered_at: datetime,
+        implementation_commit: str,
+    ) -> None:
+        payload = canonical_json(manifest)
+        try:
+            self.db.execute(
+                "INSERT INTO lab_manifests VALUES (?,?,?,?,?,?)",
+                (
+                    manifest.manifest_hash,
+                    manifest.batch_id,
+                    manifest.status,
+                    payload,
+                    registered_at.astimezone(UTC).isoformat(),
+                    implementation_commit,
+                ),
+            )
+            for hypothesis in manifest.hypotheses:
+                definition_payload = canonical_json(hypothesis)
+                self.db.execute(
+                    "INSERT INTO lab_hypotheses VALUES (?,?,?,?,?,?)",
+                    (
+                        hypothesis.hypothesis_id,
+                        manifest.manifest_hash,
+                        hypothesis.family,
+                        hypothesis.sibling_variant_count,
+                        definition_payload,
+                        sha256_canonical(hypothesis),
+                    ),
+                )
+            self.db.commit()
+        except sqlite3.IntegrityError:
+            self.db.rollback()
+            row = self.db.execute(
+                "SELECT payload_json, implementation_commit FROM lab_manifests WHERE batch_id=?",
+                (manifest.batch_id,),
+            ).fetchone()
+            if (
+                row is None
+                or row["payload_json"] != payload
+                or row["implementation_commit"] != implementation_commit
+            ):
+                raise RuntimeError("immutable Signal Lab manifest conflict")
+
+    def register_source(
+        self, provenance: SourceProvenance, quality: DataQualityReport
+    ) -> None:
+        self.db.execute(
+            "INSERT INTO lab_sources VALUES (?,?,?,?,?)",
+            (
+                provenance.source_hash,
+                provenance.source_id,
+                canonical_json(provenance),
+                canonical_json(quality.__dict__),
+                quality.report_hash,
+            ),
+        )
+        self.db.commit()
+
+    def save_run(
+        self,
+        result: Mapping[str, Any],
+        *,
+        source_hash: str,
+        run_class: Literal["SYNTHETIC_FIXTURE", "AUTHORIZED_HISTORICAL_STAGE1"],
+        started_at: datetime,
+        implementation_commit: str,
+    ) -> str:
+        if run_class == "AUTHORIZED_HISTORICAL_STAGE1" and result.get(
+            "active_epoch_evidence"
+        ):
+            raise PermissionError("active epoch evidence cannot enter the Signal Lab")
+        result_hash = str(result["result_hash"])
+        unhashed_result = dict(result)
+        unhashed_result.pop("result_hash")
+        if sha256_canonical(unhashed_result) != result_hash:
+            raise ValueError("Signal Lab result hash does not match its payload")
+        run_id = str(uuid5(NAMESPACE_URL, f"signal-lab-run:{result_hash}"))
+        self.db.execute(
+            "INSERT INTO lab_runs VALUES (?,?,?,?,?,?,?,?)",
+            (
+                run_id,
+                result["manifest_hash"],
+                source_hash,
+                run_class,
+                started_at.astimezone(UTC).isoformat(),
+                implementation_commit,
+                result_hash,
+                canonical_json(result),
+            ),
+        )
+        self.db.commit()
+        return run_id
+
+    def integrity(self) -> tuple[str, int]:
+        return (
+            str(self.db.execute("PRAGMA integrity_check").fetchone()[0]),
+            len(self.db.execute("PRAGMA foreign_key_check").fetchall()),
+        )
```

## Reviewer questions

- Are the causal cutoff and exact-timestamp outcome joins sufficient to prevent lookahead?
- Is the 14-test BH-FDR family and fit/confirmation contract adequately preregistered?
- Are the RiskReceipt fail-closed gates and future parity tolerances conservative enough?
