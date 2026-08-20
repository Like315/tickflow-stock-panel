from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.backtest.matrix import build_market_data_matrix
from app.strategy.builtin.volume_dry_breakout import MATRIX_STRATEGY


def _panel() -> pl.DataFrame:
    start = date(2024, 1, 1)
    rows: list[dict] = []
    for symbol in ("A", "B", "C", "D"):
        for offset in range(23):
            current_date = start + timedelta(days=offset)
            row = {
                "symbol": symbol,
                "name": symbol,
                "date": current_date,
                "open": 9.95,
                "high": 10.10,
                "low": 9.90,
                "close": 10.00,
                "volume": 100.0,
                "amount": 100_000.0,
            }
            if offset == 20:
                row.update(
                    open=10.00,
                    high=10.50,
                    low=9.50,
                    close=10.10,
                    volume=300.0,
                )
                if symbol == "B":
                    row.update(open=9.55, close=10.40)
                elif symbol == "D":
                    row.update(open=10.00, high=10.60, low=9.80, close=9.90)
            elif offset == 21:
                row.update(
                    open=10.20,
                    high=10.80,
                    low=10.10,
                    close=10.70 if symbol == "D" else 10.60,
                    volume=200.0 if symbol != "C" else 250.0,
                )
            elif offset == 22:
                row.update(
                    open=10.70,
                    high=10.75,
                    low=9.90,
                    close=10.00,
                    volume=500.0,
                )
            rows.append(row)
    return pl.DataFrame(rows).sort(["date", "symbol"])


def test_entry_requires_high_volume_small_body_then_lower_volume_breakout():
    panel = _panel()
    market = build_market_data_matrix(panel)
    signals = MATRIX_STRATEGY.compute_signals(market, {})
    target_time = market.timestamp_labels.index("2024-01-22")

    assert signals.entry[target_time].tolist() == [1, 0, 0, 0]
    assert signals.entry_signal_code[target_time].tolist() == [0, -1, -1, -1]


def test_entry_does_not_depend_on_future_bars():
    panel = _panel()
    target_date = date(2024, 1, 22)
    partial_market = build_market_data_matrix(panel.filter(pl.col("date") <= target_date))
    full_market = build_market_data_matrix(panel)

    partial = MATRIX_STRATEGY.compute_signals(partial_market, {})
    full = MATRIX_STRATEGY.compute_signals(full_market, {})
    full_target = full_market.timestamp_labels.index(str(target_date))
    asset_id = full_market.symbols.index("A")

    assert partial.entry[-1, asset_id] == 1
    assert full.entry[full_target, asset_id] == 1


def test_extension_filter_rejects_breakout_too_far_above_ma20():
    target_date = date(2024, 1, 22)
    panel = _panel().with_columns(
        pl.when((pl.col("symbol") == "A") & (pl.col("date") == target_date))
        .then(pl.lit(12.20))
        .otherwise(pl.col("high"))
        .alias("high"),
        pl.when((pl.col("symbol") == "A") & (pl.col("date") == target_date))
        .then(pl.lit(12.00))
        .otherwise(pl.col("close"))
        .alias("close"),
    )
    market = build_market_data_matrix(panel)
    target_time = market.timestamp_labels.index(str(target_date))
    asset_id = market.symbols.index("A")

    default_signals = MATRIX_STRATEGY.compute_signals(market, {})
    unbounded_signals = MATRIX_STRATEGY.compute_signals(
        market,
        {"use_extension_filter": False},
    )

    assert default_signals.entry[target_time, asset_id] == 0
    assert unbounded_signals.entry[target_time, asset_id] == 1


def test_breakout_quality_guard_rejects_extended_shallow_breakout():
    market = build_market_data_matrix(_panel())
    target_time = market.timestamp_labels.index("2024-01-22")
    asset_id = market.symbols.index("A")

    guarded = MATRIX_STRATEGY.compute_signals(
        market,
        {
            "use_breakout_quality_guard": True,
            "breakout_guard_ma20_bias_min": 0.05,
            "breakout_guard_margin_max": 0.01,
        },
    )
    narrower_shallow_band = MATRIX_STRATEGY.compute_signals(
        market,
        {
            "use_breakout_quality_guard": True,
            "breakout_guard_ma20_bias_min": 0.05,
            "breakout_guard_margin_max": 0.005,
        },
    )

    assert guarded.entry[target_time, asset_id] == 0
    assert narrower_shallow_band.entry[target_time, asset_id] == 1


def test_high_volume_bearish_candle_exits():
    market = build_market_data_matrix(_panel())
    signals = MATRIX_STRATEGY.compute_signals(market, {})
    target_time = market.timestamp_labels.index("2024-01-23")

    assert signals.exit[target_time].tolist() == [1, 1, 1, 1]
    assert signals.exit_signal_code[target_time].tolist() == [0, 0, 0, 0]


def test_ma20_breakdown_exit_is_distinct_from_volume_exit():
    target_date = date(2024, 1, 23)
    panel = _panel().with_columns(
        pl.when(pl.col("date") == target_date)
        .then(pl.lit(9.10))
        .otherwise(pl.col("open"))
        .alias("open"),
        pl.when(pl.col("date") == target_date)
        .then(pl.lit(9.20))
        .otherwise(pl.col("high"))
        .alias("high"),
        pl.when(pl.col("date") == target_date)
        .then(pl.lit(8.90))
        .otherwise(pl.col("low"))
        .alias("low"),
        pl.when(pl.col("date") == target_date)
        .then(pl.lit(9.00))
        .otherwise(pl.col("close"))
        .alias("close"),
        pl.when(pl.col("date") == target_date)
        .then(pl.lit(100.0))
        .otherwise(pl.col("volume"))
        .alias("volume"),
    )
    market = build_market_data_matrix(panel)
    signals = MATRIX_STRATEGY.compute_signals(market, {})
    target_time = market.timestamp_labels.index(str(target_date))

    assert signals.exit[target_time].tolist() == [1, 1, 1, 1]
    assert signals.exit_signal_code[target_time].tolist() == [1, 1, 1, 1]
