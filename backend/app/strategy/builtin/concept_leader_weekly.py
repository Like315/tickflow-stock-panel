"""Concept-leader monthly/weekly trend strategy with disciplined daily exits."""

from __future__ import annotations

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_rolling_max,
    valid_shift,
)

META = {
    "id": "concept_leader_weekly",
    "name": "龙头概念月周趋势",
    "description": "月K多头(3月线>6月线) + 周K多头(4周线>10周线) + 5日线持有; 10%固定/移动止损, 跌破5日线止盈。",
    "tags": ["龙头", "概念", "月线", "周线", "趋势", "回撤控制"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 300,
        "market_cap_min": 20e8,
        "amount_min": 1e8,
        "exclude_st": True,
        "exclude_new_days": 120,
    },
    "params": [
        {"id": "weekly_fast", "label": "周线快均线", "type": "int", "default": 4, "min": 2, "max": 8, "step": 1},
        {"id": "weekly_slow", "label": "周线慢均线", "type": "int", "default": 10, "min": 6, "max": 20, "step": 1},
        {"id": "monthly_fast", "label": "月线快均线", "type": "int", "default": 3, "min": 2, "max": 6, "step": 1},
        {"id": "monthly_slow", "label": "月线慢均线", "type": "int", "default": 6, "min": 4, "max": 12, "step": 1},
        {"id": "min_momentum_20d", "label": "20日最低动量", "type": "float", "default": 0.08, "min": 0.0, "max": 1.0, "step": 0.01},
        {"id": "max_ma5_bias", "label": "距离5日线最大乖离", "type": "float", "default": 0.20, "min": 0.02, "max": 0.50, "step": 0.01},
        {"id": "entry_on_momentum_breakout", "label": "动量突破入场", "type": "bool", "default": True},
        {"id": "entry_on_ma5_reclaim", "label": "回踩收复5日线入场", "type": "bool", "default": True},
        {"id": "entry_on_20d_high_breakout", "label": "20日新高入场", "type": "bool", "default": False},
        {"id": "entry_once_per_trend", "label": "每轮趋势只首次入场", "type": "bool", "default": False},
        {"id": "exit_on_weekly_breakdown", "label": "跌破4周线出场", "type": "bool", "default": True},
        {"id": "exit_on_monthly_breakdown", "label": "跌破3月线出场", "type": "bool", "default": True},
    ],
    "scoring": {"momentum_20d": 0.55, "momentum_60d": 0.30, "amount": 0.15},
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["momentum_breakout", "ma5_reclaim", "high_20d_breakout"]
EXIT_SIGNALS = ["ma5_breakdown", "weekly_fast_breakdown", "monthly_fast_breakdown"]
STOP_LOSS = -0.10
TRAILING_STOP = -0.10
ALERTS = []


def _first_entry_per_trend(entry: np.ndarray, trend: np.ndarray) -> np.ndarray:
    """Keep the first trigger until the trend context fully resets."""
    out = np.zeros(entry.shape, dtype=bool)
    armed = np.ones(entry.shape[1], dtype=bool)
    for time_id in range(entry.shape[0]):
        active = trend[time_id]
        armed[~active] = True
        selected = active & entry[time_id] & armed
        out[time_id, selected] = True
        armed[selected] = False
    return out


def _weekly_averages(market: MarketDataMatrix, fast: int, slow: int) -> tuple[np.ndarray, np.ndarray]:
    """Return expanding current-week close averages without using future bars."""
    dates = np.asarray(market.timestamps).astype("datetime64[ms]").astype("datetime64[D]")
    week_ids = dates.astype("datetime64[W]")
    n_t, n_a = market.shape
    fast_out = np.full((n_t, n_a), np.nan, dtype=np.float32)
    slow_out = np.full((n_t, n_a), np.nan, dtype=np.float32)
    weekly: list[np.ndarray] = []

    for t in range(n_t):
        row = market.close[t]
        if t == 0 or week_ids[t] != week_ids[t - 1]:
            weekly.append(np.full(n_a, np.nan, dtype=np.float32))
        valid = np.isfinite(row)
        weekly[-1][valid] = row[valid]
        for window, output in ((fast, fast_out), (slow, slow_out)):
            if len(weekly) < window:
                continue
            block = np.stack(weekly[-window:])
            counts = np.isfinite(block).sum(axis=0)
            sums = np.nansum(block, axis=0)
            output[t] = np.where(counts == window, sums / window, np.nan)
    return fast_out, slow_out


def _monthly_averages(market: MarketDataMatrix, fast: int, slow: int) -> tuple[np.ndarray, np.ndarray]:
    """Return expanding current-month close averages without using future bars."""
    dates = np.asarray(market.timestamps).astype("datetime64[ms]").astype("datetime64[M]")
    n_t, n_a = market.shape
    fast_out = np.full((n_t, n_a), np.nan, dtype=np.float32)
    slow_out = np.full((n_t, n_a), np.nan, dtype=np.float32)
    monthly: list[np.ndarray] = []
    for t in range(n_t):
        row = market.close[t]
        if t == 0 or dates[t] != dates[t - 1]:
            monthly.append(np.full(n_a, np.nan, dtype=np.float32))
        valid = np.isfinite(row)
        monthly[-1][valid] = row[valid]
        for window, output in ((fast, fast_out), (slow, slow_out)):
            if len(monthly) < window:
                continue
            block = np.stack(monthly[-window:])
            counts = np.isfinite(block).sum(axis=0)
            output[t] = np.where(counts == window, np.nansum(block, axis=0) / window, np.nan)
    return fast_out, slow_out


class ConceptLeaderWeeklyStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "amount"})

    def required_warmup_bars(self, params: dict) -> int:
        return max(140, int(params.get("monthly_slow", 6)) * 24)

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        fast = int(params.get("weekly_fast", 4))
        slow = int(params.get("weekly_slow", 10))
        if fast >= slow:
            raise ValueError("weekly_fast must be smaller than weekly_slow")
        monthly_fast_n = int(params.get("monthly_fast", 3))
        monthly_slow_n = int(params.get("monthly_slow", 6))
        if monthly_fast_n >= monthly_slow_n:
            raise ValueError("monthly_fast must be smaller than monthly_slow")
        weekly_fast, weekly_slow = _weekly_averages(market, fast, slow)
        monthly_fast, monthly_slow = _monthly_averages(market, monthly_fast_n, monthly_slow_n)
        ma5 = matrix_feature(market, "ma5")
        momentum = matrix_feature(market, "momentum_20d")
        ma5_bias = np.full(market.shape, np.nan, dtype=np.float32)
        np.divide(market.close, ma5, out=ma5_bias, where=np.isfinite(ma5) & (ma5 != 0))
        ma5_bias -= 1.0

        trend_context = (
            (market.close > weekly_fast)
            & (weekly_fast > weekly_slow)
            & (market.close > monthly_fast)
            & (monthly_fast > monthly_slow)
            & (market.close >= ma5)
            & (ma5_bias <= float(params.get("max_ma5_bias", 0.20)))
        )
        previous_close = valid_shift(market.close, 1, np.isfinite(market.close))
        previous_ma5 = valid_shift(ma5, 1, np.isfinite(ma5))
        previous_momentum = valid_shift(momentum, 1, np.isfinite(momentum))
        momentum_floor = float(params.get("min_momentum_20d", 0.08))
        momentum_breakout = (
            trend_context
            & (momentum >= momentum_floor)
            & (previous_momentum < momentum_floor)
            & bool(params.get("entry_on_momentum_breakout", True))
        )
        ma5_reclaim = (
            trend_context
            & (momentum >= momentum_floor)
            & (previous_close < previous_ma5)
            & bool(params.get("entry_on_ma5_reclaim", True))
        )
        close_valid = np.isfinite(market.close)
        previous_20d_high = valid_shift(
            valid_rolling_max(market.close, close_valid, 20),
            1,
            close_valid,
        )
        high_20d_breakout = (
            trend_context
            & (market.close > previous_20d_high)
            & bool(params.get("entry_on_20d_high_breakout", False))
        )
        entry = momentum_breakout | ma5_reclaim | high_20d_breakout
        if params.get("entry_once_per_trend", False):
            entry = _first_entry_per_trend(entry, trend_context)

        ma5_breakdown = (market.close < ma5) & (previous_close >= previous_ma5)
        previous_weekly_fast = valid_shift(weekly_fast, 1, np.isfinite(weekly_fast))
        weekly_breakdown = (
            (market.close < weekly_fast)
            & (previous_close >= previous_weekly_fast)
            & bool(params.get("exit_on_weekly_breakdown", True))
        )
        previous_monthly_fast = valid_shift(monthly_fast, 1, np.isfinite(monthly_fast))
        monthly_breakdown = (
            (market.close < monthly_fast)
            & (previous_close >= previous_monthly_fast)
            & bool(params.get("exit_on_monthly_breakdown", True))
        )
        exit_ = ma5_breakdown | weekly_breakdown | monthly_breakdown
        entry_code = np.where(
            entry,
            np.where(momentum_breakout, 0, np.where(ma5_reclaim, 1, 2)),
            -1,
        ).astype(np.int16)
        exit_code = np.where(
            ma5_breakdown,
            0,
            np.where(weekly_breakdown, 1, np.where(monthly_breakdown, 2, -1)),
        ).astype(np.int16)
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=entry_code,
            exit_signal_code=exit_code,
            entry_signal_ids=("momentum_breakout", "ma5_reclaim", "high_20d_breakout"),
            exit_signal_ids=("ma5_breakdown", "weekly_fast_breakdown", "monthly_fast_breakdown"),
        )


MATRIX_STRATEGY = ConceptLeaderWeeklyStrategy()
