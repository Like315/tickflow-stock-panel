"""尾盘首阳策略的实时判断与历史分钟回放。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

import numpy as np
import polars as pl

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix

STRATEGY_ID = "late_day_first_bullish_ma5_turn"
ENTRY_SIGNAL_ID = "signal_late_day_first_bullish_ma5_turn"
EXIT_SIGNAL_ID = "signal_next_morning_managed_exit"


@dataclass(frozen=True, slots=True)
class LateEntryEvaluation:
    """一只候选股票在一个尾盘时点的入场判断。"""

    matched: bool
    reason: str
    change_pct: float | None
    ma5_slope_pct: float | None


@dataclass(frozen=True, slots=True)
class IntradayReplayResult:
    """分钟规则回放后交给日线撮合器的信号与成交价。"""

    signals: SignalMatrix
    entry_price_override: np.ndarray
    exit_price_override: np.ndarray
    stats: dict[str, int | float | str]


@dataclass(frozen=True, slots=True)
class _EntrySnapshot:
    """一个只依赖已完成分钟线的尾盘候选快照。"""

    setup_time_id: int
    target_time_id: int
    asset_id: int
    bar_index: int
    timestamp: datetime
    ten_minute_closes: tuple[float, ...]
    day_open: float
    current_close: float
    previous_close: float
    next_open: float


def _number(value: Any) -> float | None:
    """把有限正数价格转换为浮点数。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) and result > 0 else None


def _row_price(row: dict[str, Any], field: str) -> float | None:
    """优先读取不复权价格，缺失时使用标准分钟价格。"""
    return _number(row.get(f"raw_{field}")) or _number(row.get(field))


def _session_rows(rows: list[dict[str, Any]], afternoon: bool) -> list[dict[str, Any]]:
    """返回同一交易时段内按时间排序的有效分钟线。"""
    start, end = (time(13, 1), time(15, 0)) if afternoon else (time(9, 31), time(11, 30))
    return [
        row
        for row in rows
        if isinstance(row.get("datetime"), datetime)
        and start <= row["datetime"].time().replace(tzinfo=None) <= end
        and _row_price(row, "close") is not None
    ]


