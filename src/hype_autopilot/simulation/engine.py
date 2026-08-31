from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Sequence
from uuid import NAMESPACE_URL, uuid5

from hype_autopilot.data.models import Candle
from hype_autopilot.data.repository import Repository
from hype_autopilot.features.indicators import atr
from hype_autopilot.simulation.models import ExitReason, PaperTrade, TradeStatus
from hype_autopilot.strategies.base import Decision, StrategyDecision


class PaperSimulator:
    def __init__(self, repository: Repository, *, latency_seconds: int = 3,
                 fee_bps_per_side: float = 4.5, slippage_bps_per_side: float = 2.0) -> None:
        self.repository = repository
        self.latency_seconds = latency_seconds
        self.fee_bps = fee_bps_per_side
        self.slippage_bps = slippage_bps_per_side

    def submit(self, decision: StrategyDecision) -> PaperTrade | None:
        if decision.decision == Decision.NO_TRADE:
            return None
        existing = self.repository.trade_for_decision(decision.decision_id)
        if existing:
            return existing
        trade_id = str(uuid5(NAMESPACE_URL, f"paper:{decision.decision_id}"))
        active = self.repository.active_trade(decision.strategy_id)
        status = TradeStatus.SUPPRESSED_POSITION_OPEN if active else TradeStatus.PENDING_ENTRY
        trade = PaperTrade(
            paper_trade_id=trade_id, strategy_decision_id=decision.decision_id,
            strategy_id=decision.strategy_id, snapshot_hash=decision.snapshot_hash,
            direction=decision.decision.value, signal_time=decision.created_at,
            stop_price=decision.stop_reference, target_price=decision.target_reference,
            current_stop_price=decision.stop_reference, status=status,
            last_processed_at=decision.created_at,
            flags=("SUPPRESSED_POSITION_OPEN",) if active else (),
        )
        trade = self.repository.save_trade(trade)
        self.repository.save_order(
            str(uuid5(NAMESPACE_URL, f"order:{decision.decision_id}")), trade,
            decision.created_at + timedelta(seconds=self.latency_seconds),
        )
        return trade

    def process_until(self, cutoff: datetime) -> list[PaperTrade]:
        results: list[PaperTrade] = []
        for trade in self.repository.active_trades():
            decision = self.repository.load_strategy_decision(trade.strategy_decision_id)
            if trade.status == TradeStatus.PENDING_ENTRY:
                trade = self._enter(trade, decision, cutoff)
            if trade.status == TradeStatus.OPEN:
                trade = self._manage(trade, decision, cutoff)
            trade = self.repository.save_trade(trade)
            self.repository.update_order(trade)
            results.append(trade)
        return results

    def _enter(self, trade: PaperTrade, decision: StrategyDecision, cutoff: datetime) -> PaperTrade:
        eligible = trade.signal_time + timedelta(seconds=self.latency_seconds)
        bars = self.repository.candles("HYPE", "1m", cutoff, start=trade.signal_time)
        candidates = [bar for bar in bars if bar.close_time >= eligible]
        if not candidates:
            return trade.model_copy(update={"last_processed_at": cutoff})
        bar = candidates[0]
        sign = 1 if trade.direction == Decision.LONG.value else -1
        raw_entry = bar.close
        entry = raw_entry * (1 + sign * self.slippage_bps / 10_000)
        slippage = abs(entry - raw_entry)
        target = trade.target_price
        if target is not None and ((sign == 1 and target <= entry) or (sign == -1 and target >= entry)):
            return trade.model_copy(update={
                "status": TradeStatus.SUPPRESSED_INVALID_TARGET_AFTER_LATENCY,
                "exit_reason": ExitReason.NO_ENTRY, "last_processed_at": bar.close_time,
                "flags": tuple(sorted(set((*trade.flags, "ENTRY_FALLBACK_1M",
                                           "SUPPRESSED_INVALID_TARGET_AFTER_LATENCY")))),
            })
        atr_value = decision.metadata.get("atr14_1h")
        multiple = decision.metadata.get("stop_atr_multiple")
        stop = entry - sign * float(atr_value) * float(multiple) if atr_value and multiple else trade.stop_price
        fee = entry * self.fee_bps / 10_000
        opened = trade.model_copy(update={
            "entry_time": bar.close_time, "entry_price": entry, "stop_price": stop,
            "current_stop_price": stop, "highest_price": raw_entry, "lowest_price": raw_entry,
            "fees": fee, "slippage_cost": slippage, "status": TradeStatus.OPEN,
            "last_processed_at": bar.close_time,
            "flags": tuple(sorted(set((*trade.flags, "ENTRY_FALLBACK_1M")))),
        })
        self.repository.save_fill(trade.paper_trade_id, bar.close_time, "ENTRY", entry, fee, slippage,
                                  {"raw_price": raw_entry, "latency_seconds": self.latency_seconds})
        return opened

    def _manage(self, trade: PaperTrade, decision: StrategyDecision, cutoff: datetime) -> PaperTrade:
        assert trade.entry_time and trade.entry_price
        bars = [bar for bar in self.repository.candles(
            "HYPE", "1m", cutoff, start=trade.last_processed_at
        ) if bar.close_time > trade.last_processed_at]
        sign = 1 if trade.direction == Decision.LONG.value else -1
        highest, lowest = trade.highest_price or trade.entry_price, trade.lowest_price or trade.entry_price
        stop = trade.current_stop_price
        flags = list(trade.flags)
        exit_raw = exit_time = reason = None
        ttl_at = trade.entry_time + timedelta(minutes=decision.trade_ttl_minutes)
        for bar in bars:
            stop_hit = stop is not None and (bar.low <= stop if sign == 1 else bar.high >= stop)
            target_hit = trade.target_price is not None and (
                bar.high >= trade.target_price if sign == 1 else bar.low <= trade.target_price
            )
            if stop_hit:
                exit_raw, exit_time, reason = stop, bar.close_time, ExitReason.STOP
                if target_hit:
                    flags.append("INTRABAR_ORDER_AMBIGUOUS")
                break
            if target_hit:
                exit_raw, exit_time, reason = trade.target_price, bar.close_time, ExitReason.TARGET
                break
            if bar.close_time >= ttl_at:
                exit_raw, exit_time, reason = bar.close, bar.close_time, ExitReason.TTL
                break
            highest, lowest = max(highest, bar.high), min(lowest, bar.low)
            if decision.strategy_version == "QUANT_TREND_V1":
                hour_bars = self.repository.candles("HYPE", "1h", bar.close_time)
                atr_value = atr(hour_bars, 14)
                if atr_value is not None:
                    candidate = highest - 3.0 * atr_value if sign == 1 else lowest + 3.0 * atr_value
                    stop = max(stop or candidate, candidate) if sign == 1 else min(stop or candidate, candidate)

        funding_cost = trade.funding_cost
        funding_cutoff = exit_time or cutoff
        for item in self.repository.funding("HYPE", funding_cutoff, start=trade.last_processed_at):
            if item.source_timestamp > trade.last_processed_at:
                funding_cost += sign * trade.entry_price * item.funding_rate
        updates = {"highest_price": highest, "lowest_price": lowest,
                   "current_stop_price": stop, "funding_cost": funding_cost,
            "last_processed_at": funding_cutoff, "flags": tuple(sorted(set(flags)))}
        if exit_raw is None:
            return trade.model_copy(update=updates)
        exit_price = exit_raw * (1 - sign * self.slippage_bps / 10_000)
        exit_slippage = abs(exit_price - exit_raw)
        exit_fee = exit_price * self.fee_bps / 10_000
        gross = sign * (exit_raw - trade.entry_price)
        total_fees = trade.fees + exit_fee
        total_slippage = trade.slippage_cost + exit_slippage
        net = gross - total_fees - total_slippage - funding_cost
        risk = abs(trade.entry_price - trade.stop_price) if trade.stop_price is not None else None
        updates.update({
            "exit_time": exit_time, "exit_price": exit_price, "exit_reason": reason,
            "fees": total_fees, "slippage_cost": total_slippage,
            "gross_pnl": gross, "net_pnl": net, "return_pct": net / trade.entry_price,
            "r_multiple": net / risk if risk else None, "status": TradeStatus.CLOSED,
            "last_processed_at": exit_time,
        })
        self.repository.save_fill(trade.paper_trade_id, exit_time, f"EXIT_{reason.value}", exit_price,
                                  exit_fee, exit_slippage, {"raw_price": exit_raw})
        return trade.model_copy(update=updates)


