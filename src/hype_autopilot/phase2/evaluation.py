from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import ceil

import numpy as np
from arch.bootstrap import MovingBlockBootstrap, StationaryBootstrap

from hype_autopilot.simulation.models import TradeStatus
from hype_autopilot.strategies.base import Decision


class EligibilityStatus(StrEnum):
    CO_ELIGIBLE = "CO_ELIGIBLE"
    NOT_CO_ELIGIBLE = "NOT_CO_ELIGIBLE"


class OutcomeStatus(StrEnum):
    COMPLETE = "COMPLETE"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    EXCLUDED = "EXCLUDED"


@dataclass(frozen=True)
class StrategyOutcome:
    snapshot_hash: str
    strategy_id: str
    decision: Decision
    was_flat: bool
    trade_status: TradeStatus | None = None
    net_return: float | None = None
    direction: str = "FLAT"
    regime: str = "UNKNOWN"
    fees: float = 0.0
    funding: float = 0.0
    slippage: float = 0.0
    api_cost: float = 0.0

    @property
    def open(self) -> bool:
        return self.trade_status in {TradeStatus.PENDING_ENTRY, TradeStatus.OPEN}

    @property
    def executed(self) -> bool:
        return self.trade_status == TradeStatus.CLOSED

    def attributed_return(self) -> float:
        if self.decision == Decision.NO_TRADE:
            return 0.0
        if self.executed and self.net_return is not None:
            return float(self.net_return)
        if self.trade_status in {
            TradeStatus.SUPPRESSED_POSITION_OPEN,
            TradeStatus.SUPPRESSED_INVALID_TARGET_AFTER_LATENCY,
            TradeStatus.SUPPRESSED_NO_ENTRY_DATA,
        }:
            return 0.0
        raise ValueError("non-flat completed decision lacks a closed realized return")


@dataclass(frozen=True)
class PairedOutcome:
    pair_id: str
    snapshot_hash: str
    designation: str
    eligibility_status: EligibilityStatus
    outcome_status: OutcomeStatus
    treatment_return: float | None
    control_return: float | None
    treatment_executed: bool
    control_executed: bool
    treatment_direction: str
    control_direction: str
    regime: str
    treatment_fees: float
    control_fees: float
    treatment_funding: float
    control_funding: float
    treatment_slippage: float
    control_slippage: float
    treatment_api_cost: float
    control_api_cost: float

    @property
    def difference(self) -> float | None:
        if self.treatment_return is None or self.control_return is None:
            return None
        return self.treatment_return - self.control_return


def build_paired_outcome(
    *,
    pair_id: str,
    designation: str,
    treatment: StrategyOutcome,
    control: StrategyOutcome,
) -> PairedOutcome:
    if treatment.snapshot_hash != control.snapshot_hash:
        raise ValueError("paired outcomes require identical snapshot lineage")
    eligible = treatment.was_flat and control.was_flat
    if not eligible:
        status = OutcomeStatus.EXCLUDED
        treatment_return = control_return = None
    elif treatment.open or control.open:
        status = OutcomeStatus.RIGHT_CENSORED
        treatment_return = control_return = None
    else:
        status = OutcomeStatus.COMPLETE
        treatment_return = treatment.attributed_return()
        control_return = control.attributed_return()
    return PairedOutcome(
        pair_id=pair_id,
        snapshot_hash=treatment.snapshot_hash,
        designation=designation,
        eligibility_status=EligibilityStatus.CO_ELIGIBLE
        if eligible
        else EligibilityStatus.NOT_CO_ELIGIBLE,
        outcome_status=status,
        treatment_return=treatment_return,
        control_return=control_return,
        treatment_executed=treatment.executed,
        control_executed=control.executed,
        treatment_direction=treatment.direction,
        control_direction=control.direction,
        regime=treatment.regime,
        treatment_fees=treatment.fees,
        control_fees=control.fees,
        treatment_funding=treatment.funding,
        control_funding=control.funding,
        treatment_slippage=treatment.slippage,
        control_slippage=control.slippage,
        treatment_api_cost=treatment.api_cost,
        control_api_cost=control.api_cost,
    )


def _effective_sample_size(values: np.ndarray) -> float:
    n = len(values)
    if n < 3 or np.var(values) == 0:
        return float(n)
    centered = values - np.mean(values)
    denominator = float(np.dot(centered, centered))
    correlations: list[float] = []
    for lag in range(1, min(n - 1, ceil(n ** (1 / 3)) * 4) + 1):
        rho = float(np.dot(centered[:-lag], centered[lag:]) / denominator)
        if rho <= 0:
            break
        correlations.append(rho)
    return max(1.0, min(float(n), n / (1 + 2 * sum(correlations))))


