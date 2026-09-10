from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import ceil, sqrt
from pathlib import Path

import numpy as np
import yaml
from arch.bootstrap import StationaryBootstrap, optimal_block_length

from hype_autopilot.hashing import sha256_canonical


class PairVerdict(StrEnum):
    PROMOTE = "PROMOTE"
    REJECT = "REJECT"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class PairRule:
    pair_id: str
    treatment: str
    control: str
    designation: str
    minimum_coeligible: int

    @property
    def confirmatory(self) -> bool:
        return self.designation in {"CONFIRMATORY", "PRIMARY_CONFIRMATORY"}


@dataclass(frozen=True)
class AnalysisGate:
    gate_version: str
    phase2_epoch_id: str
    activation_timestamp: datetime
    calendar_floor_days: int
    earliest_formal_checkpoint: datetime
    bootstrap_resamples: int
    confidence_level: float
    minimum_ess: float
    minimum_trend_states: int
    minimum_volatility_states: int
    minimum_bucket_size: int
    favorable_bucket_fraction: float
    pair_rules: Mapping[str, PairRule]
    triggered_trade_minimums: Mapping[str, int]
    next_checkpoint_delay_days: int
    config_hash: str


@dataclass(frozen=True)
class PairEvidence:
    pair_id: str
    paired_differences: tuple[float, ...]
    cost_adjusted_differences: tuple[float, ...]
    trend_states: tuple[str, ...]
    volatility_states: tuple[str, ...]
    regime_buckets: tuple[str, ...]
    open_position_count: int = 0
    open_position_durations_seconds: tuple[float, ...] = ()

    def validate(self) -> None:
        n = len(self.paired_differences)
        if not (
            len(self.cost_adjusted_differences)
            == len(self.trend_states)
            == len(self.volatility_states)
            == len(self.regime_buckets)
            == n
        ):
            raise ValueError("paired evidence arrays must have identical lengths")
        if self.open_position_count != len(self.open_position_durations_seconds):
            raise ValueError("right-censored open positions require separate durations")


@dataclass(frozen=True)
class GateEvidence:
    phase2_epoch_id: str
    as_of: datetime
    triggered_trade_counts: Mapping[str, int]
    pairs: Mapping[str, PairEvidence]
    evidence_source: str


def load_analysis_gate(path: str | Path) -> AnalysisGate:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    def parse_utc(value: object) -> datetime:
        parsed = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        if parsed.tzinfo is None:
            raise ValueError("gate timestamps must be timezone-aware")
        return parsed.astimezone(UTC)

    activation = parse_utc(payload["activation_timestamp"])
    earliest = parse_utc(payload["earliest_formal_checkpoint"])
    expected = activation + timedelta(days=int(payload["calendar_floor_days"]))
    if earliest != expected:
        raise ValueError("calendar floor and earliest checkpoint disagree")
    rules = {
        pair_id: PairRule(pair_id=pair_id, **value)
        for pair_id, value in payload["comparisons"].items()
    }
    return AnalysisGate(
        gate_version=payload["gate_version"],
        phase2_epoch_id=payload["phase2_epoch_id"],
        activation_timestamp=activation,
        calendar_floor_days=int(payload["calendar_floor_days"]),
        earliest_formal_checkpoint=earliest,
        bootstrap_resamples=int(payload["bootstrap"]["resamples"]),
        confidence_level=float(payload["bootstrap"]["confidence_level"]),
        minimum_ess=float(payload["ess"]["minimum_confirmatory"]),
        minimum_trend_states=int(payload["regime"]["minimum_distinct_trend_states"]),
        minimum_volatility_states=int(
            payload["regime"]["minimum_distinct_volatility_states"]
        ),
        minimum_bucket_size=int(
            payload["regime"]["minimum_coeligible_per_robustness_bucket"]
        ),
        favorable_bucket_fraction=float(
            payload["regime"]["favorable_bucket_fraction_strictly_greater_than"]
        ),
        pair_rules=rules,
        triggered_trade_minimums={
            key: int(value)
            for key, value in payload["triggered_trade_minimums"].items()
        },
        next_checkpoint_delay_days=int(payload["next_checkpoint_delay_days"]),
        config_hash=sha256_canonical(payload),
    )


