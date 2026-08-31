from hype_autopilot.features.models import FeatureSet
from hype_autopilot.regimes.classifier import classify_regime


def test_regime_refuses_volatility_before_minimum_history():
    features = FeatureSet(last_15m_close=100, ema20_1h=110, ema50_1h=100,
                          ema20_1h_lag6=105, atr_pct_1h=0.02)
    regime = classify_regime(features, [0.01] * 719)
    assert regime.trend == "UP"
    assert regime.volatility == "UNKNOWN"


def test_regime_uses_last_1440_values():
    features = FeatureSet(last_15m_close=100, ema20_1h=90, ema50_1h=100,
                          ema20_1h_lag6=95, atr_pct_1h=0.03)
    regime = classify_regime(features, [99.0] * 100 + [0.01] * 1000 + [0.02] * 440)
    assert regime.trend == "DOWN"
    assert regime.volatility == "HIGH"

