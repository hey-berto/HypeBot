from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import floor, log
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import yaml
from arch.bootstrap import StationaryBootstrap
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase3.gate import automatic_stationary_block_length


class LabOutcome(StrEnum):
    PASS_STAGE_1 = "PASS_STAGE_1"
    WEAK = "WEAK"
    REJECT = "REJECT"
    DATA_INVALID = "DATA_INVALID"


class HypothesisDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str
    family: str
    definition: str
    directionality: str
    thresholds: Mapping[str, float | int]
    sibling_variant_count: int = Field(ge=1)
    source_fields: tuple[str, ...]
    provenance: str
    causal_cutoff: str
    horizons_minutes: tuple[int, ...]
    designation: str
    split_id: str
    multiple_testing_family_id: str


class BatchManifest(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    lab_version: Literal["CANDIDATE_SIGNAL_LAB_V1"]
    batch_id: Literal["HYPE_BATCH_001"]
    status: Literal["FROZEN_NOT_EXECUTED"]
    data_domain: Literal["HISTORICAL_NON_PHASE2_ONLY"]
    production_epoch_access: Literal["PROHIBITED"]
    llm_calls_allowed: Literal[False]
    stage2_enabled: Literal[False]
    normalization: Mapping[str, Any]
    horizons: Mapping[str, Any]
    split: Mapping[str, Any]
    multiple_testing: Mapping[str, Any]
    statistics: Mapping[str, Any]
    promotion: Mapping[str, Any]
    source_policy: Mapping[str, Any]
    code_identity: Mapping[str, Any]
    hypotheses: tuple[HypothesisDefinition, ...]

    @model_validator(mode="after")
    def validate_batch_freeze(self) -> BatchManifest:
        expected = (
            "HYPE_FUNDING_EXTREME_CHANGE_V1",
            "HYPE_FUNDING_OI_INTERACTION_V1",
            "HYPE_AGGRESSOR_CVD_V1",
            "HYPE_TRADE_COUNT_DELTA_V1",
            "HYPE_PRICE_CVD_DIVERGENCE_V1",
            "HYPE_BTC_RELATIVE_MOMENTUM_V1",
            "HYPE_OI_PRICE_DIVERGENCE_V1",
        )
        if tuple(item.hypothesis_id for item in self.hypotheses) != expected:
            raise ValueError(
                "Batch 1 must contain the seven hypotheses in frozen order"
            )
        if tuple(self.horizons["primary_minutes"]) != (60, 240):
            raise ValueError("primary horizons are frozen at 1h and 4h")
        if tuple(self.horizons["exploratory_minutes"]) != (15, 720):
            raise ValueError("exploratory horizons are frozen at 15m and 12h")
        if self.multiple_testing.get("method") != "benjamini_hochberg":
            raise ValueError("Batch 1 multiple testing method is frozen to BH-FDR")
        if int(self.multiple_testing.get("family_size", 0)) != 14:
            raise ValueError("Batch 1 primary BH-FDR family must contain 14 tests")
        if float(self.split.get("fit_fraction", 0.0)) != 0.60:
            raise ValueError("Batch 1 chronological split is frozen at 60/40")
        if self.source_policy.get("phase2_database_reads_allowed") is not False:
            raise ValueError("the Candidate Signal Lab cannot read Phase 2 databases")
        return self

    @property
    def manifest_hash(self) -> str:
        return sha256_canonical(self)


def load_batch_manifest(path: str | Path) -> BatchManifest:
    return BatchManifest.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    provider: str
    dataset: str
    retrieval_timestamp: datetime
    coverage_start: datetime
    coverage_end: datetime
    source_hash: str = Field(min_length=64, max_length=64)
    timestamp_semantics: str
    aggressor_side_semantics: str | None = None
    reproducible_locator: str

    @model_validator(mode="after")
    def validate_time_range(self) -> SourceProvenance:
        for value in (
            self.retrieval_timestamp,
            self.coverage_start,
            self.coverage_end,
        ):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(
                    "source provenance timestamps must be normalized to UTC"
                )
        if self.coverage_end <= self.coverage_start:
            raise ValueError("source coverage must have positive duration")
        if not self.reproducible_locator.strip():
            raise ValueError("a reproducible source locator is required")
        return self


class MarketObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime
    hype_close: float
    btc_close: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    aggressor_buy_notional: float | None = None
    aggressor_sell_notional: float | None = None
    buy_trade_count: int | None = Field(default=None, ge=0)
    sell_trade_count: int | None = Field(default=None, ge=0)
    average_buy_trade_notional: float | None = None
    average_sell_trade_notional: float | None = None
    regime: str | None = None

    @model_validator(mode="after")
    def validate_values(self) -> MarketObservation:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("market timestamps must be normalized to UTC")
        numeric = self.model_dump(mode="python", exclude={"timestamp", "regime"})
        for name, value in numeric.items():
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.hype_close <= 0 or (self.btc_close is not None and self.btc_close <= 0):
            raise ValueError("close prices must be positive")
        if self.open_interest is not None and self.open_interest <= 0:
            raise ValueError("open interest must be positive")
        return self


class AggressorSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class TradePrint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_id: str
    timestamp: datetime
    price: float = Field(gt=0)
    size: float = Field(gt=0)
    aggressor_side: AggressorSide

    @model_validator(mode="after")
    def validate_timestamp(self) -> TradePrint:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("trade timestamps must be normalized to UTC")
        return self


@dataclass(frozen=True)
class TradeFlowAggregate:
    interval_start: datetime
    interval_end: datetime
    aggressor_buy_notional: float
    aggressor_sell_notional: float
    buy_trade_count: int
    sell_trade_count: int
    average_buy_trade_notional: float | None
    average_sell_trade_notional: float | None
    semantics: str
    aggregate_hash: str


def aggregate_trade_flow(
    trades: Sequence[TradePrint], interval_start: datetime, interval_end: datetime
) -> TradeFlowAggregate:
    if interval_start.tzinfo is None or interval_end.tzinfo is None:
        raise ValueError("aggregation interval must be timezone-aware")
    start = interval_start.astimezone(UTC)
    end = interval_end.astimezone(UTC)
    if end <= start:
        raise ValueError("aggregation interval must have positive duration")
    seen: set[str] = set()
    buys: list[float] = []
    sells: list[float] = []
    for trade in sorted(trades, key=lambda item: (item.timestamp, item.trade_id)):
        if trade.trade_id in seen:
            raise ValueError(f"duplicate trade id: {trade.trade_id}")
        seen.add(trade.trade_id)
        if not (start <= trade.timestamp < end):
            continue
        notional = trade.price * trade.size
        (buys if trade.aggressor_side == AggressorSide.BUY else sells).append(notional)
    payload = {
        "interval_start": start,
        "interval_end": end,
        "aggressor_buy_notional": sum(buys),
        "aggressor_sell_notional": sum(sells),
        "buy_trade_count": len(buys),
        "sell_trade_count": len(sells),
        "average_buy_trade_notional": sum(buys) / len(buys) if buys else None,
        "average_sell_trade_notional": sum(sells) / len(sells) if sells else None,
        "semantics": "BUY_IS_TAKER_BUY_SELL_IS_TAKER_SELL_INTERVAL_START_INCLUSIVE_END_EXCLUSIVE",
    }
    return TradeFlowAggregate(**payload, aggregate_hash=sha256_canonical(payload))


@dataclass(frozen=True)
class DataQualityReport:
    valid: bool
    issues: tuple[str, ...]
    missing_timestamps: tuple[str, ...]
    available_fields: tuple[str, ...]
    hypothesis_status: Mapping[str, str]
    report_hash: str


def validate_market_data(
    observations: Sequence[MarketObservation],
    provenance: SourceProvenance,
    manifest: BatchManifest,
) -> DataQualityReport:
    issues: list[str] = []
    if not observations:
        issues.append("EMPTY_DATASET")
    timestamps = [item.timestamp for item in observations]
    if timestamps != sorted(timestamps):
        issues.append("NON_MONOTONIC_TIMESTAMPS")
    if len(set(timestamps)) != len(timestamps):
        issues.append("DUPLICATE_TIMESTAMPS")
    if timestamps and min(timestamps) < provenance.coverage_start:
        issues.append("OBSERVATION_BEFORE_PROVENANCE_COVERAGE")
    if timestamps and max(timestamps) > provenance.coverage_end:
        issues.append("OBSERVATION_AFTER_PROVENANCE_COVERAGE")
    expected_delta = timedelta(
        minutes=int(manifest.source_policy["expected_interval_minutes"])
    )
    missing: list[str] = []
    if timestamps:
        cursor = min(timestamps)
        observed = set(timestamps)
        while cursor <= max(timestamps):
            if cursor not in observed:
                missing.append(cursor.isoformat())
            cursor += expected_delta
    if missing:
        issues.append("MISSING_INTERVALS")
    available = tuple(
        sorted(
            name
            for name in MarketObservation.model_fields
            if name not in {"timestamp", "regime"}
            and observations
            and all(getattr(row, name) is not None for row in observations)
        )
    )
    hypothesis_status: dict[str, str] = {}
    for hypothesis in manifest.hypotheses:
        absent = sorted(
            set(hypothesis.source_fields) - ({"timestamp"} | set(available))
        )
        if absent:
            hypothesis_status[hypothesis.hypothesis_id] = (
                "DATA_INVALID:MISSING_FIELDS:" + ",".join(absent)
            )
        elif (
            hypothesis.family.startswith("order_flow")
            and not provenance.aggressor_side_semantics
        ):
            hypothesis_status[hypothesis.hypothesis_id] = (
                "DATA_INVALID:AGGRESSOR_SIDE_SEMANTICS_UNPROVEN"
            )
        else:
            hypothesis_status[hypothesis.hypothesis_id] = "DATA_VALID"
    payload = {
        "issues": sorted(set(issues)),
        "missing_timestamps": missing,
        "available_fields": available,
        "hypothesis_status": hypothesis_status,
        "provenance_hash": sha256_canonical(provenance),
        "input_hash": sha256_canonical(observations),
    }
    return DataQualityReport(
        valid=not issues,
        issues=tuple(payload["issues"]),
        missing_timestamps=tuple(missing),
        available_fields=available,
        hypothesis_status=hypothesis_status,
        report_hash=sha256_canonical(payload),
    )


def _rolling_z(values: Sequence[float | None], index: int, window: int) -> float | None:
    if index + 1 < window:
        return None
    sample = values[index + 1 - window : index + 1]
    if any(value is None or not math.isfinite(float(value)) for value in sample):
        return None
    array = np.asarray(sample, dtype=float)
    deviation = float(np.std(array, ddof=1))
    if deviation == 0:
        return 0.0
    return (float(array[-1]) - float(np.mean(array))) / deviation


def _log_change(values: Sequence[float | None], index: int, lag: int) -> float | None:
    if index < lag or values[index] is None or values[index - lag] is None:
        return None
    current = float(values[index])
    previous = float(values[index - lag])
    if current <= 0 or previous <= 0:
        return None
    return log(current / previous)


def compute_signal_series(
    observations: Sequence[MarketObservation], hypothesis_id: str
) -> tuple[float | None, ...]:
    fields = {
        name: [getattr(row, name) for row in observations]
        for name in MarketObservation.model_fields
        if name != "timestamp"
    }
    funding_changes: list[float | None] = [None]
    funding = fields["funding_rate"]
    for index in range(1, len(observations)):
        if funding[index] is None or funding[index - 1] is None:
            funding_changes.append(None)
        else:
            funding_changes.append(float(funding[index]) - float(funding[index - 1]))
    cvd4: list[float | None] = []
    count4: list[float | None] = []
    for index in range(len(observations)):
        if index < 3:
            cvd4.append(None)
            count4.append(None)
            continue
        buy = fields["aggressor_buy_notional"][index - 3 : index + 1]
        sell = fields["aggressor_sell_notional"][index - 3 : index + 1]
        if any(value is None for value in (*buy, *sell)):
            cvd4.append(None)
        else:
            cvd4.append(sum(float(v) for v in buy) - sum(float(v) for v in sell))
        buy_count = fields["buy_trade_count"][index - 3 : index + 1]
        sell_count = fields["sell_trade_count"][index - 3 : index + 1]
        if any(value is None for value in (*buy_count, *sell_count)):
            count4.append(None)
        else:
            ratios = []
            for b, s in zip(buy_count, sell_count, strict=True):
                total = int(b) + int(s)
                ratios.append((int(b) - int(s)) / total if total else 0.0)
            count4.append(float(np.mean(ratios)))
    hype_return4 = [
        _log_change(fields["hype_close"], index, 4)
        for index in range(len(observations))
    ]
    btc_return4 = [
        _log_change(fields["btc_close"], index, 4) for index in range(len(observations))
    ]
    oi_change1 = [
        _log_change(fields["open_interest"], index, 1)
        for index in range(len(observations))
    ]
    oi_change4 = [
        _log_change(fields["open_interest"], index, 4)
        for index in range(len(observations))
    ]
    output: list[float | None] = []
    for index in range(len(observations)):
        if hypothesis_id == "HYPE_FUNDING_EXTREME_CHANGE_V1":
            a = _rolling_z(funding, index, 96)
            b = _rolling_z(funding_changes, index, 24)
            value = -a - 0.5 * b if a is not None and b is not None else None
        elif hypothesis_id == "HYPE_FUNDING_OI_INTERACTION_V1":
            a = _rolling_z(funding, index, 96)
            b = _rolling_z(oi_change1, index, 24)
            value = -a * max(b, 0.0) if a is not None and b is not None else None
        elif hypothesis_id == "HYPE_AGGRESSOR_CVD_V1":
            value = _rolling_z(cvd4, index, 96)
        elif hypothesis_id == "HYPE_TRADE_COUNT_DELTA_V1":
            value = _rolling_z(count4, index, 96)
        elif hypothesis_id == "HYPE_PRICE_CVD_DIVERGENCE_V1":
            a = _rolling_z(cvd4, index, 96)
            b = _rolling_z(hype_return4, index, 96)
            value = a - b if a is not None and b is not None else None
        elif hypothesis_id == "HYPE_BTC_RELATIVE_MOMENTUM_V1":
            spread = [
                h - b if h is not None and b is not None else None
                for h, b in zip(hype_return4, btc_return4, strict=True)
            ]
            value = _rolling_z(spread, index, 96)
        elif hypothesis_id == "HYPE_OI_PRICE_DIVERGENCE_V1":
            a = _rolling_z(hype_return4, index, 96)
            b = _rolling_z(oi_change4, index, 96)
            value = a - b if a is not None and b is not None else None
        else:
            raise KeyError(f"unknown frozen hypothesis: {hypothesis_id}")
        output.append(value)
    return tuple(output)


def build_forward_returns(
    observations: Sequence[MarketObservation], horizons_minutes: Sequence[int]
) -> tuple[Mapping[int, float | None], ...]:
    by_timestamp = {row.timestamp: row.hype_close for row in observations}
    output: list[dict[int, float | None]] = []
    for row in observations:
        horizon_values: dict[int, float | None] = {}
        for minutes in horizons_minutes:
            future = by_timestamp.get(row.timestamp + timedelta(minutes=minutes))
            horizon_values[int(minutes)] = (
                log(float(future) / row.hype_close) if future is not None else None
            )
        output.append(horizon_values)
    return tuple(output)


def benjamini_hochberg(
    p_values: Mapping[str, float], q: float
) -> dict[str, dict[str, float | bool]]:
    if not 0 < q < 1:
        raise ValueError("BH-FDR q must be between zero and one")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    largest_rejected = 0
    for rank, (_, p_value) in enumerate(ordered, start=1):
        if not 0 <= p_value <= 1:
            raise ValueError("p-values must be in [0,1]")
        if p_value <= q * rank / m:
            largest_rejected = rank
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank in range(m, 0, -1):
        key, p_value = ordered[rank - 1]
        running = min(running, p_value * m / rank)
        adjusted[key] = running
    return {
        key: {
            "p_value": value,
            "adjusted_p_value": adjusted[key],
            "rejected": rank <= largest_rejected,
        }
        for rank, (key, value) in enumerate(ordered, start=1)
    }


@dataclass(frozen=True)
class PartitionEffect:
    count: int
    mean: float | None
    confidence_interval: tuple[float, float] | None
    p_value: float
    block_length: int
    lag1_autocorrelation: float


def _partition_effect(
    values: Sequence[float], *, repetitions: int, confidence_level: float, seed: int
) -> PartitionEffect:
    if not values:
        return PartitionEffect(0, None, None, 1.0, 1, 0.0)
    data = np.asarray(values, dtype=float)
    observed = float(np.mean(data))
    block = automatic_stationary_block_length(data)
    if len(data) == 1 or float(np.var(data)) == 0.0:
        p_value = 1.0 / (repetitions + 1) if observed > 0 else 1.0
        ci = (observed, observed)
    else:
        rng = np.random.default_rng(seed)
        direct = StationaryBootstrap(block, data, seed=rng)
        means = np.asarray(
            [
                float(np.mean(positional[0]))
                for positional, _ in direct.bootstrap(repetitions)
            ]
        )
        alpha = 1.0 - confidence_level
        ci = tuple(float(v) for v in np.quantile(means, [alpha / 2, 1 - alpha / 2]))
        centered = data - observed
        null = StationaryBootstrap(
            block, centered, seed=np.random.default_rng(seed + 10_000)
        )
        null_means = [
            float(np.mean(positional[0]))
            for positional, _ in null.bootstrap(repetitions)
        ]
        p_value = (1 + sum(value >= observed for value in null_means)) / (
            repetitions + 1
        )
    lag1 = (
        float(np.corrcoef(data[:-1], data[1:])[0, 1])
        if len(data) > 2 and float(np.var(data)) > 0
        else 0.0
    )
    if not math.isfinite(lag1):
        lag1 = 0.0
    return PartitionEffect(len(data), observed, ci, p_value, block, lag1)


def _directional_values(
    signals: Sequence[float | None],
    returns: Sequence[float | None],
    indices: range,
    threshold: float,
) -> list[float]:
    return [
        math.copysign(1.0, float(signals[index])) * float(returns[index])
        for index in indices
        if signals[index] is not None
        and returns[index] is not None
        and abs(float(signals[index])) >= threshold
    ]


def screen_batch(
    observations: Sequence[MarketObservation],
    provenance: SourceProvenance,
    manifest: BatchManifest,
    *,
    repetitions_override: int | None = None,
) -> dict[str, Any]:
    """Run the frozen Stage-1 screen; callers must separately authorize real data use."""
    if provenance.dataset.startswith("phase2_epoch_"):
        raise PermissionError(
            "active or historical Phase 2 evidence is outside Lab scope"
        )
    quality = validate_market_data(observations, provenance, manifest)
    horizons = tuple(manifest.horizons["primary_minutes"]) + tuple(
        manifest.horizons["exploratory_minutes"]
    )
    forward = build_forward_returns(observations, horizons)
    split_at = floor(len(observations) * float(manifest.split["fit_fraction"]))
    repetitions = repetitions_override or int(
        manifest.statistics["bootstrap_resamples"]
    )
    threshold = float(manifest.normalization["event_threshold_abs_z"])
    reports: dict[str, dict[str, Any]] = {}
    primary_p_values: dict[str, float] = {}
    for hypothesis_index, hypothesis in enumerate(manifest.hypotheses):
        signals = compute_signal_series(observations, hypothesis.hypothesis_id)
        status = quality.hypothesis_status[hypothesis.hypothesis_id]
        horizon_reports: dict[str, Any] = {}
        for horizon_index, minutes in enumerate(horizons):
            returns = [item[int(minutes)] for item in forward]
            fit_values = _directional_values(
                signals, returns, range(split_at), threshold
            )
            confirmation_values = _directional_values(
                signals, returns, range(split_at, len(observations)), threshold
            )
            seed = 1_000 + hypothesis_index * 100 + horizon_index
            fit_effect = _partition_effect(
                fit_values,
                repetitions=repetitions,
                confidence_level=float(manifest.statistics["confidence_level"]),
                seed=seed,
            )
            confirmation_effect = _partition_effect(
                confirmation_values,
                repetitions=repetitions,
                confidence_level=float(manifest.statistics["confidence_level"]),
                seed=seed + 50,
            )
            sign_agreement = (
                fit_effect.count > 0
                and confirmation_effect.count > 0
                and fit_effect.mean is not None
                and confirmation_effect.mean is not None
                and math.copysign(1.0, fit_effect.mean)
                == math.copysign(1.0, confirmation_effect.mean)
            )
            test_id = f"{hypothesis.hypothesis_id}:{minutes}m"
            if int(minutes) in manifest.horizons["primary_minutes"]:
                primary_p_values[test_id] = (
                    confirmation_effect.p_value if status == "DATA_VALID" else 1.0
                )
            horizon_reports[str(minutes)] = {
                "designation": "PRIMARY"
                if int(minutes) in manifest.horizons["primary_minutes"]
                else "EXPLORATORY",
                "fit": fit_effect.__dict__,
                "confirmation": confirmation_effect.__dict__,
                "fit_confirmation_sign_agreement": sign_agreement,
                "severe_clustering_warning": abs(
                    confirmation_effect.lag1_autocorrelation
                )
                >= float(manifest.statistics["clustering_warning_abs_lag1"]),
            }
        reports[hypothesis.hypothesis_id] = {
            "data_status": status,
            "horizons": horizon_reports,
        }
    if len(primary_p_values) != int(manifest.multiple_testing["family_size"]):
        raise RuntimeError("primary BH-FDR family does not match the frozen size")
    bh = benjamini_hochberg(primary_p_values, float(manifest.multiple_testing["fdr_q"]))
    for hypothesis in manifest.hypotheses:
        report = reports[hypothesis.hypothesis_id]
        qualifying: list[str] = []
        weak = False
        for minutes in manifest.horizons["primary_minutes"]:
            key = f"{hypothesis.hypothesis_id}:{minutes}m"
            item = report["horizons"][str(minutes)]
            item["bh_fdr"] = bh[key]
            fit = item["fit"]
            confirmation = item["confirmation"]
            enough = fit["count"] >= int(
                manifest.statistics["minimum_fit_events"]
            ) and confirmation["count"] >= int(
                manifest.statistics["minimum_confirmation_events"]
            )
            positive_stable = (
                item["fit_confirmation_sign_agreement"]
                and fit["mean"] is not None
                and confirmation["mean"] is not None
                and fit["mean"] > 0
                and confirmation["mean"] > 0
            )
            if enough and positive_stable:
                weak = True
            if enough and positive_stable and bh[key]["rejected"]:
                qualifying.append(key)
        if report["data_status"] != "DATA_VALID" or not quality.valid:
            outcome = LabOutcome.DATA_INVALID
        elif qualifying:
            outcome = LabOutcome.PASS_STAGE_1
        elif weak:
            outcome = LabOutcome.WEAK
        else:
            outcome = LabOutcome.REJECT
        report["outcome"] = outcome.value
        report["qualifying_primary_tests"] = qualifying
        report["exploratory_can_promote"] = False
    payload = {
        "lab_version": manifest.lab_version,
        "batch_id": manifest.batch_id,
        "manifest_hash": manifest.manifest_hash,
        "input_hash": sha256_canonical(observations),
        "provenance_hash": sha256_canonical(provenance),
        "data_quality": quality.__dict__,
        "split_index": split_at,
        "bh_family_id": manifest.multiple_testing["family_id"],
        "bh_family_size": len(primary_p_values),
        "reports": reports,
        "stage2_executed": False,
        "active_epoch_evidence": False,
    }
    return {**payload, "result_hash": sha256_canonical(payload)}


LAB_SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_manifests (
  manifest_hash TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL, payload_json TEXT NOT NULL,
  registered_at TEXT NOT NULL, implementation_commit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_hypotheses (
  hypothesis_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL REFERENCES lab_manifests(manifest_hash),
  family TEXT NOT NULL, sibling_variant_count INTEGER NOT NULL,
  payload_json TEXT NOT NULL, definition_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS lab_sources (
  source_hash TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL, quality_report_json TEXT NOT NULL,
  quality_report_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS lab_runs (
  run_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL REFERENCES lab_manifests(manifest_hash),
  source_hash TEXT NOT NULL REFERENCES lab_sources(source_hash),
  run_class TEXT NOT NULL CHECK(run_class IN ('SYNTHETIC_FIXTURE','AUTHORIZED_HISTORICAL_STAGE1')),
  started_at TEXT NOT NULL, implementation_commit TEXT NOT NULL,
  result_hash TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_lab_manifests_update BEFORE UPDATE ON lab_manifests
BEGIN SELECT RAISE(ABORT, 'lab manifests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_lab_manifests_delete BEFORE DELETE ON lab_manifests
BEGIN SELECT RAISE(ABORT, 'lab manifests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_lab_hypotheses_update BEFORE UPDATE ON lab_hypotheses
BEGIN SELECT RAISE(ABORT, 'lab hypotheses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_lab_hypotheses_delete BEFORE DELETE ON lab_hypotheses
BEGIN SELECT RAISE(ABORT, 'lab hypotheses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_lab_sources_update BEFORE UPDATE ON lab_sources
BEGIN SELECT RAISE(ABORT, 'lab sources are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_lab_sources_delete BEFORE DELETE ON lab_sources
BEGIN SELECT RAISE(ABORT, 'lab sources are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_lab_runs_update BEFORE UPDATE ON lab_runs
BEGIN SELECT RAISE(ABORT, 'lab runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_lab_runs_delete BEFORE DELETE ON lab_runs
BEGIN SELECT RAISE(ABORT, 'lab runs are immutable'); END;
"""


def signal_lab_schema_hash() -> str:
    return sha256_canonical({"schema": LAB_SCHEMA})


def connect_signal_lab(
    path: str | Path, isolated_root: str | Path
) -> sqlite3.Connection:
    root = Path(isolated_root).resolve()
    required_root = (root / "data" / "phase3_signal_lab").resolve()
    resolved = Path(path).resolve()
    if required_root != resolved.parent and required_root not in resolved.parents:
        raise ValueError("Signal Lab database must remain under data/phase3_signal_lab")
    lowered = str(resolved).lower()
    if "phase2_epoch" in lowered or "epoch_001.sqlite3" in lowered:
        raise ValueError("production evidence databases are prohibited")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(resolved)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(LAB_SCHEMA)
    db.commit()
    return db


class SignalLabRepository:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def register_manifest(
        self,
        manifest: BatchManifest,
        *,
        registered_at: datetime,
        implementation_commit: str,
    ) -> None:
        payload = canonical_json(manifest)
        try:
            self.db.execute(
                "INSERT INTO lab_manifests VALUES (?,?,?,?,?,?)",
                (
                    manifest.manifest_hash,
                    manifest.batch_id,
                    manifest.status,
                    payload,
                    registered_at.astimezone(UTC).isoformat(),
                    implementation_commit,
                ),
            )
            for hypothesis in manifest.hypotheses:
                definition_payload = canonical_json(hypothesis)
                self.db.execute(
                    "INSERT INTO lab_hypotheses VALUES (?,?,?,?,?,?)",
                    (
                        hypothesis.hypothesis_id,
                        manifest.manifest_hash,
                        hypothesis.family,
                        hypothesis.sibling_variant_count,
                        definition_payload,
                        sha256_canonical(hypothesis),
                    ),
                )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            row = self.db.execute(
                "SELECT payload_json, implementation_commit FROM lab_manifests WHERE batch_id=?",
                (manifest.batch_id,),
            ).fetchone()
            if (
                row is None
                or row["payload_json"] != payload
                or row["implementation_commit"] != implementation_commit
            ):
                raise RuntimeError("immutable Signal Lab manifest conflict")

    def register_source(
        self, provenance: SourceProvenance, quality: DataQualityReport
    ) -> None:
        self.db.execute(
            "INSERT INTO lab_sources VALUES (?,?,?,?,?)",
            (
                provenance.source_hash,
                provenance.source_id,
                canonical_json(provenance),
                canonical_json(quality.__dict__),
                quality.report_hash,
            ),
        )
        self.db.commit()

    def save_run(
        self,
        result: Mapping[str, Any],
        *,
        source_hash: str,
        run_class: Literal["SYNTHETIC_FIXTURE", "AUTHORIZED_HISTORICAL_STAGE1"],
        started_at: datetime,
        implementation_commit: str,
    ) -> str:
        if run_class == "AUTHORIZED_HISTORICAL_STAGE1" and result.get(
            "active_epoch_evidence"
        ):
            raise PermissionError("active epoch evidence cannot enter the Signal Lab")
        result_hash = str(result["result_hash"])
        unhashed_result = dict(result)
        unhashed_result.pop("result_hash")
        if sha256_canonical(unhashed_result) != result_hash:
            raise ValueError("Signal Lab result hash does not match its payload")
        run_id = str(uuid5(NAMESPACE_URL, f"signal-lab-run:{result_hash}"))
        self.db.execute(
            "INSERT INTO lab_runs VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                result["manifest_hash"],
                source_hash,
                run_class,
                started_at.astimezone(UTC).isoformat(),
                implementation_commit,
                result_hash,
                canonical_json(result),
            ),
        )
        self.db.commit()
        return run_id

    def integrity(self) -> tuple[str, int]:
        return (
            str(self.db.execute("PRAGMA integrity_check").fetchone()[0]),
            len(self.db.execute("PRAGMA foreign_key_check").fetchall()),
        )