def lag_corrected_ess(values: Sequence[float]) -> tuple[float, int]:
    data = np.asarray(values, dtype=float)
    n = len(data)
    if n < 3 or float(np.var(data)) == 0.0:
        return float(n), 0
    centered = data - float(np.mean(data))
    denominator = float(np.dot(centered, centered))
    cutoff_threshold = 2.0 / sqrt(n)
    correlations: list[float] = []
    cutoff_lag = n - 1
    for lag in range(1, n):
        rho = float(np.dot(centered[:-lag], centered[lag:]) / denominator)
        if abs(rho) < cutoff_threshold:
            cutoff_lag = lag
            break
        correlations.append(rho)
    divisor = 1.0 + 2.0 * sum(correlations)
    ess = float(n) if divisor <= 0 else n / divisor
    return max(1.0, min(float(n), float(ess))), cutoff_lag


def automatic_stationary_block_length(values: Sequence[float]) -> int:
    data = np.asarray(values, dtype=float)
    if len(data) < 2 or float(np.var(data)) == 0.0:
        return 1
    estimate = float(optimal_block_length(data)["stationary"].iloc[0])
    if not np.isfinite(estimate):
        return 1
    return max(1, min(len(data), ceil(estimate)))


def stationary_mean_ci(
    values: Sequence[float],
    *,
    confidence_level: float,
    repetitions: int,
    seed: int,
) -> tuple[tuple[float, float], int]:
    data = np.asarray(values, dtype=float)
    if len(data) == 0:
        return (float("nan"), float("nan")), 1
    if len(data) == 1 or float(np.var(data)) == 0.0:
        value = float(data[0])
        return (value, value), 1
    block_length = automatic_stationary_block_length(data)
    bootstrap = StationaryBootstrap(
        block_length, data, seed=np.random.default_rng(seed)
    )
    means = [
        float(np.mean(positional[0]))
        for positional, _ in bootstrap.bootstrap(repetitions)
    ]
    alpha = 1.0 - confidence_level
    low, high = np.quantile(np.asarray(means), [alpha / 2.0, 1.0 - alpha / 2.0])
    return (float(low), float(high)), block_length


def _regime_robustness(
    differences: Sequence[float], buckets: Sequence[str], minimum_size: int
) -> tuple[dict[str, float], int, float]:
    grouped: dict[str, list[float]] = {}
    for difference, bucket in zip(differences, buckets, strict=True):
        grouped.setdefault(bucket, []).append(float(difference))
    qualifying = {
        bucket: float(np.mean(values))
        for bucket, values in sorted(grouped.items())
        if len(values) >= minimum_size
    }
    favorable = sum(value > 0 for value in qualifying.values())
    fraction = favorable / len(qualifying) if qualifying else 0.0
    return qualifying, favorable, fraction


