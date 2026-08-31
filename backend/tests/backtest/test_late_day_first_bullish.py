from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl

from app.backtest.engine import BacktestEngine
from app.backtest.matrix import build_market_data_matrix, make_signal_matrix
from app.backtest.strategy import (
    BacktestResultPolicy,
    StrategyBacktestConfig,
    StrategyBacktestService,
)
from app.strategy.builtin._late_day_first_bullish import (
    completed_ten_minute_closes,
    evaluate_late_entry,
    replay_intraday_strategy,
)
from app.strategy.engine import StrategyEngine


def _session_minutes(trade_date: date) -> list[datetime]:
    morning = datetime.combine(trade_date, time(9, 31))
    afternoon = datetime.combine(trade_date, time(13, 1))
    return [morning + timedelta(minutes=index) for index in range(120)] + [
        afternoon + timedelta(minutes=index) for index in range(120)
    ]


def _entry_day_minutes(trade_date: date) -> pl.DataFrame:
    chunk_closes = [10.0] * 14 + [10.0, 10.0, 10.0, 10.0, 9.8, 9.8, 10.4, 10.4, 10.4, 10.4]
    rows = []
    for index, timestamp in enumerate(_session_minutes(trade_date)):
        session_index = index if index < 120 else index - 120
        chunk_id = index // 10 if index < 120 else 12 + session_index // 10
        close = chunk_closes[chunk_id]
        open_price = 9.9 if index == 0 else close
        if timestamp.time() == time(14, 31):
            open_price = 10.41
        rows.append(
            {
                "symbol": "A",
                "datetime": timestamp,
                "open": open_price,
                "high": max(open_price, close) + 0.01,
                "low": min(open_price, close) - 0.01,
                "close": close,
                "volume": 100.0,
                "amount": close * 10_000,
            }
        )
    return pl.DataFrame(rows)


def _exit_day_minutes(trade_date: date) -> pl.DataFrame:
    rows = []
    for index in range(120):
        timestamp = datetime.combine(trade_date, time(9, 31)) + timedelta(minutes=index)
        close = 10.8 if index == 0 else 10.79
        rows.append(
            {
                "symbol": "A",
                "datetime": timestamp,
                "open": 10.79 if index == 1 else close,
                "high": close + 0.02,
                "low": close - 0.02,
                "close": close,
                "volume": 100.0,
                "amount": close * 10_000,
            }
        )
    return pl.DataFrame(rows)


def _daily_market():
    dates = [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]
    panel = pl.DataFrame(
        {
            "symbol": ["A"] * 3,
            "date": dates,
            "open": [10.1, 9.9, 10.8],
            "high": [10.2, 10.5, 10.9],
            "low": [9.9, 9.8, 10.7],
            "close": [10.0, 10.4, 10.79],
            "volume": [100_000.0] * 3,
        }
    )
    panel = panel.with_columns(
        pl.lit(0.02).alias("change_pct"),
        pl.lit(0.05).alias("momentum_20d"),
    )
    return build_market_data_matrix(panel, field_columns={"change_pct", "momentum_20d"})


def test_late_entry_requires_a_new_ma5_upturn() -> None:
    matched = evaluate_late_entry(
        ten_minute_closes=(10.0, 10.0, 10.0, 10.0, 9.8, 9.8, 10.4),
        day_open=9.9,
        current_close=10.4,
        previous_close=10.0,
        change_rank=1,
        params={},
    )
    still_falling = evaluate_late_entry(
        ten_minute_closes=(10.4, 10.3, 10.2, 10.1, 10.0, 9.9, 9.8),
        day_open=9.9,
        current_close=10.1,
        previous_close=10.0,
        change_rank=1,
        params={},
    )

    assert matched.matched
    assert matched.reason == "late_first_bullish_ma5_turn"
    assert not still_falling.matched
    assert still_falling.reason == "ma5_not_turning_up"


def test_intraday_replay_uses_next_minute_prices_for_both_sides() -> None:
    market = _daily_market()
    setup = np.zeros(market.shape, dtype=np.uint8)
    setup[0, 0] = 1
    setup_signals = make_signal_matrix(market.shape, entry=setup)
    minutes = pl.concat(
        [_entry_day_minutes(date(2026, 8, 18)), _exit_day_minutes(date(2026, 8, 19))]
    )

    result = replay_intraday_strategy(market, setup_signals, minutes, {})

    assert result.signals.entry[1, 0] == 1
    assert result.signals.exit[2, 0] == 1
    assert result.entry_price_override[1, 0] == np.float32(10.41)
    assert result.exit_price_override[2, 0] == np.float32(10.79)
    assert result.stats["confirmed_entries"] == 1
    entry_rows = _entry_day_minutes(date(2026, 8, 18)).to_dicts()
    closes = completed_ten_minute_closes(entry_rows, datetime(2026, 8, 18, 14, 30))
    assert len(closes) == 21


def test_intraday_replay_fails_closed_when_next_morning_minutes_are_missing() -> None:
    market = _daily_market()
    setup = np.zeros(market.shape, dtype=np.uint8)
    setup[0, 0] = 1

    result = replay_intraday_strategy(
        market,
        make_signal_matrix(market.shape, entry=setup),
        _entry_day_minutes(date(2026, 8, 18)),
        {},
    )

    assert not result.signals.entry.any()
    assert not result.signals.exit.any()


def test_strategy_backtest_service_runs_intraday_replay_without_daily_fallback() -> None:
    market = _daily_market()
    minutes = pl.concat(
        [_entry_day_minutes(date(2026, 8, 18)), _exit_day_minutes(date(2026, 8, 19))]
    )

    class _Repo:
        store = SimpleNamespace(data_dir=None)

        @staticmethod
        def get_minute_by_dates(symbols, dates, *, asset_type):
            assert symbols == ["A"]
            assert asset_type == "stock"
            return minutes.filter(pl.col("datetime").dt.date().is_in(dates))

    strategy_engine = StrategyEngine([])
    strategy = StrategyEngine._load_file(
        Path("app/strategy/builtin/late_day_first_bullish_ma5_turn.py")
    )
    strategy_engine._strategies = {str(strategy.meta["id"]): strategy}
    engine = BacktestEngine(_Repo())
    engine.load_market_data_matrix_for_backtest = lambda *args, **kwargs: market
    service = StrategyBacktestService(engine, strategy_engine)

    result = service.run(
        StrategyBacktestConfig(
            strategy_id="late_day_first_bullish_ma5_turn",
            symbols=["A"],
            start=date(2026, 8, 17),
            end=date(2026, 8, 17),
            overrides={"basic_filter": {"enabled": False}, "max_hold_days": 2},
            entry_fill="open_t+1",
            exit_fill="open_t+1",
            mode="full",
            min_hold_days=1,
            holding_days=2,
            fees_pct=0,
            slippage_bps=0,
        ),
        result_policy=BacktestResultPolicy(include_benchmark=False),
    )

    assert result.error is None
    assert len(result.trades) == 1
    assert result.trades[0]["entry_price"] == 10.41
    assert result.trades[0]["exit_price"] == 10.79
    assert result.stats["intraday_replay"]["confirmed_entries"] == 1
