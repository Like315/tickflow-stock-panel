from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.services.research_agent_screening import screen_dataframe


def _history(symbols: list[str], days: int = 60) -> pl.DataFrame:
    start = date(2026, 5, 1)
    return pl.DataFrame({
        "symbol": [symbol for symbol in symbols for _ in range(days)],
        "date": [start + timedelta(days=i) for _ in symbols for i in range(days)],
    })


def test_screen_filters_risk_suspension_and_short_history() -> None:
    latest = pl.DataFrame({
        "symbol": ["A", "B", "C", "D"],
        "name": ["稳健股份", "ST风险", "停牌股份", "历史不足"],
        "close": [10.0, 10.0, 10.0, 10.0],
        "raw_close": [10.0, 10.0, 10.0, 10.0],
        "volume": [1000.0, 1000.0, 0.0, 1000.0],
        "amount": [1e8, 1e8, 1e8, 1e8],
        "ma20": [9.5] * 4,
        "ma60": [9.0] * 4,
        "momentum_20d": [0.1] * 4,
        "annual_vol_20d": [0.2] * 4,
        "rsi_14": [55.0] * 4,
        "vol_ratio_5d": [1.2] * 4,
        "change_pct": [0.02] * 4,
    })
    history = pl.concat([_history(["A", "B", "C"]), _history(["D"], 20)])
    result = screen_dataframe(latest, history, as_of=date(2026, 8, 11))
    assert [row["symbol"] for row in result.candidates] == ["A"]
    assert result.excluded["risk_warning"] == 1
    assert result.excluded["suspended"] == 1
    assert result.excluded["insufficient_history"] == 1


def test_screen_is_stable_and_keeps_decimal_percentages() -> None:
    symbols = [f"S{i:02d}" for i in range(4)]
    latest = pl.DataFrame({
        "symbol": symbols,
        "name": ["股票"] * 4,
        "close": [10.0, 11.0, 12.0, 13.0],
        "raw_close": [10.0, 11.0, 12.0, 13.0],
        "volume": [1000.0] * 4,
        "amount": [1e8] * 4,
        "ma20": [9.0] * 4,
        "ma60": [8.0] * 4,
        "momentum_20d": [0.05, 0.10, 0.15, 0.20],
        "annual_vol_20d": [0.20, 0.21, 0.22, 0.23],
        "rsi_14": [50.0] * 4,
        "vol_ratio_5d": [1.0] * 4,
        "change_pct": [0.01, 0.02, 0.03, 0.04],
    })
    first = screen_dataframe(latest, _history(symbols), limit=4)
    second = screen_dataframe(latest.reverse(), _history(symbols), limit=4)
    assert [row["symbol"] for row in first.candidates] == [
        row["symbol"] for row in second.candidates
    ]
    selected = {row["symbol"]: row for row in first.candidates}
    assert selected["S03"]["change_pct"] == 0.04
