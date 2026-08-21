"""Deterministic investment-expert decision runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import (
    ExecutionEvent,
    ExpertPolicy,
    MinuteBar,
    OrderIntent,
    TrainedDecisionModel,
)


@dataclass
class _SymbolState:
    bars: int = 0
    cumulative_volume: float = 0.0
    cumulative_amount: float = 0.0
    previous_high: float | None = None


@dataclass
class RuntimeStep:
    execution_events: list[ExecutionEvent] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    submitted_event: ExecutionEvent | None = None


class InvestmentExpertRuntime:
    def __init__(
        self,
        *,
        session_id: str,
        policy: ExpertPolicy,
        candidates: set[str],
        executor: StrictMinuteExecutor,
        candidate_context: dict[str, dict[str, Any]] | None = None,
        decision_model: TrainedDecisionModel | None = None,
        entry_guard: Callable[[], bool] | None = None,
    ) -> None:
        self.session_id = session_id
        self.policy = policy
        self.candidates = candidates
        self.executor = executor
        self.candidate_context = candidate_context or {}
        self.decision_model = decision_model
        self.entry_guard = entry_guard
        self.entries_enabled = True
        self.states: dict[str, _SymbolState] = {}

    def on_bar(self, bar: MinuteBar) -> RuntimeStep:
        execution_events = self.executor.process_bar(bar)
        if any(event.event_type == "data_rejected" for event in execution_events):
            return RuntimeStep(execution_events=execution_events)

        state = self.states.setdefault(bar.symbol, _SymbolState())
        previous_high = state.previous_high
        state.bars += 1
        state.cumulative_volume += bar.volume
        state.cumulative_amount += bar.amount
        state.previous_high = (
            max(previous_high, bar.raw_high) if previous_high is not None else bar.raw_high
        )
        is_candidate = bar.symbol in self.candidates
        if not is_candidate and self.executor.total_shares(bar.symbol) <= 0:
            return RuntimeStep(execution_events=execution_events)

        volume_shares = state.cumulative_volume * self.executor.constitution.volume_unit_shares
        vwap = state.cumulative_amount / volume_shares if volume_shares > 0 else None
        available_at = bar.datetime + timedelta(minutes=1)
        features = {
            "event_time": bar.datetime.isoformat(),
            "available_at": available_at.isoformat(),
            "bars": state.bars,
            "raw_close": bar.raw_close,
            "vwap": vwap,
            "vwap_bias": (bar.raw_close / vwap - 1) if vwap else None,
            "previous_high": previous_high,
            "breakout_pct": (bar.raw_close / previous_high - 1) if previous_high else None,
            "total_shares": self.executor.total_shares(bar.symbol),
            "settled_shares": self.executor.settled_shares(bar.symbol, bar.datetime.date()),
            "is_candidate": is_candidate,
        }
        context = self.candidate_context.get(bar.symbol, {})
        features["daily_momentum_20d"] = context.get("daily_momentum_20d")
        features["candidate_score"] = context.get("score")
        features["overnight_us_available"] = context.get("overnight_us_available")
        features["overnight_us_score"] = context.get("overnight_us_score")
        features["overnight_us_tilt"] = context.get("overnight_us_tilt")
        features["overnight_us_factor"] = context.get("overnight_us_factor")
        features["overnight_us_module"] = context.get("overnight_us_module")
        features["overnight_us_module_symbol"] = context.get("overnight_us_module_symbol")
        features["overnight_us_match_confidence"] = context.get("overnight_us_match_confidence")
        features["news_sentiment_score"] = context.get("news_sentiment_score")
        features["candidate_news_sentiment"] = context.get("candidate_news_sentiment")
        features["news_sentiment_confidence"] = context.get("news_sentiment_confidence")
        features["news_factor_score"] = context.get("news_factor_score")
        features["model_probability"] = (
            self.decision_model.predict_probability(features)
            if self.decision_model is not None
            else None
        )
        action, reason = self._decide(bar, features)
        decision_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{self.session_id}:{self.policy.id}:{bar.symbol}:{available_at.isoformat()}:{action}",
            )
        )
        decision = {
            "id": decision_id,
            "symbol": bar.symbol,
            "decision_time": available_at,
            "action": action,
            "features": features,
            "reason": reason,
        }
        submitted = None
        if action in {"buy", "sell"} and not self._has_pending(bar.symbol):
            shares = self._order_shares(bar, action)
            if shares > 0:
                intent = OrderIntent(
                    id=f"order_{decision_id}",
                    decision_id=decision_id,
                    symbol=bar.symbol,
                    side=action,
                    shares=shares,
                    signal_time=available_at,
                    reason=reason,
                )
                submitted = self.executor.submit(intent)
        return RuntimeStep(
            execution_events=execution_events,
            decision=decision,
            submitted_event=submitted,
        )

    def _decide(self, bar: MinuteBar, features: dict[str, Any]) -> tuple[str, str]:
        clock = bar.datetime.strftime("%H:%M")
        shares = int(features["total_shares"])
        settled = int(features["settled_shares"])
        vwap_bias = features["vwap_bias"]
        breakout = features["breakout_pct"]
        overnight_factor = max(
            -1.0,
            min(1.0, float(features.get("overnight_us_factor") or 0.0)),
        )
        overnight_exit_adjustment = overnight_factor * self.policy.overnight_us_exit_weight * 0.01
        effective_exit_vwap_bias = self.policy.exit_vwap_bias - overnight_exit_adjustment
        effective_take_profit_pct = max(
            0.001,
            self.policy.take_profit_pct
            + overnight_factor * self.policy.overnight_us_exit_weight * 0.10,
        )
        features["overnight_us_exit_adjustment"] = overnight_exit_adjustment
        features["effective_exit_vwap_bias"] = effective_exit_vwap_bias
        features["effective_take_profit_pct"] = effective_take_profit_pct
        if settled > 0:
            lots = [lot for lot in self.executor.lots if lot.symbol == bar.symbol]
            invested = sum(lot.remaining_shares * lot.entry_price for lot in lots)
            held = sum(lot.remaining_shares for lot in lots)
            entry_price = invested / held if held else None
            pnl_pct = bar.raw_close / entry_price - 1 if entry_price else None
            if pnl_pct is not None and pnl_pct <= self.policy.stop_loss_pct:
                return "sell", "settled_position_stop_loss"
            if pnl_pct is not None and pnl_pct >= effective_take_profit_pct:
                return "sell", "settled_position_take_profit"
            oldest_date = min(lot.acquired_date for lot in lots)
            if (bar.datetime.date() - oldest_date).days >= self.policy.max_hold_days:
                return "sell", "settled_position_max_hold"
            if vwap_bias is not None and vwap_bias <= effective_exit_vwap_bias:
                return "sell", "settled_position_vwap_breakdown"
        if shares > 0:
            return "hold", "position_not_sellable_or_exit_not_triggered"
        if not features["is_candidate"]:
            return "abstain", "carryover_exit_only_symbol"
        if not self.entries_enabled or (self.entry_guard is not None and not self.entry_guard()):
            return "abstain", "risk_kill_switch_entries_disabled"
        if not (self.policy.entry_start <= clock <= self.policy.entry_end):
            return "abstain", "outside_entry_window"
        if int(features["bars"]) < self.policy.min_completed_bars:
            return "abstain", "insufficient_completed_bars"
        news_factor_score = max(
            -1.0,
            min(1.0, float(features.get("news_factor_score") or 0.0)),
        )
        news_confirmation_bias = news_factor_score * self.policy.news_candidate_weight * 0.004
        raw_overnight_entry_adjustment = (
            overnight_factor * self.policy.overnight_us_entry_weight * 0.01
        )
        if raw_overnight_entry_adjustment > 0:
            positive_adjustment_cap = max(
                0.0,
                min(
                    self.policy.min_vwap_bias - news_confirmation_bias,
                    self.policy.min_breakout_pct - news_confirmation_bias,
                )
                * 0.5,
            )
            overnight_entry_adjustment = min(
                raw_overnight_entry_adjustment,
                positive_adjustment_cap,
            )
        else:
            overnight_entry_adjustment = raw_overnight_entry_adjustment
        required_vwap_bias = (
            self.policy.min_vwap_bias - news_confirmation_bias - overnight_entry_adjustment
        )
        required_breakout_pct = max(
            0.0,
            self.policy.min_breakout_pct - news_confirmation_bias - overnight_entry_adjustment,
        )
        required_probability = max(
            0.50,
            min(
                0.95,
                self.policy.entry_probability_threshold
                - overnight_factor * self.policy.overnight_us_entry_weight * 0.10,
            ),
        )
        features["news_confirmation_bias"] = news_confirmation_bias
        features["raw_overnight_us_entry_adjustment"] = raw_overnight_entry_adjustment
        features["overnight_us_entry_adjustment"] = overnight_entry_adjustment
        features["required_vwap_bias"] = required_vwap_bias
        features["required_breakout_pct"] = required_breakout_pct
        features["required_probability"] = required_probability
        if vwap_bias is None or vwap_bias < required_vwap_bias:
            return "abstain", "vwap_confirmation_missing"
        if breakout is None or breakout < required_breakout_pct:
            return "abstain", "opening_range_breakout_missing"
        probability = features.get("model_probability")
        if self.decision_model is not None and probability is None:
            return "abstain", "model_features_incomplete"
        if probability is not None and probability < required_probability:
            return "abstain", "trained_probability_below_threshold"
        return "buy", "vwap_and_opening_range_confirmed"

    def _has_pending(self, symbol: str) -> bool:
        return any(pending.intent.symbol == symbol for pending in self.executor.pending.values())

    def _order_shares(self, bar: MinuteBar, action: str) -> int:
        if action == "sell":
            return self.executor.settled_shares(bar.symbol, bar.datetime.date())
        target_value = self.executor.equity() * self.policy.target_position_pct
        lot_size = self.executor.constitution.lot_size
        shares = int(target_value / bar.raw_close)
        return shares - shares % lot_size
