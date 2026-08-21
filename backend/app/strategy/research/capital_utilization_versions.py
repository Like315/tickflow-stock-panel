"""Research variants that widen C signals and add a guarded trend sleeve."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

import numpy as np
import polars as pl

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_rolling_mean,
    valid_shift,
)
from app.strategy.builtin.volume_dry_breakout import (
    MATRIX_STRATEGY as DAILY_VOLUME_STRATEGY,
)
from app.strategy.research.volume_dry_breakout_versions import (
    C_QUALITY_PARAMS,
    SECTOR_LEADER_SCORE,
)

C_EXPANDED_PARAMS = {
    **C_QUALITY_PARAMS,
    "setup_vol_ratio_min": 2.0,
}

C_MARKET_CONTEXT = {
    **SECTOR_LEADER_SCORE,
    "market_min_score": 50.0,
    "market_min_consecutive_days": 2,
}

INTRADAY_MARKET_CONTEXT = {
    **C_MARKET_CONTEXT,
    # A 14:30 historical replay has no point-in-time full-market snapshot.
    # Use the previous completed close and fail closed on missing minute bars.
    "lag_bars": 1,
}

LEADER_SUPPLEMENT_PARAMS = {
    "entry_on_momentum_breakout": False,
    "entry_on_ma5_reclaim": True,
    "entry_on_20d_high_breakout": True,
    "entry_once_per_trend": True,
}


@dataclass(frozen=True)
class CapitalUtilizationVersion:
    id: str
    label: str
    strategy_id: str
    params: dict[str, Any]
    overrides: dict[str, Any]
    max_exposure_pct: float
    max_positions: int
    timing: str


VERSIONS = (
    CapitalUtilizationVersion(
        id="c2_daily_expanded",
        label="C2-日K扩容",
        strategy_id="volume_dry_breakout",
        params=dict(C_EXPANDED_PARAMS),
        overrides={"sector_context_filter": dict(C_MARKET_CONTEXT)},
        max_exposure_pct=0.50,
        max_positions=2,
        timing="T日收盘确认, T+1开盘",
    ),
    CapitalUtilizationVersion(
        id="cm_intraday_1430",
        label="CM-14:30分K确认",
        strategy_id="volume_dry_breakout",
        params=dict(C_EXPANDED_PARAMS),
        overrides={"sector_context_filter": dict(INTRADAY_MARKET_CONTEXT)},
        max_exposure_pct=0.50,
        max_positions=3,
        timing="T日14:30确认, 下一分钟开盘",
    ),
    CapitalUtilizationVersion(
        id="l_first_trend_entry",
        label="L-龙头首次趋势",
        strategy_id="concept_leader_weekly",
        params=dict(LEADER_SUPPLEMENT_PARAMS),
        overrides={"sector_context_filter": dict(C_MARKET_CONTEXT)},
        max_exposure_pct=0.20,
        max_positions=1,
        timing="T日收盘确认, T+1开盘",
    ),
)


class VolumeDryBreakoutIntradayCandidateStrategy:
    """T-1 setup only; T-day price/volume is confirmed from completed minute bars."""

    def required_fields(self) -> frozenset[str]:
        return DAILY_VOLUME_STRATEGY.required_fields()

    def required_warmup_bars(self, params: dict) -> int:
        return DAILY_VOLUME_STRATEGY.required_warmup_bars(params)

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        previous_open = valid_shift(market.open, 1)
        previous_high = valid_shift(market.high, 1)
        previous_low = valid_shift(market.low, 1)
        previous_close = valid_shift(market.close, 1)
        previous_vol_ratio = valid_shift(matrix_feature(market, "vol_ratio_5d"), 1)

        setup_range = previous_high - previous_low
        setup_body = np.abs(previous_close - previous_open)
        setup_lower_wick = np.minimum(previous_open, previous_close) - previous_low
        candidate = (
            (previous_vol_ratio >= float(params.get("setup_vol_ratio_min", 2.0)))
            & (setup_range > 0)
            & (setup_body <= setup_range * float(params.get("max_body_to_range", 0.25)))
            & (setup_lower_wick >= setup_range * float(params.get("min_lower_wick_to_range", 0.45)))
        )

        daily = DAILY_VOLUME_STRATEGY.compute_signals(market, params)
        return make_signal_matrix(
            market.shape,
            entry=candidate.astype(np.uint8),
            exit=daily.exit,
            entry_signal_code=np.where(candidate, 0, -1).astype(np.int16),
            exit_signal_code=daily.exit_signal_code,
            entry_signal_ids=("signal_volume_dry_breakout_1430",),
            exit_signal_ids=daily.exit_signal_ids,
        )


INTRADAY_CANDIDATE_STRATEGY = VolumeDryBreakoutIntradayCandidateStrategy()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _minute_key(value: Any) -> tuple[str, time] | None:
    if not isinstance(value, datetime):
        return None
    return value.date().isoformat(), value.time().replace(tzinfo=None)


def confirm_intraday_entries(
    market: MarketDataMatrix,
    signals: SignalMatrix,
    minute_df: pl.DataFrame,
    *,
    cutoff: time = time(14, 30),
    min_last_bar: time = time(14, 25),
    cumulative_volume_ratio_max: float = 0.65,
    ma20_bias_max: float = 0.08,
    quality_guard_ma20_bias_min: float = 0.05,
    quality_guard_margin_max: float = 0.01,
) -> tuple[np.ndarray, np.ndarray, dict[str, int | float | str]]:
    """Confirm T entries using only completed bars through 14:30.

    The returned fill is the next minute bar's open. Candidate symbol-days without
    complete minute coverage are rejected instead of falling back to daily data.
    """
    candidate_points = np.argwhere(signals.entry != 0)
    confirmed = np.zeros(market.shape, dtype=np.uint8)
    fill_price = np.full(market.shape, np.nan, dtype=np.float32)
    stats: dict[str, int | float | str] = {
        "candidate_symbol_days": len(candidate_points),
        "minute_covered_symbol_days": 0,
        "confirmed_symbol_days": 0,
        "missing_minute_symbol_days": 0,
        "rejected_price": 0,
        "rejected_volume": 0,
        "rejected_extension": 0,
        "rejected_quality": 0,
        "cutoff": cutoff.strftime("%H:%M"),
    }
    required = {"symbol", "datetime", "open", "close", "volume"}
    if (
        not candidate_points.size
        or minute_df.is_empty()
        or not required.issubset(minute_df.columns)
    ):
        stats["missing_minute_symbol_days"] = len(candidate_points)
        stats["minute_coverage"] = 0.0
        return confirmed, fill_price, stats

    rows_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in minute_df.sort(["symbol", "datetime"]).iter_rows(named=True):
        resolved = _minute_key(row.get("datetime"))
        if resolved is None:
            continue
        date_text, _ = resolved
        rows_by_key.setdefault((str(row.get("symbol")), date_text), []).append(row)

    close_valid = np.isfinite(market.close)
    previous_high = valid_shift(market.high, 1, np.isfinite(market.high))
    previous_volume = valid_shift(market.volume, 1, np.isfinite(market.volume))
    prior_19_close_mean = valid_shift(
        valid_rolling_mean(market.close, close_valid, 19),
        1,
        close_valid,
    )

    for time_id_raw, asset_id_raw in candidate_points:
        time_id = int(time_id_raw)
        asset_id = int(asset_id_raw)
        date_text = market.timestamp_labels[time_id][:10]
        symbol = market.symbols[asset_id]
        rows = rows_by_key.get((symbol, date_text), [])
        points: list[tuple[time, float, float, float]] = []
        for row in rows:
            resolved = _minute_key(row.get("datetime"))
            open_price = _finite(row.get("open"))
            close_price = _finite(row.get("close"))
            volume = _finite(row.get("volume"))
            if (
                resolved is None
                or open_price is None
                or close_price is None
                or volume is None
                or volume < 0
            ):
                continue
            points.append((resolved[1], open_price, close_price, volume))

        cutoff_ids = [idx for idx, point in enumerate(points) if point[0] <= cutoff]
        if not cutoff_ids:
            stats["missing_minute_symbol_days"] += 1
            continue
        cutoff_id = cutoff_ids[-1]
        if points[cutoff_id][0] < min_last_bar or cutoff_id + 1 >= len(points):
            stats["missing_minute_symbol_days"] += 1
            continue
        stats["minute_covered_symbol_days"] += 1

        current_price = points[cutoff_id][2]
        next_open = points[cutoff_id + 1][1]
        day_open = points[0][1]
        prev_high = float(previous_high[time_id, asset_id])
        prev_volume = float(previous_volume[time_id, asset_id])
        prior_mean = float(prior_19_close_mean[time_id, asset_id])
        if not all(
            np.isfinite(value) and value > 0
            for value in (prev_high, prev_volume, prior_mean, next_open)
        ):
            stats["missing_minute_symbol_days"] += 1
            stats["minute_covered_symbol_days"] -= 1
            continue

        provisional_ma20 = (prior_mean * 19.0 + current_price) / 20.0
        if (
            current_price <= prev_high
            or current_price <= day_open
            or current_price <= provisional_ma20
        ):
            stats["rejected_price"] += 1
            continue
        cumulative_volume = sum(point[3] for point in points[: cutoff_id + 1])
        if cumulative_volume > prev_volume * cumulative_volume_ratio_max:
            stats["rejected_volume"] += 1
            continue
        ma20_bias = current_price / provisional_ma20 - 1.0
        if ma20_bias > ma20_bias_max:
            stats["rejected_extension"] += 1
            continue
        breakout_margin = current_price / prev_high - 1.0
        if ma20_bias >= quality_guard_ma20_bias_min and breakout_margin <= quality_guard_margin_max:
            stats["rejected_quality"] += 1
            continue

        confirmed[time_id, asset_id] = 1
        fill_price[time_id, asset_id] = np.float32(next_open)
        stats["confirmed_symbol_days"] += 1

    candidates = int(stats["candidate_symbol_days"])
    stats["minute_coverage"] = round(
        float(stats["minute_covered_symbol_days"]) / candidates if candidates else 0.0,
        4,
    )
    return confirmed, fill_price, stats


def summarize_activity(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    *,
    initial_capital: float,
    exposure_cap: float,
) -> dict[str, int | float]:
    buy_notional = sum(float(trade.get("entry_value") or 0.0) for trade in trades)
    sell_notional = sum(float(trade.get("exit_value") or 0.0) for trade in trades)
    exposures = [float(row.get("exposure") or 0.0) for row in equity_curve]
    positions = [int(row.get("positions") or 0) for row in equity_curve]
    active = [value for value in exposures if value > 0]
    years = max(len(equity_curve) / 252.0, 1 / 252.0)
    gross_turnover = (
        (buy_notional + sell_notional) / initial_capital if initial_capital > 0 else 0.0
    )
    avg_exposure = float(np.mean(exposures)) if exposures else 0.0
    return {
        "round_trip_trades": len(trades),
        "transactions": len(trades) * 2,
        "buy_notional": round(buy_notional, 2),
        "sell_notional": round(sell_notional, 2),
        "traded_notional": round(buy_notional + sell_notional, 2),
        "capital_turnover": round(gross_turnover, 4),
        "annualized_turnover": round(gross_turnover / years, 4),
        "active_days": len(active),
        "active_day_ratio": round(len(active) / len(exposures), 4) if exposures else 0.0,
        "avg_positions": round(float(np.mean(positions)), 4) if positions else 0.0,
        "avg_exposure": round(avg_exposure, 4),
        "avg_active_exposure": round(float(np.mean(active)), 4) if active else 0.0,
        "max_exposure": round(max(exposures), 4) if exposures else 0.0,
        "exposure_limit_utilization": round(avg_exposure / exposure_cap, 4)
        if exposure_cap > 0
        else 0.0,
        "idle_capital_ratio": round(1.0 - avg_exposure, 4),
    }
