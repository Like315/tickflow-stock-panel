from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl

from app.backtest.matrix import build_market_data_matrix, make_signal_matrix
from app.strategy.research.capital_utilization_versions import (
    INTRADAY_CANDIDATE_STRATEGY,
    VERSIONS,
    confirm_intraday_entries,
    summarize_activity,
)


def _market():
    rows: list[dict] = []
    start = date(2024, 1, 1)
    for offset in range(25):
        row = {
            "symbol": "A",
            "name": "A",
            "date": start + timedelta(days=offset),
            "open": 10.0,
            "high": 10.1,
            "low": 9.9,
            "close": 10.0,
            "volume": 100.0,
            "amount": 100_000.0,
        }
        if offset == 20:
            row.update(open=10.0, high=10.5, low=9.5, close=10.1, volume=300.0)
        elif offset == 21:
            # The intraday candidate must not use this completed daily candle.
            row.update(open=9.0, high=9.1, low=8.0, close=8.5, volume=900.0)
        rows.append(row)
    return build_market_data_matrix(pl.DataFrame(rows))


def test_intraday_candidate_uses_only_previous_daily_setup():
    market = _market()
    signals = INTRADAY_CANDIDATE_STRATEGY.compute_signals(
        market,
        {
            "setup_vol_ratio_min": 2.0,
            "max_body_to_range": 0.25,
            "min_lower_wick_to_range": 0.45,
        },
    )

    assert signals.entry[21, 0] == 1


def test_1430_confirmation_uses_next_minute_open_and_fails_closed():
    market = _market()
    entry = np.zeros(market.shape, dtype=np.uint8)
    entry[21, 0] = 1
    signals = make_signal_matrix(market.shape, entry=entry)
    target_date = date(2024, 1, 1) + timedelta(days=21)
    minute = pl.DataFrame({
        "symbol": ["A", "A", "A"],
        "datetime": [
            datetime.combine(target_date, datetime.strptime("14:29", "%H:%M").time()),
            datetime.combine(target_date, datetime.strptime("14:30", "%H:%M").time()),
            datetime.combine(target_date, datetime.strptime("14:31", "%H:%M").time()),
        ],
        "open": [10.2, 10.5, 10.72],
        "close": [10.4, 10.7, 10.75],
        "volume": [80.0, 70.0, 10.0],
    })

    confirmed, fill, stats = confirm_intraday_entries(market, signals, minute)
    missing, missing_fill, missing_stats = confirm_intraday_entries(
        market,
        signals,
        pl.DataFrame(),
    )

    assert confirmed[21, 0] == 1
    assert fill[21, 0] == np.float32(10.72)
    assert stats["minute_coverage"] == 1.0
    assert not missing.any()
    assert np.isnan(missing_fill).all()
    assert missing_stats["missing_minute_symbol_days"] == 1


def test_research_versions_have_distinct_timing_and_risk_sleeves():
    assert [version.id for version in VERSIONS] == [
        "c2_daily_expanded",
        "cm_intraday_1430",
        "l_first_trend_entry",
    ]
    assert VERSIONS[0].params["setup_vol_ratio_min"] == 2.0
    assert VERSIONS[0].max_positions == 2
    assert VERSIONS[1].overrides["sector_context_filter"]["lag_bars"] == 1
    assert VERSIONS[2].params["entry_once_per_trend"] is True
    assert VERSIONS[2].max_exposure_pct == 0.20


def test_activity_summary_reports_transaction_notional_and_utilization():
    summary = summarize_activity(
        [{"entry_value": 100.0, "exit_value": 110.0}],
        [
            {"exposure": 0.0, "positions": 0},
            {"exposure": 0.2, "positions": 1},
        ],
        initial_capital=1_000.0,
        exposure_cap=0.5,
    )

    assert summary["round_trip_trades"] == 1
    assert summary["transactions"] == 2
    assert summary["traded_notional"] == 210.0
    assert summary["avg_exposure"] == 0.1
    assert summary["exposure_limit_utilization"] == 0.2
