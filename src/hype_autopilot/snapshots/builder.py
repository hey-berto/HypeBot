from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.data.freshness import assess_freshness
from hype_autopilot.data.models import ObservationClass
from hype_autopilot.data.repository import Repository
from hype_autopilot.features.engine import atr_percent_history, build_features
from hype_autopilot.regimes.classifier import classify_regime
from hype_autopilot.snapshots.canonicalize import freeze_snapshot
from hype_autopilot.snapshots.models import DecisionSnapshot, MarketSnapshot


class SnapshotBuilder:
    def __init__(self, repository: Repository, base_config: dict[str, Any], epoch_config: dict[str, Any]) -> None:
        self.repository = repository
        self.base_config = base_config
        self.epoch_config = epoch_config

    def build(self, snapshot_at: datetime, *, available_at: datetime | None = None,
              observation_class: ObservationClass = ObservationClass.SOAK) -> DecisionSnapshot:
        snapshot_at = snapshot_at.astimezone(UTC).replace(second=0, microsecond=0)
        available_at = available_at or datetime.now(UTC)
        if snapshot_at.minute % 15:
            raise ValueError("snapshot timestamp must be a UTC quarter-hour boundary")
        symbols = self.base_config["symbols"]
        hype, btc = symbols["hype"], symbols["btc"]
        hype_bars = {interval: self.repository.candles(hype, interval, snapshot_at, available_at=available_at)
                     for interval in ("1m", "5m", "15m", "1h", "4h")}
        btc_bars = {interval: self.repository.candles(btc, interval, snapshot_at, available_at=available_at)
                    for interval in ("15m", "1h", "4h")}
        funding = self.repository.funding(hype, snapshot_at, available_at=available_at)
        contexts = self.repository.asset_contexts(hype, snapshot_at, available_at=available_at)
        context = contexts[-1] if contexts else None
        bbo = self.repository.latest_bbo(hype, snapshot_at, available_at=available_at)

        if not hype_bars["15m"]:
            raise ValueError("cannot build even a rejected snapshot without a completed HYPE 15m candle")
        hype_features = build_features(
            snapshot_at, hype_bars["15m"], hype_bars["1h"],
            [(item.source_timestamp, item.funding_rate) for item in funding],
            context=context, bbo=bbo, context_history=contexts,
        )
        btc_features = None
        if btc_bars["15m"]:
            btc_features = build_features(snapshot_at, btc_bars["15m"], btc_bars["1h"], [])
        hype_atr_history = atr_percent_history(hype_bars["1h"], snapshot_at)
        regime = classify_regime(hype_features, hype_atr_history)
        btc_regime = classify_regime(btc_features, atr_percent_history(btc_bars["1h"], snapshot_at)) if btc_features else None

        source_cutoffs: dict[str, datetime] = {}
        for prefix, groups in (("hype", hype_bars), ("btc", btc_bars)):
            for interval, bars in groups.items():
                if bars:
                    source_cutoffs[f"{prefix}_{interval}"] = bars[-1].close_time
        if context:
            source_cutoffs["hype_context"] = context.source_timestamp
        if funding:
            source_cutoffs["hype_funding"] = funding[-1].source_timestamp
        if bbo:
            source_cutoffs["hype_bbo"] = bbo.source_timestamp

        rejections: list[str] = []
        if len(hype_bars["1h"]) < 720:
            rejections.append("INSUFFICIENT_HYPE_1H_HISTORY_720")
        if len(hype_bars["15m"]) < 96:
            rejections.append("INSUFFICIENT_HYPE_15M_HISTORY_96")
        if len(funding) < 72:
            rejections.append("INSUFFICIENT_FUNDING_HISTORY_72")
        if len(btc_bars["1h"]) < 200 or len(btc_bars["15m"]) < 96:
            rejections.append("INSUFFICIENT_BTC_HISTORY")
        if regime.volatility.value == "UNKNOWN" or regime.trend.value == "UNKNOWN":
            rejections.append("INVALID_HYPE_REGIME")
        quality = assess_freshness(
            snapshot_at, source_cutoffs, self.base_config["freshness_seconds"],
            set(self.base_config["required_sources"]), set(self.base_config["optional_sources"]),
            tuple(rejections),
        )
        market = MarketSnapshot(
            hype_context=context, hype_features=hype_features,
            btc_features=({**btc_features.model_dump(mode="python"),
                           "regime": btc_regime.combined if btc_regime else None} if btc_features else {}),
            microstructure={"spread_bps": hype_features.spread_bps,
                            "bbo_imbalance": hype_features.bbo_imbalance},
        )
        logical_id = str(uuid5(NAMESPACE_URL, f"{self.epoch_config['epoch_id']}:{observation_class.value}:{snapshot_at.isoformat()}"))
        snapshot = DecisionSnapshot(
            snapshot_id=logical_id, snapshot_timestamp=snapshot_at, created_at=snapshot_at,
            snapshot_schema_version=self.epoch_config["snapshot_schema_version"],
            feature_schema_version=self.epoch_config["feature_schema_version"],
            epoch_id=self.epoch_config["epoch_id"], observation_class=observation_class,
            market=market, regime=regime, data_quality=quality, source_cutoffs=source_cutoffs,
        )
        self.repository.save_feature_observation(snapshot_at, hype, snapshot.feature_schema_version, hype_features)
        if btc_features:
            self.repository.save_feature_observation(snapshot_at, btc, snapshot.feature_schema_version, btc_features)
        return freeze_snapshot(snapshot)
