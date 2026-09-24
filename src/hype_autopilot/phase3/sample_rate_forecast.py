"""Offline, non-performance planning forecast for frozen epoch006 sample supply."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hype_autopilot.phase3.gate import lag_corrected_ess


PLANNING_LABEL = "PLANNING_ONLY — NOT PERFORMANCE EVIDENCE"
FORECAST_VERSION = "EPOCH006_SAMPLE_RATE_FORECAST_V1"
WINDOW_DAYS = 42
BOUNDARIES_PER_DAY = 96
WINDOW_BOUNDARIES = WINDOW_DAYS * BOUNDARIES_PER_DAY
PAIR_MINIMUMS = {
    "LLM_V1__vs__QUANT_TREND_V1": 60,
    "LLM_V1__vs__QUANT_MR_V1": 60,
    "HYBRID_TREND_LLM_V1__vs__QUANT_TREND_V1": 60,
    "HYBRID_MR_LLM_V1__vs__QUANT_MR_V1": 30,
}
TRIGGER_MINIMUMS = {
    "QUANT_TREND_V1": 40,
    "QUANT_MR_V1": 40,
    "LLM_V1": 40,
    "HYBRID_TREND_LLM_V1": 20,
    "HYBRID_MR_LLM_V1": 20,
}
_REQUIRED_LOGIC = {
    "quant_trend_version": "QUANT_TREND_V1",
    "quant_mr_version": "QUANT_MR_V1",
    "detector_version": "SETUP_DETECTOR_V1",
    "simulator_version": "SIMULATOR_V1",
}
_FORBIDDEN_FIELD_TERMS = ("pnl", "return", "profit", "win_rate", "expectancy")


def _parse_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("historical boundary timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "p10": _percentile(values, 0.10),
        "p25": _percentile(values, 0.25),
        "median": _percentile(values, 0.50),
        "p75": _percentile(values, 0.75),
        "p90": _percentile(values, 0.90),
    }


def _assert_no_performance_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(term in str(key).lower() for term in _FORBIDDEN_FIELD_TERMS):
                raise ValueError("planning fixture must not contain performance fields")
            _assert_no_performance_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_performance_fields(item)


def _validate_fixture(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("forecast_version") != FORECAST_VERSION:
        raise ValueError("unexpected sample-rate forecast fixture version")
    if payload.get("phase2_epoch_id") == "phase2_epoch_006":
        raise ValueError("epoch006 prospective evidence is not a forecast input")
    if payload.get("frozen_logic") != _REQUIRED_LOGIC:
        raise ValueError("historical input must declare the frozen epoch006 logic")
    _assert_no_performance_fields(payload)
    boundaries = list(payload.get("boundaries", []))
    if not boundaries:
        raise ValueError("historical boundary-state input is required")
    required = {
        "timestamp",
        "scoreable",
        "data_gap",
        "regime",
        "quant_trend_decision",
        "quant_mr_decision",
        "detector_trigger",
        "trend_position_open",
        "mr_position_open",
        "trend_suppressed",
        "mr_suppressed",
    }
    for row in boundaries:
        if not required <= set(row):
            raise ValueError("historical boundary-state row is incomplete")
        _parse_timestamp(row["timestamp"])
    return sorted(boundaries, key=lambda row: _parse_timestamp(row["timestamp"]))


def load_historical_boundary_state(path: str | Path) -> dict[str, Any]:
    """Load a frozen replay export containing state only, never outcomes."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("historical boundary-state export must be an object")
    _validate_fixture(payload)
    return payload