def completed_ten_minute_closes(
    rows: list[dict[str, Any]],
    through: datetime,
) -> tuple[float, ...]:
    """按交易时段聚合截至指定时点已完成的10分钟收盘价。"""
    closes: list[float] = []
    for afternoon in (False, True):
        session = [row for row in _session_rows(rows, afternoon) if row["datetime"] <= through]
        if not session:
            continue
        session_start = session[0]["datetime"].replace(
            hour=13 if afternoon else 9,
            minute=1 if afternoon else 31,
            second=0,
            microsecond=0,
        )
        buckets: dict[int, list[dict[str, Any]]] = {}
        for row in session:
            offset = int((row["datetime"] - session_start).total_seconds() // 60)
            if offset >= 0:
                buckets.setdefault(offset // 10, []).append(row)
        for bucket_id in sorted(buckets):
            chunk = buckets[bucket_id]
            expected_start = session_start + timedelta(minutes=bucket_id * 10)
            expected_end = expected_start + timedelta(minutes=9)
            close = _row_price(chunk[-1], "close")
            if (
                len(chunk) == 10
                and chunk[0]["datetime"] == expected_start
                and chunk[-1]["datetime"] == expected_end
                and close is not None
            ):
                closes.append(close)
    return tuple(closes)


def evaluate_late_entry(
    *,
    ten_minute_closes: tuple[float, ...],
    day_open: float,
    current_close: float,
    previous_close: float,
    change_rank: int | None,
    params: dict[str, Any],
) -> LateEntryEvaluation:
    """判断首阳、涨幅排名和10分钟 MA5 是否同时成立。"""
    change_pct = current_close / previous_close - 1 if previous_close > 0 else None
    if change_rank is None or change_rank > int(params.get("gainer_rank_limit", 20)):
        return LateEntryEvaluation(False, "outside_gainer_rank", change_pct, None)
    if current_close <= day_open or change_pct is None:
        return LateEntryEvaluation(False, "not_first_bullish_day", change_pct, None)
    if change_pct < float(params.get("minimum_change_pct", 0.01)):
        return LateEntryEvaluation(False, "change_below_threshold", change_pct, None)
    if len(ten_minute_closes) < 7:
        return LateEntryEvaluation(False, "insufficient_ten_minute_bars", change_pct, None)
    values = np.asarray(ten_minute_closes[-7:], dtype=np.float64)
    previous_previous_ma5 = float(values[:5].mean())
    previous_ma5 = float(values[1:6].mean())
    current_ma5 = float(values[2:7].mean())
    slope_pct = current_ma5 / previous_ma5 - 1 if previous_ma5 > 0 else None
    minimum_slope = float(params.get("ma5_min_slope_pct", 0.0))
    if slope_pct is None or current_ma5 <= previous_ma5 or previous_ma5 > previous_previous_ma5:
        return LateEntryEvaluation(False, "ma5_not_turning_up", change_pct, slope_pct)
    if slope_pct < minimum_slope or current_close < current_ma5:
        return LateEntryEvaluation(False, "ma5_confirmation_weak", change_pct, slope_pct)
    return LateEntryEvaluation(True, "late_first_bullish_ma5_turn", change_pct, slope_pct)


def _minute_groups(frame: pl.DataFrame) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """把分钟数据按股票和交易日分组。"""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if frame.is_empty() or not {"symbol", "datetime"}.issubset(frame.columns):
        return groups
    for row in frame.sort(["symbol", "datetime"]).iter_rows(named=True):
        value = row.get("datetime")
        if isinstance(value, datetime):
            groups.setdefault((str(row["symbol"]), value.date().isoformat()), []).append(row)
    return groups


def _entry_snapshots(
    market: MarketDataMatrix,
    signals: SignalMatrix,
    groups: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[_EntrySnapshot]:
    """为每个日线准备信号生成可用的尾盘分钟快照。"""
    snapshots: list[_EntrySnapshot] = []
    for setup_raw, asset_raw in np.argwhere(signals.entry != 0):
        setup_id, asset_id = int(setup_raw), int(asset_raw)
        target_id = setup_id + 1
        if target_id >= market.shape[0]:
            continue
        symbol = market.symbols[asset_id]
        rows = groups.get((symbol, market.timestamp_labels[target_id][:10]), [])
        previous_close = float(market.close[setup_id, asset_id])
        day_open = _row_price(rows[0], "open") if rows else None
        if day_open is None or not np.isfinite(previous_close) or previous_close <= 0:
            continue
        snapshots.extend(
            _snapshots_for_rows(rows, setup_id, target_id, asset_id, day_open, previous_close)
        )
    return snapshots


def _snapshots_for_rows(
    rows: list[dict[str, Any]],
    setup_id: int,
    target_id: int,
    asset_id: int,
    day_open: float,
    previous_close: float,
) -> list[_EntrySnapshot]:
    """生成14:30至14:50三个可执行检查点。"""
    result: list[_EntrySnapshot] = []
    for index, row in enumerate(rows[:-1]):
        timestamp = row.get("datetime")
        if not isinstance(timestamp, datetime) or timestamp.time().replace(tzinfo=None) not in {
            time(14, 30),
            time(14, 40),
            time(14, 50),
        }:
            continue
        current_close = _row_price(row, "close")
        next_open = _row_price(rows[index + 1], "open")
        if current_close is None or next_open is None:
            continue
        result.append(
            _EntrySnapshot(
                setup_id,
                target_id,
                asset_id,
                index,
                timestamp,
                completed_ten_minute_closes(rows, timestamp),
                day_open,
                current_close,
                previous_close,
                next_open,
            )
        )
    return result


def _ranked_snapshots(snapshots: list[_EntrySnapshot]) -> list[tuple[_EntrySnapshot, int]]:
    """在同一检查时点按相对前收涨幅生成候选池排名。"""
    grouped: dict[tuple[int, datetime], list[_EntrySnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault((snapshot.target_time_id, snapshot.timestamp), []).append(snapshot)
    ranked: list[tuple[_EntrySnapshot, int]] = []
    for rows in grouped.values():
        ordered = sorted(
            rows,
            key=lambda item: (-(item.current_close / item.previous_close - 1), item.asset_id),
        )
        ranked.extend((item, rank) for rank, item in enumerate(ordered, start=1))
    return sorted(ranked, key=lambda item: (item[0].timestamp, item[0].asset_id))


def _morning_exit_fill(
    rows: list[dict[str, Any]],
    entry_price: float,
    params: dict[str, Any],
) -> tuple[float | None, str | None]:
    """用已完成分钟线决定次日早盘卖出，并返回下一分钟开盘价。"""
    peak = entry_price
    stop_loss = float(params.get("morning_stop_loss_pct", -0.03))
    take_profit = float(params.get("morning_take_profit_pct", 0.03))
    activation = float(params.get("morning_trailing_activate_pct", 0.015))
    drawdown = float(params.get("morning_trailing_drawdown_pct", 0.008))
    for index, row in enumerate(rows[:-1]):
        timestamp = row.get("datetime")
        if not isinstance(timestamp, datetime):
            continue
        clock = timestamp.time().replace(tzinfo=None)
        if not time(9, 31) <= clock <= time(11, 25):
            continue
        close = _row_price(row, "close")
        high = _row_price(row, "high")
        next_open = _row_price(rows[index + 1], "open")
        if close is None or high is None or next_open is None:
            continue
        peak = max(peak, high)
        if close / entry_price - 1 <= stop_loss:
            return next_open, "next_morning_stop_loss"
        if close / entry_price - 1 >= take_profit:
            return next_open, "next_morning_take_profit"
        if peak / entry_price - 1 >= activation and close / peak - 1 <= -abs(drawdown):
            return next_open, "next_morning_peak_drawdown"
        if clock >= time(11, 25):
            return next_open, "next_morning_timeout"
    return None, None


def replay_intraday_strategy(
    market: MarketDataMatrix,
    setup_signals: SignalMatrix,
    minute_frame: pl.DataFrame,
    params: dict[str, Any],
) -> IntradayReplayResult:
    """把日线准备信号回放为尾盘买入和次日早盘卖出。"""
    shape = market.shape
    entry = np.zeros(shape, dtype=np.uint8)
    exit_ = np.zeros(shape, dtype=np.uint8)
    score = np.zeros(shape, dtype=np.float32)
    entry_price = np.full(shape, np.nan, dtype=np.float32)
    exit_price = np.full(shape, np.nan, dtype=np.float32)
    groups = _minute_groups(minute_frame)
    snapshots = _entry_snapshots(market, setup_signals, groups)
    confirmed_assets: set[tuple[int, int]] = set()
    exit_reasons: dict[str, int] = {}
    for snapshot, rank in _ranked_snapshots(snapshots):
        key = (snapshot.target_time_id, snapshot.asset_id)
        if key in confirmed_assets:
            continue
        evaluation = evaluate_late_entry(
            ten_minute_closes=snapshot.ten_minute_closes,
            day_open=snapshot.day_open,
            current_close=snapshot.current_close,
            previous_close=snapshot.previous_close,
            change_rank=rank,
            params=params,
        )
        if not evaluation.matched:
            continue
        exit_id = snapshot.target_time_id + 1
        if exit_id >= shape[0]:
            continue
        symbol = market.symbols[snapshot.asset_id]
        exit_rows = groups.get((symbol, market.timestamp_labels[exit_id][:10]), [])
        fill, reason = _morning_exit_fill(exit_rows, snapshot.next_open, params)
        if fill is None or reason is None:
            continue
        entry[key] = 1
        exit_[exit_id, snapshot.asset_id] = 1
        entry_price[key] = np.float32(snapshot.next_open)
        exit_price[exit_id, snapshot.asset_id] = np.float32(fill)
        score[key] = setup_signals.score[snapshot.setup_time_id, snapshot.asset_id]
        confirmed_assets.add(key)
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    signals = make_signal_matrix(
        shape,
        entry=entry,
        exit=exit_,
        score=score,
        entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
        exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
        entry_signal_ids=(ENTRY_SIGNAL_ID,),
        exit_signal_ids=(EXIT_SIGNAL_ID,),
    )
    stats: dict[str, int | float | str] = {
        "setup_candidates": int(setup_signals.entry.sum()),
        "minute_snapshots": len(snapshots),
        "confirmed_entries": int(entry.sum()),
        "complete_round_trips": int(exit_.sum()),
        "ranking_scope": "point_in_time_setup_pool",
    }
    stats.update({f"exit_{key}": value for key, value in exit_reasons.items()})
    return IntradayReplayResult(signals, entry_price, exit_price, stats)
