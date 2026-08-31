from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class TrendRegime(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class VolatilityRegime(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class Regime(BaseModel):
    model_config = ConfigDict(frozen=True)
    trend: TrendRegime
    volatility: VolatilityRegime
    combined: str
    version: str = "REGIME_V1"