def _trigger_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    directional = [row for row in rows if row[key] != "NO_TRADE"]
    regimes = Counter(str(row["regime"]) for row in directional)
    timestamps = [_parse_timestamp(row["timestamp"]) for row in directional]
    intervals = [
        (later - earlier).total_seconds() / 60.0
        for earlier, later in zip(timestamps, timestamps[1:], strict=False)
    ]
    return {
        "directional_trigger_count": len(directional),
        "directional_trigger_frequency": len(directional) / len(rows) if rows else 0.0,
        "no_trade_count": len(rows) - len(directional),
        "no_trade_frequency": (len(rows) - len(directional)) / len(rows) if rows else 0.0,
        "average_minutes_between_triggers": sum(intervals) / len(intervals) if intervals else None,
        "trigger_regime_distribution": dict(sorted(regimes.items())),
    }


def _suppression_stats(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    flags = [bool(row[f"{prefix}_suppressed"]) for row in rows]
    streaks: list[int] = []
    current = 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            streaks.append(current)
            current = 0
    if current:
        streaks.append(current)
    lock_seconds = [
        float(row[f"{prefix}_lock_seconds"])
        for row in rows
        if row.get(f"{prefix}_lock_seconds") is not None
    ]
    return {
        "suppressed_boundary_fraction": sum(flags) / len(flags) if flags else 0.0,
        "suppression_streak_boundaries": _distribution([float(value) for value in streaks]),
        "position_lock_seconds": {
            "mean": sum(lock_seconds) / len(lock_seconds) if lock_seconds else None,
            "median": _percentile(lock_seconds, 0.5),
        },
    }


def _window_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scoreable = [row for row in rows if bool(row["scoreable"])]
    trend = _trigger_stats(scoreable, "quant_trend_decision")
    mr = _trigger_stats(scoreable, "quant_mr_decision")
    detector_triggers = sum(row["detector_trigger"] != "NO_TRIGGER" for row in scoreable)
    availability = [1.0 if bool(row["scoreable"]) else 0.0 for row in rows]
    ess, cutoff = lag_corrected_ess(availability)
    regimes = Counter(str(row["regime"]) for row in scoreable)
    trend_states = {str(row["regime"]).split("_", 1)[0] for row in scoreable}
    volatility_states = {
        str(row["regime"]).split("_", 1)[1]
        for row in scoreable
        if "_" in str(row["regime"])
    }
    return {
        "expected_boundaries": WINDOW_BOUNDARIES,
        "observed_boundaries": len(rows),
        "scoreable_snapshots": len(scoreable),
        "rejected_or_unavailable_boundaries": len(rows) - len(scoreable),
        "data_gap_boundaries": sum(bool(row["data_gap"]) for row in rows),
        "quant_trend": trend,
        "quant_mr": mr,
        "detector_trigger_count": detector_triggers,
        "trend_suppression": _suppression_stats(scoreable, "trend"),
        "mr_suppression": _suppression_stats(scoreable, "mr"),
        "availability_ess": ess,
        "availability_ess_acf_cutoff_lag": cutoff,
        "regime_coverage": {
            "distinct_trend_states": len(trend_states),
            "distinct_volatility_states": len(volatility_states),
            "qualifying_buckets_at_least_5": sum(value >= 5 for value in regimes.values()),
        },
    }


def _planning_classification(distribution: dict[str, float | None], minimum: int) -> str:
    if distribution["p10"] is not None and distribution["p10"] >= minimum:
        return "COMFORTABLY_LIKELY"
    if distribution["median"] is not None and distribution["median"] >= minimum:
        return "BORDERLINE"
    return "STRUCTURALLY_UNLIKELY"


def forecast_historical_sample_rate(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a rolling 42-day supply forecast without accessing outcomes."""
    rows = _validate_fixture(payload)
    starts = [
        index
        for index in range(0, len(rows))
        if _parse_timestamp(rows[-1]["timestamp"])
        >= _parse_timestamp(rows[index]["timestamp"]) + timedelta(days=WINDOW_DAYS)
    ]
    windows = [
        [
            row
            for row in rows
            if _parse_timestamp(rows[start]["timestamp"])
            <= _parse_timestamp(row["timestamp"])
            < _parse_timestamp(rows[start]["timestamp"]) + timedelta(days=WINDOW_DAYS)
        ]
        for start in starts[::BOUNDARIES_PER_DAY]
    ]
    if not windows:
        raise ValueError("at least one complete historical 42-day window is required")
    summaries = [_window_summary(window) for window in windows]
    metric = lambda path: [float(path(summary)) for summary in summaries]
    trend_triggers = metric(lambda item: item["quant_trend"]["directional_trigger_count"])
    mr_triggers = metric(lambda item: item["quant_mr"]["directional_trigger_count"])
    scoreable = metric(lambda item: item["scoreable_snapshots"])
    ess = metric(lambda item: item["availability_ess"])
    coverage = {
        "at_least_two_trend_states_fraction": sum(
            item["regime_coverage"]["distinct_trend_states"] >= 2 for item in summaries
        )
        / len(summaries),
        "at_least_two_volatility_states_fraction": sum(
            item["regime_coverage"]["distinct_volatility_states"] >= 2 for item in summaries
        )
        / len(summaries),
        "qualifying_regime_bucket_count": _distribution(
            metric(lambda item: item["regime_coverage"]["qualifying_buckets_at_least_5"])
        ),
    }
    coeligibility_ranges = {
        pair: {
            "historical_quant_state_upper_bound": _distribution(scoreable),
            "lower_bound_without_historical_llm": 0,
            "assumption": "LLM_AND_HYBRID_POSITION_STATE_UNOBSERVED_NO_FABRICATION",
            "planning_minimum": minimum,
        }
        for pair, minimum in PAIR_MINIMUMS.items()
    }
    return {
        "label": PLANNING_LABEL,
        "forecast_version": FORECAST_VERSION,
        "source": payload.get("source", "UNSPECIFIED_HISTORICAL_REPLAY"),
        "historical_start": rows[0]["timestamp"],
        "historical_end": rows[-1]["timestamp"],
        "complete_rolling_42day_windows": len(summaries),
        "rolling_distributions": {
            "scoreable_snapshots": _distribution(scoreable),
            "quant_trend_directional_triggers": _distribution(trend_triggers),
            "quant_mr_directional_triggers": _distribution(mr_triggers),
            "availability_ess": _distribution(ess),
        },
        "triggered_trade_planning": {
            "QUANT_TREND_V1": {
                "minimum": TRIGGER_MINIMUMS["QUANT_TREND_V1"],
                "distribution": _distribution(trend_triggers),
                "classification": _planning_classification(
                    _distribution(trend_triggers), TRIGGER_MINIMUMS["QUANT_TREND_V1"]
                ),
            },
            "QUANT_MR_V1": {
                "minimum": TRIGGER_MINIMUMS["QUANT_MR_V1"],
                "distribution": _distribution(mr_triggers),
                "classification": _planning_classification(
                    _distribution(mr_triggers), TRIGGER_MINIMUMS["QUANT_MR_V1"]
                ),
            },
            "LLM_V1": {"classification": "UNOBSERVED_NO_HISTORICAL_LLM"},
            "HYBRID_TREND_LLM_V1": {"classification": "UNOBSERVED_NO_HISTORICAL_LLM"},
            "HYBRID_MR_LLM_V1": {"classification": "UNOBSERVED_NO_HISTORICAL_LLM"},
        },
        "coeligibility_scenario_ranges": coeligibility_ranges,
        "regime_coverage": coverage,
        "window_summaries": summaries,
        "limitations": [
            "NO_HISTORICAL_LLM_DECISIONS_OR_LLM_POSITION_STATE",
            "COELIGIBILITY_IS_REPORTED_AS_STRUCTURAL_BOUNDS_NOT_FABRICATED_ESTIMATES",
            "AVAILABILITY_ESS_APPLIES_FROZEN_LAG_CORRECTED_ESS_TO_BOUNDARY_AVAILABILITY",
            "NO_PERFORMANCE_OR_OUTCOME_FIELDS_ACCEPTED",
        ],
    }
