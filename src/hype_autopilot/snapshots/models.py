from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hype_autopilot.data.models import AssetContext, ObservationClass
from hype_autopilot.features.models import FeatureSet
from hype_autopilot.regimes.models import Regime


class DataQuality(BaseModel):
    model_config = ConfigDict(frozen=True)
    required_sources_present: bool
    stale_sources: tuple[str, ...] = ()
    missing_optional_fields: tuple[str, ...] = ()
    source_max_age_seconds: dict[str, float] = Field(default_factory=dict)
    scoreable: bool
    rejection_reasons: tuple[str, ...] = ()


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    hype_context: AssetContext | None = None
    hype_features: FeatureSet
    btc_features: dict[str, float | str | None] = Field(default_factory=dict)
    microstructure: dict[str, float | None] = Field(default_factory=dict)


class DecisionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    snapshot_timestamp: datetime
    created_at: datetime
    snapshot_schema_version: str = "1.0.0"
    feature_schema_version: str = "1.0.0"
    epoch_id: str
    observation_class: ObservationClass = ObservationClass.SOAK
    market: MarketSnapshot
    regime: Regime
    data_quality: DataQuality
    source_cutoffs: dict[str, datetime]
    snapshot_hash: str | None = None

    @model_validator(mode="after")
    def reject_nonfinite_scoreable(self) -> "DecisionSnapshot":
        if self.data_quality.scoreable:
            def visit(value: Any) -> bool:
                if isinstance(value, float):
                    return not math.isfinite(value)
                if isinstance(value, dict):
                    return any(visit(v) for v in value.values())
                if isinstance(value, (list, tuple)):
                    return any(visit(v) for v in value)
                return False
            if visit(self.model_dump(mode="python")):
                raise ValueError("scoreable snapshots cannot contain NaN or Infinity")
        return self
