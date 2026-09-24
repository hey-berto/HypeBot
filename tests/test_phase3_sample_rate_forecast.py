from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hype_autopilot.phase3.sample_rate_forecast import (
    FORECAST_VERSION,
    PLANNING_LABEL,
    forecast_historical_sample_rate,
)


def _fixture(days: int = 85) -> dict[str, object]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    boundaries = []
    for index in range(days * 96):
        at = start + timedelta(minutes=15 * index)
        scoreable = index % 97 != 0
        regime = ("UP_HIGH", "DOWN_LOW", "UP_NORMAL", "DOWN_HIGH")[index % 4]
        boundaries.append(
            {
                "timestamp": at.isoformat(),
                "scoreable": scoreable,
                "data_gap": index % 251 == 0,
                "regime": regime,
                "quant_trend_decision": "LONG" if index % 120 == 0 else "NO_TRADE",
                "quant_mr_decision": "SHORT" if index % 160 == 0 else "NO_TRADE",
                "detector_trigger": "TRIGGER_LONG" if index % 120 == 0 else "NO_TRIGGER",
                "trend_position_open": index % 40 in range(1, 5),
                "mr_position_open": index % 60 in range(1, 3),
                "trend_suppressed": index % 40 in range(1, 5),
                "mr_suppressed": index % 60 in range(1, 3),
                "trend_lock_seconds": 3600 if index % 40 == 1 else None,
                "mr_lock_seconds": 1800 if index % 60 == 1 else None,
            }
        )
    return {
        "forecast_version": FORECAST_VERSION,
        "phase2_epoch_id": "historical_replay_2025q1",
        "source": "SYNTHETIC_FROZEN_BOUNDARY_STATE",
        "frozen_logic": {
            "quant_trend_version": "QUANT_TREND_V1",
            "quant_mr_version": "QUANT_MR_V1",
            "detector_version": "SETUP_DETECTOR_V1",
            "simulator_version": "SIMULATOR_V1",
        },
        "boundaries": boundaries,
    }


def test_forecast_is_deterministic_and_has_no_llm_fabrication():
    payload = _fixture()
    first = forecast_historical_sample_rate(payload)
    second = forecast_historical_sample_rate(payload)

    assert first == second
    assert first["label"] == PLANNING_LABEL
    assert first["complete_rolling_42day_windows"] > 1
    assert first["rolling_distributions"]["scoreable_snapshots"]["median"] is not None
    assert first["triggered_trade_planning"]["QUANT_TREND_V1"]["minimum"] == 40
    assert first["triggered_trade_planning"]["LLM_V1"] == {
        "classification": "UNOBSERVED_NO_HISTORICAL_LLM"
    }
    pair = first["coeligibility_scenario_ranges"]["LLM_V1__vs__QUANT_TREND_V1"]
    assert pair["lower_bound_without_historical_llm"] == 0
    assert pair["planning_minimum"] == 60
    assert first["window_summaries"][0]["expected_boundaries"] == 4032


def test_forecast_rejects_epoch006_and_performance_inputs():
    epoch006 = _fixture()
    epoch006["phase2_epoch_id"] = "phase2_epoch_006"
    with pytest.raises(ValueError, match="epoch006 prospective"):
        forecast_historical_sample_rate(epoch006)

    with_performance = _fixture()
    with_performance["boundaries"][0]["net_pnl"] = 1.0
    with pytest.raises(ValueError, match="performance fields"):
        forecast_historical_sample_rate(with_performance)
