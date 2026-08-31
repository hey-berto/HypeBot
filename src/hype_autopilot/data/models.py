from __future__ import annotations

from datetime import datetime

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from hype_autopilot.clock import ensure_utc


class ObservationClass(StrEnum):
    WARMUP = "WARMUP"
    SOAK = "SOAK"
    SCORED_PROSPECTIVE = "SCORED_PROSPECTIVE"


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int | None = None
    received_at: datetime
    observation_class: ObservationClass = ObservationClass.WARMUP

    @field_validator("open_time", "close_time", "received_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def valid_ohlc(self) -> "Candle":
        if self.close_time <= self.open_time:
            raise ValueError("candle close_time must be after open_time")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC range")
        if self.low > self.high or self.volume < 0:
            raise ValueError("invalid candle bounds")
        return self


class AssetContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    source_timestamp: datetime
    received_at: datetime
    mark_price: float | None = None
    mid_price: float | None = None
    oracle_price: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    day_notional_volume: float | None = None

    @field_validator("source_timestamp", "received_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class BboObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    source_timestamp: datetime
    received_at: datetime
    bid_price: float | None = None
    bid_size: float | None = None
    ask_price: float | None = None
    ask_size: float | None = None

    @field_validator("source_timestamp", "received_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class FundingObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    source_timestamp: datetime
    received_at: datetime
    funding_rate: float
    premium: float | None = None
    observation_class: ObservationClass = ObservationClass.WARMUP

    @field_validator("source_timestamp", "received_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        return ensure_utc(value)