def _max_drawdown(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    curve = np.cumsum(values)
    peak = np.maximum.accumulate(np.concatenate(([0.0], curve)))[:-1]
    return float(np.min(curve - peak))


def _bootstrap_ci(
    values: np.ndarray, *, method: str, repetitions: int, block_size: int, seed: int
) -> tuple[float, float]:
    if len(values) == 0:
        return (float("nan"), float("nan"))
    if len(values) == 1:
        return (float(values[0]), float(values[0]))
    size = max(1, min(block_size, len(values)))
    rng = np.random.default_rng(seed)
    bootstrap = (
        StationaryBootstrap(size, values, seed=rng)
        if method == "stationary"
        else MovingBlockBootstrap(size, values, seed=rng)
    )
    samples: list[float] = []
    for positional, _ in bootstrap.bootstrap(repetitions):
        samples.append(float(np.mean(positional[0])))
    low, high = np.quantile(np.asarray(samples), [0.025, 0.975])
    return float(low), float(high)


def _slice_means(rows: Sequence[PairedOutcome], field: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        difference = row.difference
        if difference is None:
            continue
        key = str(getattr(row, field))
        grouped.setdefault(key, []).append(difference)
    return {key: float(np.mean(values)) for key, values in sorted(grouped.items())}


def evaluate_pair(
    outcomes: Iterable[PairedOutcome],
    *,
    repetitions: int = 2_000,
    block_size: int = 8,
    seed: int = 17,
) -> dict[str, object]:
    rows = list(outcomes)
    pair_ids = {row.pair_id for row in rows}
    designations = {row.designation for row in rows}
    if len(pair_ids) > 1 or len(designations) > 1:
        raise ValueError(
            "each pair and confirmatory/exploratory designation must be evaluated separately"
        )
    complete = [row for row in rows if row.outcome_status == OutcomeStatus.COMPLETE]
    diffs = np.asarray([row.difference for row in complete], dtype=float)
    treatment = np.asarray([row.treatment_return for row in complete], dtype=float)
    control = np.asarray([row.control_return for row in complete], dtype=float)
    halfway = len(diffs) // 2
    first_half = float(np.mean(diffs[:halfway])) if halfway else float("nan")
    second_half = (
        float(np.mean(diffs[halfway:])) if len(diffs) - halfway else float("nan")
    )
    best_index = int(np.argmax(diffs)) if len(diffs) else None
    without_best = np.delete(diffs, best_index) if best_index is not None else diffs
    disagreements = sum(
        row.treatment_direction != row.control_direction for row in complete
    )
    return {
        "pair_id": next(iter(pair_ids), None),
        "designation": next(iter(designations), None),
        "n_boundaries": len(rows),
        "n_coeligible": sum(
            row.eligibility_status == EligibilityStatus.CO_ELIGIBLE for row in rows
        ),
        "n_complete": len(complete),
        "n_right_censored": sum(
            row.outcome_status == OutcomeStatus.RIGHT_CENSORED for row in rows
        ),
        "n_excluded": sum(row.outcome_status == OutcomeStatus.EXCLUDED for row in rows),
        "treatment_executed_trades": sum(row.treatment_executed for row in complete),
        "control_executed_trades": sum(row.control_executed for row in complete),
        "treatment_expectancy": float(np.mean(treatment))
        if len(treatment)
        else float("nan"),
        "control_expectancy": float(np.mean(control)) if len(control) else float("nan"),
        "paired_mean_difference": float(np.mean(diffs)) if len(diffs) else float("nan"),
        "stationary_bootstrap_95_ci": _bootstrap_ci(
            diffs,
            method="stationary",
            repetitions=repetitions,
            block_size=block_size,
            seed=seed,
        ),
        "moving_block_bootstrap_95_ci": _bootstrap_ci(
            diffs,
            method="moving",
            repetitions=repetitions,
            block_size=block_size,
            seed=seed + 1,
        ),
        "effective_sample_size": _effective_sample_size(diffs),
        "paired_max_drawdown": _max_drawdown(diffs),
        "tail_p05": float(np.quantile(diffs, 0.05)) if len(diffs) else float("nan"),
        "tail_p95": float(np.quantile(diffs, 0.95)) if len(diffs) else float("nan"),
        "first_half_mean": first_half,
        "second_half_mean": second_half,
        "half_consistent": bool(
            np.isfinite(first_half)
            and np.isfinite(second_half)
            and np.sign(first_half) == np.sign(second_half)
        ),
        "remove_best_mean": float(np.mean(without_best))
        if len(without_best)
        else float("nan"),
        "best_observation_share_abs": (
            float(abs(diffs[best_index]) / np.sum(np.abs(diffs)))
            if best_index is not None and np.sum(np.abs(diffs)) > 0
            else 0.0
        ),
        "direction_slice_mean_difference": _slice_means(
            complete, "treatment_direction"
        ),
        "regime_slice_mean_difference": _slice_means(complete, "regime"),
        "direction_disagreements": disagreements,
        "direction_disagreement_rate": disagreements / len(complete)
        if complete
        else float("nan"),
        "treatment_costs": {
            "fees": sum(row.treatment_fees for row in complete),
            "funding": sum(row.treatment_funding for row in complete),
            "slippage": sum(row.treatment_slippage for row in complete),
            "api": sum(row.treatment_api_cost for row in complete),
        },
        "control_costs": {
            "fees": sum(row.control_fees for row in complete),
            "funding": sum(row.control_funding for row in complete),
            "slippage": sum(row.control_slippage for row in complete),
            "api": sum(row.control_api_cost for row in complete),
        },
        "bootstrap_library": "arch",
    }


def evaluate_registered_pairs(
    outcomes: Iterable[PairedOutcome],
    *,
    repetitions: int = 2_000,
    block_size: int = 8,
    seed: int = 17,
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[PairedOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.pair_id, []).append(outcome)
    return {
        pair_id: evaluate_pair(
            rows, repetitions=repetitions, block_size=block_size, seed=seed + index
        )
        for index, (pair_id, rows) in enumerate(sorted(grouped.items()))
    }