def evaluate_pair(
    gate: AnalysisGate,
    evidence: PairEvidence,
    *,
    as_of: datetime,
    triggered_trade_counts: Mapping[str, int],
    repetitions_override: int | None = None,
    evidence_source: str,
    seed: int = 37,
) -> dict[str, object]:
    evidence.validate()
    rule = gate.pair_rules[evidence.pair_id]
    if repetitions_override is not None and evidence_source != "SYNTHETIC_FIXTURE":
        raise PermissionError("bootstrap override is restricted to synthetic fixtures")
    repetitions = repetitions_override or gate.bootstrap_resamples
    values = np.asarray(evidence.paired_differences, dtype=float)
    adjusted = np.asarray(evidence.cost_adjusted_differences, dtype=float)
    ess, cutoff_lag = lag_corrected_ess(values)
    ci, block_length = stationary_mean_ci(
        values,
        confidence_level=gate.confidence_level,
        repetitions=repetitions,
        seed=seed,
    )
    qualifying, favorable_count, favorable_fraction = _regime_robustness(
        values, evidence.regime_buckets, gate.minimum_bucket_size
    )
    halfway = len(values) // 2
    first_half = float(np.mean(values[:halfway])) if halfway else float("nan")
    second_half = (
        float(np.mean(values[halfway:])) if len(values) - halfway else float("nan")
    )
    without_best = np.delete(values, int(np.argmax(values))) if len(values) else values
    requirements = {
        "calendar_floor": as_of.astimezone(UTC) >= gate.earliest_formal_checkpoint,
        "coeligible_minimum": len(values) >= rule.minimum_coeligible,
        "ess_minimum": (ess >= gate.minimum_ess) if rule.confirmatory else True,
        "trend_representation": len(set(evidence.trend_states))
        >= gate.minimum_trend_states,
        "volatility_representation": len(set(evidence.volatility_states))
        >= gate.minimum_volatility_states,
        "triggered_trade_minimums": all(
            triggered_trade_counts.get(strategy, 0) >= minimum
            for strategy, minimum in gate.triggered_trade_minimums.items()
        ),
    }
    minimums_met = all(requirements.values())
    robustness = {
        "ci_favorable": bool(np.isfinite(ci[0]) and ci[0] > 0),
        "survives_best_removal": bool(
            len(without_best) and float(np.mean(without_best)) > 0
        ),
        "regime_majority_favorable": favorable_fraction
        > gate.favorable_bucket_fraction,
        "positive_after_api_cost": bool(len(adjusted) and float(np.mean(adjusted)) > 0),
        "halves_positive": bool(first_half > 0 and second_half > 0),
    }
    if not minimums_met:
        verdict = PairVerdict.INCONCLUSIVE
    elif ci[0] <= 0 <= ci[1] or ci[1] < 0:
        verdict = PairVerdict.REJECT
    elif all(robustness.values()):
        verdict = PairVerdict.PROMOTE
    else:
        verdict = PairVerdict.INCONCLUSIVE
    return {
        "pair_id": evidence.pair_id,
        "designation": rule.designation,
        "verdict": verdict.value,
        "requirements": requirements,
        "minimums_met": minimums_met,
        "n_coeligible_complete": len(values),
        "n_right_censored": evidence.open_position_count,
        "open_position_durations_seconds": evidence.open_position_durations_seconds,
        "effective_sample_size": ess,
        "ess_acf_cutoff_lag": cutoff_lag,
        "stationary_block_length": block_length,
        "bootstrap_resamples": repetitions,
        "confidence_level": gate.confidence_level,
        "mean_difference": float(np.mean(values)) if len(values) else float("nan"),
        "cost_adjusted_mean_difference": float(np.mean(adjusted))
        if len(adjusted)
        else float("nan"),
        "confidence_interval": ci,
        "remove_best_mean": float(np.mean(without_best))
        if len(without_best)
        else float("nan"),
        "first_half_mean": first_half,
        "second_half_mean": second_half,
        "qualifying_regime_bucket_means": qualifying,
        "favorable_regime_bucket_count": favorable_count,
        "favorable_regime_bucket_fraction": favorable_fraction,
        "robustness": robustness,
    }


def evaluate_gate(
    gate: AnalysisGate,
    evidence: GateEvidence,
    *,
    repetitions_override: int | None = None,
) -> dict[str, object]:
    if evidence.phase2_epoch_id != gate.phase2_epoch_id:
        raise ValueError("evidence epoch does not match preregistered gate")
    reports = {
        pair_id: evaluate_pair(
            gate,
            pair,
            as_of=evidence.as_of,
            triggered_trade_counts=evidence.triggered_trade_counts,
            repetitions_override=repetitions_override,
            evidence_source=evidence.evidence_source,
            seed=37 + index,
        )
        for index, (pair_id, pair) in enumerate(sorted(evidence.pairs.items()))
    }
    primary = [
        report
        for pair_id, report in reports.items()
        if gate.pair_rules[pair_id].confirmatory
    ]
    if any(report["verdict"] == PairVerdict.INCONCLUSIVE for report in primary):
        project_disposition = "CONTINUE_COLLECTION"
    elif any(report["verdict"] == PairVerdict.PROMOTE for report in primary):
        project_disposition = "PROMOTION_CANDIDATE"
    else:
        project_disposition = "REVIEW_QUANT_ONLY_OR_NO_DEPLOYMENT"
    return {
        "gate_version": gate.gate_version,
        "gate_config_hash": gate.config_hash,
        "phase2_epoch_id": gate.phase2_epoch_id,
        "as_of": evidence.as_of.astimezone(UTC).isoformat(),
        "evidence_source": evidence.evidence_source,
        "earliest_formal_checkpoint": gate.earliest_formal_checkpoint.isoformat(),
        "pair_reports": reports,
        "project_disposition": project_disposition,
        "next_checkpoint": (
            evidence.as_of.astimezone(UTC)
            + timedelta(days=gate.next_checkpoint_delay_days)
        ).isoformat()
        if project_disposition == "CONTINUE_COLLECTION"
        else None,
        "exploratory_pair_determines_project_outcome": False,
        "infrastructure_readiness_used_as_decision_input": False,
    }
