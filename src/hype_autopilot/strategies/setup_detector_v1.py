from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from hype_autopilot.snapshots.models import DecisionSnapshot


class DetectorTrigger(StrEnum):
    TRIGGER_LONG = "TRIGGER_LONG"
    TRIGGER_SHORT = "TRIGGER_SHORT"
    TRIGGER_CONTEXT = "TRIGGER_CONTEXT"
    NO_TRIGGER = "NO_TRIGGER"


class DetectorDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot_hash: str
    detector_version: str = "SETUP_DETECTOR_V1"
    trigger: DetectorTrigger
    reason_codes: tuple[str, ...]


class SetupDetectorV1:
    def evaluate(self, snapshot: DecisionSnapshot) -> DetectorDecision:
        if not snapshot.snapshot_hash:
            raise ValueError("detector requires a frozen snapshot")
        f = snapshot.market.hype_features
        trigger, reasons = DetectorTrigger.NO_TRIGGER, ["NO_ANOMALY"]
        if f.donchian_high_1h is not None and f.last_15m_close > f.donchian_high_1h:
            trigger, reasons = DetectorTrigger.TRIGGER_LONG, ["BREAKOUT_CANDIDATE"]
        elif f.donchian_low_1h is not None and f.last_15m_close < f.donchian_low_1h:
            trigger, reasons = DetectorTrigger.TRIGGER_SHORT, ["BREAKOUT_CANDIDATE"]
        elif f.funding_zscore is not None and abs(f.funding_zscore) >= 2.0:
            trigger, reasons = DetectorTrigger.TRIGGER_CONTEXT, ["FUNDING_CROWDING"]
        elif f.return_1h is not None and abs(f.return_1h) >= 0.03:
            trigger, reasons = DetectorTrigger.TRIGGER_CONTEXT, ["LARGE_1H_MOVE"]
        return DetectorDecision(snapshot_hash=snapshot.snapshot_hash, trigger=trigger, reason_codes=tuple(reasons))

