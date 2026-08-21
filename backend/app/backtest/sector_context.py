"""Point-in-time sector context masks for matrix backtests.

The context row is aligned to its signal row.  A zero-bar lag is valid when a
completed daily close produces a signal for next-session execution; positive
lags remain available for conservative controls.  Sector membership is
supplied by the caller so this layer does not depend on a particular provider.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from app.backtest.matrix import MarketDataMatrix, matrix_feature


@dataclass(frozen=True)
class SectorContextFilter:
    entry_mask: np.ndarray
    score: np.ndarray
    metadata: dict[str, int | float | str]


def _rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    out = np.full(source.shape, np.nan, dtype=np.float64)
    if window <= 0 or source.shape[0] < window:
        return out
    cumulative = np.vstack(
        [np.zeros((1, source.shape[1]), dtype=np.float64), np.cumsum(source, axis=0)]
    )
    out[window - 1 :] = cumulative[window:] - cumulative[:-window]
    return out


def _rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    out = np.full(source.shape, np.nan, dtype=np.float64)
    for row in range(window - 1, source.shape[0]):
        out[row] = np.nanmax(source[row - window + 1 : row + 1], axis=0)
    return out


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.full(np.broadcast_shapes(numerator.shape, denominator.shape), np.nan)
    np.divide(
        numerator,
        denominator,
        out=out,
        where=np.isfinite(denominator) & (denominator != 0),
    )
    return out


def _daily_top_mask(values: np.ndarray, top: int) -> np.ndarray:
    result = np.zeros(values.shape, dtype=bool)
    for row in range(values.shape[0]):
        eligible = np.flatnonzero(np.isfinite(values[row]))
        if eligible.size == 0:
            continue
        order = eligible[np.argsort(-values[row, eligible], kind="stable")]
        result[row, order[: min(top, order.size)]] = True
    return result


def _select_top(values: np.ndarray, eligible: np.ndarray, top: int) -> np.ndarray:
    result = np.zeros(eligible.shape, dtype=bool)
    for row in range(values.shape[0]):
        candidates = np.flatnonzero(eligible[row] & np.isfinite(values[row]))
        if candidates.size == 0:
            continue
        order = candidates[np.argsort(-values[row, candidates], kind="stable")]
        result[row, order[: min(top, order.size)]] = True
    return result


def _sector_percentile(
    values: np.ndarray,
    asset_sector_ids: np.ndarray,
    sector_count: int,
) -> np.ndarray:
    """Return daily within-sector percentile ranks in [0, 1]."""
    result = np.zeros(values.shape, dtype=np.float64)
    for sector_id in range(sector_count):
        members = np.flatnonzero(asset_sector_ids == sector_id)
        if members.size == 0:
            continue
        for row in range(values.shape[0]):
            valid = members[np.isfinite(values[row, members])]
            if valid.size == 0:
                continue
            order = valid[np.argsort(values[row, valid], kind="stable")]
            if order.size == 1:
                result[row, order] = 1.0
            else:
                result[row, order] = np.arange(order.size) / (order.size - 1)
    return result


def _industry_at_level(value: object, level: int) -> str:
    parts = [part.strip() for part in str(value or "").split("-") if part.strip()]
    if not parts:
        return ""
    return parts[min(level - 1, len(parts) - 1)]


def _asset_sector_ids(
    symbols: tuple[str, ...],
    mapping: pl.DataFrame,
    *,
    kind: str,
    level: int,
) -> tuple[np.ndarray, tuple[str, ...]]:
    required = {"_sym_up", kind}
    missing = required - set(mapping.columns)
    if missing:
        raise ValueError(f"sector mapping missing columns: {sorted(missing)}")

    normalized: dict[str, str] = {}
    rows = mapping.select("_sym_up", kind).drop_nulls().sort(["_sym_up", kind])
    for symbol, raw_sector in rows.iter_rows():
        sector = (
            _industry_at_level(raw_sector, level)
            if kind == "industry"
            else str(raw_sector).strip()
        )
        if sector:
            normalized.setdefault(str(symbol).upper(), sector)

    sector_names = tuple(sorted(set(normalized.values())))
    sector_index = {name: index for index, name in enumerate(sector_names)}
    asset_ids = np.full(len(symbols), -1, dtype=np.int32)
    for asset_id, symbol in enumerate(symbols):
        sector = normalized.get(symbol.upper())
        if sector is not None:
            asset_ids[asset_id] = sector_index[sector]
    return asset_ids, sector_names


def build_sector_context_filter(
    market: MarketDataMatrix,
    mapping: pl.DataFrame,
    config: dict,
) -> SectorContextFilter:
    """Build a time-by-asset entry mask from sector data known before entry.

    ``lag_bars=0`` uses windows ending on signal bar T.  This is point-in-time
    safe only when T is a completed close and execution occurs no earlier than
    T+1.  Positive values retain the older, more conservative alignment.
    """
    kind = str(config.get("kind", "industry"))
    if kind != "industry":
        raise ValueError("sector_context_filter currently supports industry only")
    level = int(config.get("level", 1))
    if level not in {1, 2, 3}:
        raise ValueError("sector_context_filter.level must be 1, 2 or 3")
    mode = str(config.get("mode", "intersection"))
    if mode not in {"trend", "mainline", "intersection", "union"}:
        raise ValueError(
            "sector_context_filter.mode must be trend, mainline, intersection or union"
        )
    apply_as = str(config.get("apply_as", "filter"))
    if apply_as not in {"filter", "score", "filter_score"}:
        raise ValueError(
            "sector_context_filter.apply_as must be filter, score or filter_score"
        )
    lag_bars = int(config.get("lag_bars", 0))
    if lag_bars < 0:
        raise ValueError("sector_context_filter.lag_bars must be non-negative")

    trend_window = int(config.get("trend_window", 10))
    mainline_window = int(config.get("mainline_window", 5))
    if trend_window < 3 or mainline_window < 2:
        raise ValueError("sector context windows are too short")

    asset_sector_ids, sector_names = _asset_sector_ids(
        market.symbols,
        mapping,
        kind=kind,
        level=level,
    )
    covered = asset_sector_ids >= 0
    coverage = float(covered.mean()) if covered.size else 0.0
    min_coverage = float(config.get("min_coverage", 0.5))
    if not sector_names or coverage < min_coverage:
        raise ValueError(
            f"sector mapping coverage {coverage:.1%} is below required {min_coverage:.1%}"
        )

    n_time = market.shape[0]
    n_sector = len(sector_names)
    change = matrix_feature(market, "change_pct")
    amount = market.field("amount")
    boards = matrix_feature(market, "consecutive_limit_ups")
    locked = market.limit_up_locked.astype(bool)

    sector_return = np.full((n_time, n_sector), np.nan, dtype=np.float64)
    sector_amount = np.zeros((n_time, n_sector), dtype=np.float64)
    sector_limits = np.zeros((n_time, n_sector), dtype=np.float64)
    sector_height = np.zeros((n_time, n_sector), dtype=np.float64)
    for sector_id in range(n_sector):
        members = np.flatnonzero(asset_sector_ids == sector_id)
        if members.size == 0:
            continue
        returns = change[:, members]
        valid_returns = np.isfinite(returns)
        return_count = valid_returns.sum(axis=1)
        return_sum = np.where(valid_returns, returns, 0.0).sum(axis=1)
        np.divide(
            return_sum,
            return_count,
            out=sector_return[:, sector_id],
            where=return_count > 0,
        )
        sector_amount[:, sector_id] = np.where(
            np.isfinite(amount[:, members]), amount[:, members], 0.0
        ).sum(axis=1)
        sector_limits[:, sector_id] = locked[:, members].sum(axis=1)
        member_boards = np.where(np.isfinite(boards[:, members]), boards[:, members], 0.0)
        sector_height[:, sector_id] = member_boards.max(axis=1)

    valid_return = np.isfinite(sector_return)
    rolling_valid = _rolling_sum(valid_return.astype(np.float64), trend_window)
    rolling_return = _rolling_sum(
        np.where(valid_return, sector_return, 0.0), trend_window
    )
    rolling_up = _rolling_sum((sector_return > 0).astype(np.float64), trend_window)
    up_ratio = _safe_ratio(rolling_up, rolling_valid)

    daily_top = _daily_top_mask(
        sector_return,
        max(1, int(config.get("trend_daily_top", 10))),
    )
    top_ratio = _safe_ratio(
        _rolling_sum(daily_top.astype(np.float64), trend_window), rolling_valid
    )
    amount_window = max(2, min(5, trend_window // 2))
    recent_amount = _rolling_sum(sector_amount, amount_window)
    previous_amount = np.full(recent_amount.shape, np.nan)
    previous_amount[amount_window:] = recent_amount[:-amount_window]
    amount_ratio = _safe_ratio(recent_amount, previous_amount)

    min_trend_return = float(config.get("min_trend_return", 0.02))
    min_trend_up_ratio = float(config.get("min_trend_up_ratio", 0.55))
    min_trend_top_ratio = float(config.get("min_trend_top_ratio", 0.30))
    min_valid_days = max(
        1,
        int(np.ceil(trend_window * float(config.get("min_trend_valid_ratio", 0.8)))),
    )
    trend_eligible = (
        (rolling_valid >= min_valid_days)
        & (rolling_return >= min_trend_return)
        & (up_ratio >= min_trend_up_ratio)
        & (top_ratio >= min_trend_top_ratio)
    )
    trend_score = (
        0.40 * np.clip((rolling_return - min_trend_return) / 0.10, 0.0, 1.0)
        + 0.25 * np.clip((up_ratio - 0.5) / 0.35, 0.0, 1.0)
        + 0.20 * np.clip(top_ratio, 0.0, 1.0)
        + 0.15 * np.clip((amount_ratio - 0.8) / 1.2, 0.0, 1.0)
    )
    trend_selected = _select_top(
        trend_score,
        trend_eligible,
        max(1, int(config.get("trend_top", 8))),
    )

    rolling_limits = _rolling_sum(sector_limits, mainline_window)
    rolling_active = _rolling_sum((sector_limits > 0).astype(np.float64), mainline_window)
    rolling_market_limits = _rolling_sum(
        locked.sum(axis=1, dtype=np.float64).reshape(-1, 1), mainline_window
    )
    limit_share = _safe_ratio(rolling_limits, rolling_market_limits)
    rolling_height = _rolling_max(sector_height, mainline_window)

    min_mainline_limits = float(config.get("min_mainline_limit_ups", 3))
    min_mainline_days = float(config.get("min_mainline_active_days", 2))
    min_mainline_share = float(config.get("min_mainline_limit_share", 0.02))
    min_mainline_height = float(config.get("min_mainline_height", 1))
    mainline_eligible = (
        (rolling_limits >= min_mainline_limits)
        & (rolling_active >= min_mainline_days)
        & (limit_share >= min_mainline_share)
        & (rolling_height >= min_mainline_height)
    )
    mainline_score = (
        0.35 * np.clip(rolling_limits / 12.0, 0.0, 1.0)
        + 0.25 * np.clip(rolling_active / mainline_window, 0.0, 1.0)
        + 0.25 * np.clip(limit_share / 0.12, 0.0, 1.0)
        + 0.15 * np.clip(rolling_height / 3.0, 0.0, 1.0)
    )
    mainline_selected = _select_top(
        mainline_score,
        mainline_eligible,
        max(1, int(config.get("mainline_top", 5))),
    )

    if mode == "trend":
        selected = trend_selected
    elif mode == "mainline":
        selected = mainline_selected
    elif mode == "union":
        selected = trend_selected | mainline_selected
    else:
        selected = trend_selected & mainline_selected

    lagged = np.zeros(selected.shape, dtype=bool)
    if lag_bars == 0:
        lagged[:] = selected
    elif lag_bars < n_time:
        lagged[lag_bars:] = selected[:-lag_bars]

    leader_window = max(5, int(config.get("leader_window", 20)))
    sector_momentum = _rolling_sum(
        np.where(valid_return, sector_return, 0.0), leader_window
    )
    stock_momentum = matrix_feature(market, f"momentum_{leader_window}d")
    sector_momentum_by_asset = np.full(market.shape, np.nan, dtype=np.float64)
    valid_assets = np.flatnonzero(covered)
    if valid_assets.size:
        sector_momentum_by_asset[:, valid_assets] = sector_momentum[
            :, asset_sector_ids[valid_assets]
        ]
    relative_strength = stock_momentum - sector_momentum_by_asset
    relative_rank = _sector_percentile(
        relative_strength,
        asset_sector_ids,
        n_sector,
    )
    amount_rank = _sector_percentile(
        np.where(np.isfinite(amount), amount, np.nan),
        asset_sector_ids,
        n_sector,
    )
    leader_score = 0.70 * relative_rank + 0.30 * amount_rank
    sector_score = np.zeros(market.shape, dtype=np.float64)
    if valid_assets.size:
        sector_score[:, valid_assets] = (
            0.30 * np.nan_to_num(trend_score[:, asset_sector_ids[valid_assets]])
            + 0.20 * np.nan_to_num(mainline_score[:, asset_sector_ids[valid_assets]])
        )
    context_score = np.clip(
        (sector_score + 0.30 * leader_score) / 0.80 * 100.0,
        0.0,
        100.0,
    )
    lagged_score = np.zeros(market.shape, dtype=np.float32)
    if lag_bars == 0:
        lagged_score[:] = context_score.astype(np.float32)
    elif lag_bars < n_time:
        lagged_score[lag_bars:] = context_score[:-lag_bars].astype(np.float32)

    valid_change = np.isfinite(change)
    market_valid = valid_change.sum(axis=1)
    market_up = (valid_change & (change > 0)).sum(axis=1)
    market_strong_down = (valid_change & (change <= -0.03)).sum(axis=1)
    market_average = _safe_ratio(
        np.where(valid_change, change, 0.0).sum(axis=1), market_valid
    )
    market_breadth = _safe_ratio(market_up, market_valid)
    market_resilience = 1.0 - np.clip(
        _safe_ratio(market_strong_down, market_valid) / 0.15,
        0.0,
        1.0,
    )
    ma20 = matrix_feature(market, "ma20")
    valid_ma20 = np.isfinite(ma20) & np.isfinite(market.close)
    above_ma20 = _safe_ratio(
        (valid_ma20 & (market.close > ma20)).sum(axis=1),
        valid_ma20.sum(axis=1),
    )
    market_score = 100.0 * (
        0.35 * np.clip((market_breadth - 0.30) / 0.40, 0.0, 1.0)
        + 0.20 * np.clip((market_average + 0.015) / 0.03, 0.0, 1.0)
        + 0.20 * np.clip((above_ma20 - 0.25) / 0.50, 0.0, 1.0)
        + 0.15
        * np.clip((locked.sum(axis=1, dtype=np.float64) - 20.0) / 80.0, 0.0, 1.0)
        + 0.10 * np.nan_to_num(market_resilience)
    )
    lagged_market_score = np.zeros(n_time, dtype=np.float32)
    if lag_bars == 0:
        lagged_market_score[:] = market_score.astype(np.float32)
    elif lag_bars < n_time:
        lagged_market_score[lag_bars:] = market_score[:-lag_bars].astype(np.float32)

    entry_mask = np.ones(market.shape, dtype=bool)
    if apply_as in {"filter", "filter_score"}:
        entry_mask.fill(False)
        if valid_assets.size:
            entry_mask[:, valid_assets] = lagged[:, asset_sector_ids[valid_assets]]
    market_min_score = config.get("market_min_score")
    if market_min_score is not None:
        market_pass = lagged_market_score >= float(market_min_score)
        consecutive_days = int(config.get("market_min_consecutive_days", 1))
        if consecutive_days < 1:
            raise ValueError(
                "sector_context_filter.market_min_consecutive_days must be positive"
            )
        persistent_market_pass = market_pass.copy()
        for offset in range(1, consecutive_days):
            previous_pass = np.zeros(n_time, dtype=bool)
            previous_pass[offset:] = market_pass[:-offset]
            persistent_market_pass &= previous_pass
        entry_mask &= persistent_market_pass[:, None]
    else:
        consecutive_days = 1
    entry_mask.flags.writeable = False
    lagged_score.flags.writeable = False

    active_rows = np.flatnonzero(lagged.any(axis=1))
    average_selected = (
        float(lagged[active_rows].sum(axis=1).mean()) if active_rows.size else 0.0
    )
    return SectorContextFilter(
        entry_mask=entry_mask,
        metadata={
            "kind": kind,
            "level": level,
            "mode": mode,
            "apply_as": apply_as,
            "lag_bars": lag_bars,
            "sector_count": n_sector,
            "covered_assets": int(covered.sum()),
            "asset_coverage": round(coverage, 6),
            "active_days": int(active_rows.size),
            "average_selected_sectors": round(average_selected, 3),
            "market_min_score": (
                float(market_min_score) if market_min_score is not None else -1.0
            ),
            "market_min_consecutive_days": consecutive_days,
        },
        score=lagged_score,
    )