def simulate_trade(decision: StrategyDecision, one_minute_bars: Sequence[Candle], *,
                   latency_seconds: int = 3, fee_bps_per_side: float = 4.5,
                   slippage_bps_per_side: float = 2.0) -> PaperTrade | None:
    """Pure deterministic helper used for focused simulator tests."""
    if decision.decision == Decision.NO_TRADE:
        return None
    trade_id = str(uuid5(NAMESPACE_URL, f"paper:{decision.decision_id}"))
    sign = 1 if decision.decision == Decision.LONG else -1
    eligible = decision.created_at + timedelta(seconds=latency_seconds)
    bars = sorted((bar for bar in one_minute_bars if bar.close_time >= eligible), key=lambda item: item.close_time)
    if not bars:
        return PaperTrade(
            paper_trade_id=trade_id, strategy_decision_id=decision.decision_id,
            strategy_id=decision.strategy_id, snapshot_hash=decision.snapshot_hash,
            direction=decision.decision.value, signal_time=decision.created_at,
            exit_reason=ExitReason.NO_ENTRY, status=TradeStatus.SUPPRESSED_NO_ENTRY_DATA,
            last_processed_at=decision.created_at,
        )
    entry_bar = bars[0]
    raw_entry = entry_bar.close
    entry = raw_entry * (1 + sign * slippage_bps_per_side / 10_000)
    stop, target = decision.stop_reference, decision.target_reference
    exit_raw, exit_time, reason = bars[-1].close, bars[-1].close_time, ExitReason.TTL
    flags = ["ENTRY_FALLBACK_1M"]
    for bar in bars[1:] or bars:
        stop_hit = stop is not None and (bar.low <= stop if sign == 1 else bar.high >= stop)
        target_hit = target is not None and (bar.high >= target if sign == 1 else bar.low <= target)
        if stop_hit:
            exit_raw, exit_time, reason = stop, bar.close_time, ExitReason.STOP
            if target_hit:
                flags.append("INTRABAR_ORDER_AMBIGUOUS")
            break
        if target_hit:
            exit_raw, exit_time, reason = target, bar.close_time, ExitReason.TARGET
            break
    exit_price = exit_raw * (1 - sign * slippage_bps_per_side / 10_000)
    fees = (entry + exit_price) * fee_bps_per_side / 10_000
    slippage = abs(entry - raw_entry) + abs(exit_price - exit_raw)
    gross = sign * (exit_raw - raw_entry)
    net = gross - fees - slippage
    risk = abs(entry - stop) if stop is not None else None
    return PaperTrade(
        paper_trade_id=trade_id, strategy_decision_id=decision.decision_id,
        strategy_id=decision.strategy_id, snapshot_hash=decision.snapshot_hash,
        direction=decision.decision.value, signal_time=decision.created_at,
        entry_time=entry_bar.close_time, entry_price=entry, stop_price=stop,
        target_price=target, current_stop_price=stop, highest_price=max(b.high for b in bars),
        lowest_price=min(b.low for b in bars), exit_time=exit_time, exit_price=exit_price,
        exit_reason=reason, fees=fees, slippage_cost=slippage, gross_pnl=gross,
        net_pnl=net, return_pct=net / entry, r_multiple=net / risk if risk else None,
        status=TradeStatus.CLOSED, last_processed_at=exit_time, flags=tuple(flags),
    )
