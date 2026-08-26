"""Deterministic investment-expert decision runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal, TypeAlias, cast
from uuid import NAMESPACE_URL, uuid5

from app.market_calendar import cn_trading_days_elapsed
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
    """单只股票在当前会话中的分钟聚合状态。"""

    bars: int = 0
    cumulative_volume: float = 0.0
    cumulative_amount: float = 0.0
    previous_high: float | None = None


@dataclass
class RuntimeStep:
    """一次分钟线处理产生的决策与执行事件。"""

    execution_events: list[ExecutionEvent] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    submitted_event: ExecutionEvent | None = None


@dataclass(frozen=True, slots=True)
class InvestmentExpertRuntimeConfig:
    """投资专家运行时的会话依赖与可选决策上下文。"""

    session_id: str
    policy: ExpertPolicy
    candidates: set[str]
    executor: StrictMinuteExecutor
    candidate_context: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_model: TrainedDecisionModel | None = None
    entry_guard: Callable[[], bool] | None = None


DecisionAction: TypeAlias = Literal["buy", "sell", "hold", "abstain"]
Decision: TypeAlias = tuple[DecisionAction, str]


class InvestmentExpertRuntime:
    """按固定风控宪法执行投资专家分钟级决策。"""

    def __init__(self, config: InvestmentExpertRuntimeConfig) -> None:
        """使用经过类型约束的配置初始化决策运行时。"""
        self.session_id = config.session_id
        self.policy = config.policy
        self.candidates = config.candidates
        self.executor = config.executor
        self.candidate_context = config.candidate_context
        self.decision_model = config.decision_model
        self.entry_guard = config.entry_guard
        self.entries_enabled = True
        self.states: dict[str, _SymbolState] = {}

    def on_bar(self, bar: MinuteBar) -> RuntimeStep:
        """处理一根已完成分钟线并返回决策与执行结果。"""
        execution_events = self.executor.process_bar(bar)
        if any(event.event_type == "data_rejected" for event in execution_events):
            return RuntimeStep(execution_events=execution_events)
        state, previous_high = self._update_symbol_state(bar)
        is_candidate = bar.symbol in self.candidates
        if not is_candidate and self.executor.total_shares(bar.symbol) <= 0:
            return RuntimeStep(execution_events=execution_events)
        features = self._decision_features(bar, state, previous_high, is_candidate)
        available_at = bar.datetime + timedelta(minutes=1)
        features["available_at"] = available_at.isoformat()
        action, reason = self._decide(bar, features)
        decision_id = self._decision_id(bar, available_at.isoformat(), action)
        decision = {
            "id": decision_id,
            "symbol": bar.symbol,
            "decision_time": available_at,
            "action": action,
            "features": features,
            "reason": reason,
        }
        submitted = self._submit_order(bar, decision_id, (action, reason), features)
        return RuntimeStep(execution_events, decision, submitted)

    def _update_symbol_state(self, bar: MinuteBar) -> tuple[_SymbolState, float | None]:
        """更新股票分钟聚合状态并返回更新前最高价。"""
        state = self.states.setdefault(bar.symbol, _SymbolState())
        previous_high = state.previous_high
        state.bars += 1
        state.cumulative_volume += bar.volume
        state.cumulative_amount += bar.amount
        state.previous_high = (
            max(previous_high, bar.raw_high) if previous_high is not None else bar.raw_high
        )
        return state, previous_high

    def _holding_features(self, bar: MinuteBar) -> dict[str, Any]:
        """计算持仓交易日和满足退出期限的可卖数量。"""
        open_lots = [
            lot
            for lot in self.executor.lots
            if lot.symbol == bar.symbol and lot.remaining_shares > 0
        ]
        holding_days = {
            lot.lot_id: cn_trading_days_elapsed(lot.acquired_date, bar.datetime.date())
            for lot in open_lots
        }
        constitution = self.executor.constitution
        return {
            "min_hold_trading_days": constitution.min_hold_trading_days,
            "max_hold_trading_days": constitution.max_hold_trading_days,
            "oldest_hold_trading_days": max(holding_days.values(), default=0),
            "min_hold_eligible_shares": sum(
                lot.remaining_shares
                for lot in open_lots
                if holding_days[lot.lot_id] >= constitution.min_hold_trading_days
            ),
            "max_hold_expired_shares": sum(
                lot.remaining_shares
                for lot in open_lots
                if holding_days[lot.lot_id] >= constitution.max_hold_trading_days
            ),
        }

    def _candidate_features(self, symbol: str) -> dict[str, Any]:
        """提取候选股票的日线、隔夜美股和新闻特征。"""
        context = self.candidate_context.get(symbol, {})
        keys = (
            "daily_momentum_20d",
            "score",
            "overnight_us_available",
            "overnight_us_score",
            "overnight_us_tilt",
            "overnight_us_factor",
            "overnight_us_module",
            "overnight_us_module_symbol",
            "overnight_us_match_confidence",
            "news_sentiment_score",
            "candidate_news_sentiment",
            "news_sentiment_confidence",
            "news_factor_score",
        )
        result = {key: context.get(key) for key in keys}
        result["candidate_score"] = result.pop("score")
        return result

    def _decision_features(
        self,
        bar: MinuteBar,
        state: _SymbolState,
        previous_high: float | None,
        is_candidate: bool,
    ) -> dict[str, Any]:
        """构造当前分钟决策所需的完整时点特征。"""
        volume_shares = state.cumulative_volume * self.executor.constitution.volume_unit_shares
        vwap = state.cumulative_amount / volume_shares if volume_shares > 0 else None
        features: dict[str, Any] = {
            "event_time": bar.datetime.isoformat(),
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
        features.update(self._holding_features(bar))
        features.update(self._candidate_features(bar.symbol))
        features["model_probability"] = (
            self.decision_model.predict_probability(features)
            if self.decision_model is not None
            else None
        )
        return features

    def _decision_id(self, bar: MinuteBar, available_at: str, action: DecisionAction) -> str:
        """生成可重放且稳定的决策标识。"""
        value = f"{self.session_id}:{self.policy.id}:{bar.symbol}:{available_at}:{action}"
        return str(uuid5(NAMESPACE_URL, value))

    def _submit_order(
        self,
        bar: MinuteBar,
        decision_id: str,
        decision: Decision,
        features: dict[str, Any],
    ) -> ExecutionEvent | None:
        """在没有同标的待处理订单时提交买卖意图。"""
        action, reason = decision
        if action not in {"buy", "sell"} or self._has_pending(bar.symbol):
            return None
        shares = self._order_shares(bar, action, reason, features)
        if shares <= 0:
            return None
        order_side = cast(Literal["buy", "sell"], action)
        intent = OrderIntent(
            id=f"order_{decision_id}",
            decision_id=decision_id,
            symbol=bar.symbol,
            side=order_side,
            shares=shares,
            signal_time=bar.datetime + timedelta(minutes=1),
            reason=reason,
        )
        return self.executor.submit(intent)

    def _exit_thresholds(self, features: dict[str, Any]) -> tuple[float, float]:
        """计算隔夜美股影响后的退出阈值并写入审计特征。"""
        factor = max(-1.0, min(1.0, float(features.get("overnight_us_factor") or 0.0)))
        adjustment = factor * self.policy.overnight_us_exit_weight * 0.01
        vwap_bias = self.policy.exit_vwap_bias - adjustment
        take_profit = max(
            0.001,
            self.policy.take_profit_pct + factor * self.policy.overnight_us_exit_weight * 0.10,
        )
        features["overnight_us_exit_adjustment"] = adjustment
        features["effective_exit_vwap_bias"] = vwap_bias
        features["effective_take_profit_pct"] = take_profit
        return vwap_bias, take_profit

    def _position_decision(
        self,
        bar: MinuteBar,
        features: dict[str, Any],
        thresholds: tuple[float, float],
    ) -> Decision | None:
        """按止损、最短持有期和最长持有期判断持仓退出。"""
        if int(features["settled_shares"]) <= 0:
            return None
        lots = [
            lot
            for lot in self.executor.lots
            if lot.symbol == bar.symbol and lot.remaining_shares > 0
        ]
        invested = sum(lot.remaining_shares * lot.entry_price for lot in lots)
        held = sum(lot.remaining_shares for lot in lots)
        pnl_pct = bar.raw_close / (invested / held) - 1 if held else None
        if pnl_pct is not None and pnl_pct <= self.policy.stop_loss_pct:
            return "sell", "settled_position_stop_loss"
        if int(features.get("min_hold_eligible_shares") or 0) <= 0:
            return "hold", "position_min_hold_not_reached"
        if pnl_pct is not None and pnl_pct >= thresholds[1]:
            return "sell", "settled_position_take_profit"
        if int(features.get("max_hold_expired_shares") or 0) > 0:
            return "sell", "settled_position_max_hold"
        vwap_bias = features["vwap_bias"]
        if vwap_bias is not None and vwap_bias <= thresholds[0]:
            return "sell", "settled_position_vwap_breakdown"
        return None

    def _entry_thresholds(self, features: dict[str, Any]) -> tuple[float, float, float]:
        """计算新闻与隔夜行情影响后的入场阈值。"""
        overnight = max(-1.0, min(1.0, float(features.get("overnight_us_factor") or 0.0)))
        news = max(-1.0, min(1.0, float(features.get("news_factor_score") or 0.0)))
        news_bias = news * self.policy.news_candidate_weight * 0.004
        raw_adjustment = overnight * self.policy.overnight_us_entry_weight * 0.01
        positive_cap = max(
            0.0,
            min(self.policy.min_vwap_bias - news_bias, self.policy.min_breakout_pct - news_bias)
            * 0.5,
        )
        adjustment = min(raw_adjustment, positive_cap) if raw_adjustment > 0 else raw_adjustment
        required_vwap = self.policy.min_vwap_bias - news_bias - adjustment
        required_breakout = max(0.0, self.policy.min_breakout_pct - news_bias - adjustment)
        required_probability = max(
            0.50,
            min(
                0.95,
                self.policy.entry_probability_threshold
                - overnight * self.policy.overnight_us_entry_weight * 0.10,
            ),
        )
        features.update(
            {
                "news_confirmation_bias": news_bias,
                "raw_overnight_us_entry_adjustment": raw_adjustment,
                "overnight_us_entry_adjustment": adjustment,
                "required_vwap_bias": required_vwap,
                "required_breakout_pct": required_breakout,
                "required_probability": required_probability,
            }
        )
        return required_vwap, required_breakout, required_probability

    def _entry_decision(self, bar: MinuteBar, features: dict[str, Any]) -> Decision:
        """完成无持仓候选股票的入场门控判断。"""
        if not features["is_candidate"]:
            return "abstain", "carryover_exit_only_symbol"
        if not self.entries_enabled or (self.entry_guard is not None and not self.entry_guard()):
            return "abstain", "risk_kill_switch_entries_disabled"
        clock = bar.datetime.strftime("%H:%M")
        if not (self.policy.entry_start <= clock <= self.policy.entry_end):
            return "abstain", "outside_entry_window"
        if int(features["bars"]) < self.policy.min_completed_bars:
            return "abstain", "insufficient_completed_bars"
        required_vwap, required_breakout, required_probability = self._entry_thresholds(features)
        if features["vwap_bias"] is None or features["vwap_bias"] < required_vwap:
            return "abstain", "vwap_confirmation_missing"
        if features["breakout_pct"] is None or features["breakout_pct"] < required_breakout:
            return "abstain", "opening_range_breakout_missing"
        probability = features.get("model_probability")
        if self.decision_model is not None and probability is None:
            return "abstain", "model_features_incomplete"
        if probability is not None and probability < required_probability:
            return "abstain", "trained_probability_below_threshold"
        return "buy", "vwap_and_opening_range_confirmed"

    def _decide(self, bar: MinuteBar, features: dict[str, Any]) -> Decision:
        """按持仓优先原则选择退出、持有、放弃或买入。"""
        shares = int(features["total_shares"])
        position_decision = self._position_decision(bar, features, self._exit_thresholds(features))
        if position_decision is not None:
            return position_decision
        if shares > 0:
            return "hold", "position_not_sellable_or_exit_not_triggered"
        return self._entry_decision(bar, features)

    def _has_pending(self, symbol: str) -> bool:
        """判断股票是否已有待处理订单。"""
        return any(pending.intent.symbol == symbol for pending in self.executor.pending.values())

    def _order_shares(
        self,
        bar: MinuteBar,
        action: str,
        reason: str,
        features: dict[str, Any],
    ) -> int:
        """按退出原因或目标仓位计算整手下单数量。"""
        if action == "sell":
            if reason == "settled_position_max_hold":
                return int(features.get("max_hold_expired_shares") or 0)
            if reason != "settled_position_stop_loss":
                return int(features.get("min_hold_eligible_shares") or 0)
            return self.executor.settled_shares(bar.symbol, bar.datetime.date())
        target_value = self.executor.equity() * self.policy.target_position_pct
        lot_size = self.executor.constitution.lot_size
        shares = int(target_value / bar.raw_close)
        return shares - shares % lot_size
