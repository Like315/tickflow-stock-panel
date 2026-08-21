from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pytest

from app.backtest.matrix import MarketDataMatrix
from app.strategy.builtin.concept_leader_weekly import (
    MATRIX_STRATEGY,
    _first_entry_per_trend,
    _monthly_averages,
    _weekly_averages,
)


def _market(closes: list[float]) -> MarketDataMatrix:
    close = np.asarray(closes, dtype=np.float32)[:, None]
    n = len(closes)
    timestamps = np.arange(
        np.datetime64("2026-01-05", "ms").astype(np.int64),
        np.datetime64("2026-01-05", "ms").astype(np.int64) + n * 7 * 86_400_000,
        7 * 86_400_000,
        dtype=np.int64,
    )
    ones = np.ones_like(close)
    return MarketDataMatrix(
        timestamps=timestamps,
        timestamp_labels=tuple(str(i) for i in range(n)),
        session_ids=np.arange(n, dtype=np.int32),
        symbols=("600001.SH",),
        names=("测试",),
        open=close.copy(),
        high=close.copy(),
        low=close.copy(),
        close=close,
        volume=ones,
        tradable=np.ones_like(close, dtype=bool),
        limit_up_locked=np.zeros_like(close, dtype=bool),
        limit_down_locked=np.zeros_like(close, dtype=bool),
        fields=MappingProxyType({"amount": ones * 2e8}),
    )


def test_weekly_average_uses_only_visible_weeks():
    market = _market([10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
    fast, slow = _weekly_averages(market, 4, 10)
    assert np.isnan(slow[8, 0])
    assert fast[9, 0] == pytest.approx((16 + 17 + 18 + 19) / 4)
    assert slow[9, 0] == pytest.approx(14.5)


def test_monthly_average_uses_only_visible_months():
    market = _market(list(range(10, 42)))
    fast, slow = _monthly_averages(market, 3, 6)
    assert np.isnan(slow[20, 0])
    assert np.isfinite(fast[-1, 0])
    assert np.isfinite(slow[-1, 0])


def test_rejects_invalid_weekly_windows():
    market = _market(list(range(10, 80)))
    with pytest.raises(ValueError, match="weekly_fast"):
        MATRIX_STRATEGY.compute_signals(market, {"weekly_fast": 10, "weekly_slow": 4})


def test_first_entry_per_trend_rearms_only_after_trend_reset():
    trend = np.array([[0], [1], [1], [1], [0], [1], [1]], dtype=bool)
    entry = np.array([[0], [1], [0], [1], [0], [0], [1]], dtype=bool)

    filtered = _first_entry_per_trend(entry, trend)

    assert filtered[:, 0].tolist() == [False, True, False, False, False, False, True]


def test_20d_high_mode_emits_one_entry_per_unbroken_trend():
    market = _market(list(range(10, 80)))

    signals = MATRIX_STRATEGY.compute_signals(
        market,
        {
            "entry_on_momentum_breakout": False,
            "entry_on_ma5_reclaim": False,
            "entry_on_20d_high_breakout": True,
            "entry_once_per_trend": True,
        },
    )

    assert int(signals.entry.sum()) == 1
    assert signals.entry_signal_code[signals.entry.astype(bool)].tolist() == [2]


def test_risk_contract_is_ten_percent():
    from app.strategy.builtin import concept_leader_weekly as strategy

    assert strategy.STOP_LOSS == -0.10
    assert strategy.TRAILING_STOP == -0.10
    assert strategy.ENTRY_SIGNALS == [
        "momentum_breakout",
        "ma5_reclaim",
        "high_20d_breakout",
    ]
    assert strategy.EXIT_SIGNALS == [
        "ma5_breakdown",
        "weekly_fast_breakdown",
        "monthly_fast_breakdown",
    ]
