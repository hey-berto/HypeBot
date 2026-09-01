from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hype_autopilot.config import config_hash, load_yaml

ACTIVATION_PHRASE = "start Phase 2 evidence collection"


class InformationBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot_only: bool
    web_browsing: bool
    tools: bool
    live_search: bool
    external_market_lookup: bool
    connectors: bool
    post_snapshot_data: bool

    @model_validator(mode="after")
    def enforce_snapshot_only(self) -> InformationBoundary:
        if not self.snapshot_only or any(
            (
                self.web_browsing,
                self.tools,
                self.live_search,
                self.external_market_lookup,
                self.connectors,
                self.post_snapshot_data,
            )
        ):
            raise ValueError(
                "Phase 2 LLM input must be snapshot-only with every external channel disabled"
            )
        return self


class ResourceIsolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_concurrent_llm_calls: int = Field(ge=1, le=1)
    max_process_memory_mb: int = Field(ge=256)
    max_database_size_mb: int = Field(ge=256)
    process_nice_increment: int = Field(ge=0, le=19)
    api_budget_usd_per_day: float = Field(gt=0)


class Phase2Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    phase2_epoch_id: str
    status: str
    evidence_collection_enabled: bool
    activation_authorized: bool
    schedule_minutes: int = Field(gt=0)
    database_path: str
    snapshot_schema_version: str
    feature_schema_version: str
    quant_trend_version: str
    quant_mean_reversion_version: str
    detector_version: str
    regime_version: str
    llm_strategy_version: str
    provider: str
    model: str
    model_version: str
    reasoning_effort: str
    prompt_version: str
    prompt_path: str
    output_schema_version: str
    hybrid_trend_version: str
    hybrid_mr_version: str
    hybrid_trend_designation: str
    hybrid_mr_designation: str
    llm_vs_quant_trend_designation: str
    llm_vs_quant_mr_designation: str
    simulator_version: str
    llm_geometry_adapter_version: str
    snapshot_to_call_staleness_seconds: int = Field(gt=0)
    malformed_output_max_retries: int = Field(ge=0, le=1)
    request_timeout_seconds: int = Field(gt=0)
    tools_allowed: bool
    input_cost_per_million_usd: float = Field(ge=0)
    cached_input_cost_per_million_usd: float = Field(ge=0)
    output_cost_per_million_usd: float = Field(ge=0)
    resource_isolation: ResourceIsolation
    information_boundary: InformationBoundary

    @model_validator(mode="after")
    def validate_frozen_contract(self) -> Phase2Config:
        if self.schedule_minutes != 15:
            raise ValueError("Phase 2 cadence is frozen at 15 minutes")
        if self.tools_allowed:
            raise ValueError("Phase 2 tools must remain disabled")
        if self.provider != "openai":
            raise ValueError("the pinned Phase 2 provider is openai")
        if self.llm_strategy_version != "LLM_V1":
            raise ValueError("the pinned LLM strategy version is LLM_V1")
        return self

    def assert_build_only(self) -> None:
        if self.evidence_collection_enabled or self.activation_authorized:
            raise RuntimeError(
                "build/test commands require both scored-evidence gates to remain disabled"
            )

    def assert_activation(self, authorization: str) -> None:
        if authorization != ACTIVATION_PHRASE:
            raise PermissionError(f"exact authorization required: {ACTIVATION_PHRASE}")
        if not self.evidence_collection_enabled or not self.activation_authorized:
            raise PermissionError(
                "configuration has not enabled and authorized Phase 2 evidence collection"
            )


def load_phase2_config(path: str | Path) -> tuple[Phase2Config, str]:
    raw = load_yaml(path)
    return Phase2Config.model_validate(raw), config_hash(raw)


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_inside_workspace(value: str | Path, workspace_root: str | Path) -> Path:
    root = Path(workspace_root).resolve()
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("Phase 2 resource must remain inside the isolated worktree")
    return resolved


def config_manifest_fields(config: Phase2Config) -> dict[str, Any]:
    return {
        "snapshot_schema_version": config.snapshot_schema_version,
        "feature_schema_version": config.feature_schema_version,
        "quant_trend_version": config.quant_trend_version,
        "quant_mean_reversion_version": config.quant_mean_reversion_version,
        "detector_version": config.detector_version,
        "regime_version": config.regime_version,
        "llm_strategy_version": config.llm_strategy_version,
        "provider": config.provider,
        "model": config.model,
        "model_version": config.model_version,
        "reasoning_effort": config.reasoning_effort,
        "prompt_version": config.prompt_version,
        "output_schema_version": config.output_schema_version,
        "hybrid_trend_version": config.hybrid_trend_version,
        "hybrid_mr_version": config.hybrid_mr_version,
        "simulator_version": config.simulator_version,
        "llm_geometry_adapter_version": config.llm_geometry_adapter_version,
        "staleness_seconds": config.snapshot_to_call_staleness_seconds,
        "malformed_output_max_retries": config.malformed_output_max_retries,
        "request_timeout_seconds": config.request_timeout_seconds,
        "resource_isolation": config.resource_isolation.model_dump(mode="json"),
        "information_boundary": config.information_boundary.model_dump(mode="json"),
        "pair_designations": {
            "HYBRID_TREND_LLM_V1": config.hybrid_trend_designation,
            "HYBRID_MR_LLM_V1": config.hybrid_mr_designation,
            "LLM_V1_vs_QUANT_TREND_V1": config.llm_vs_quant_trend_designation,
            "LLM_V1_vs_QUANT_MR_V1": config.llm_vs_quant_mr_designation,
        },
    }
