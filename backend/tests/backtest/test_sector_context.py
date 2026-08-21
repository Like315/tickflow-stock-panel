from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl

from app.backtest.matrix import (
    MatrixPipelineConfig,
    MatrixStrategyPipeline,
    build_market_data_matrix,
    make_signal_matrix,
)
from app.backtest.sector_context import build_sector_context_filter
from app.strategy.research.volume_dry_breakout_versions import VERSIONS
from scripts.bt_volume_dry_breakout_sector_context import (
    _config as research_backtest_config,
)
from scripts.bt_volume_dry_breakout_sector_context import (
    _trades_touching_range,
    _without_entry_range,
)


def _market(
    *,
    days: int = 8,
    shock_last: bool = False,
    market_crash_last: bool = False,
    market_crash_offsets: frozenset[int] = frozenset(),
    market_weak_offsets: frozenset[int] = frozenset(),
    split_leadership: bool = False,
):
    start = date(2024, 1, 1)
    closes = {
        "A1": 10.0,
        "A2": 12.0,
        "B1": 8.0,
        "B2": 9.0,
    }
    rows: list[dict] = []
    for offset in range(days):
        for symbol in closes:
            sector_a = symbol.startswith("A")
            previous_close = closes[symbol]
            if offset:
                if sector_a:
                    closes[symbol] *= 1.02
                elif not split_leadership:
                    closes[symbol] *= 0.995
            if offset in market_weak_offsets:
                closes[symbol] = previous_close * 0.999
            if shock_last and offset == days - 1:
                closes[symbol] *= 0.85 if sector_a else 1.15
            if (market_crash_last and offset == days - 1) or (
                offset in market_crash_offsets
            ):
                closes[symbol] *= 0.80
            is_limit_up = (
                split_leadership
                and not sector_a
                and offset in {days - 3, days - 2, days - 1}
            )
            rows.append({
                "symbol": symbol,
                "name": symbol,
                "date": start + timedelta(days=offset),
                "open": closes[symbol] * 0.99,
                "high": closes[symbol] * 1.01,
                "low": closes[symbol] * 0.98,
                "close": closes[symbol],
                "volume": 100.0,
                "amount": 1_000_000.0,
                "consecutive_limit_ups": 2 if is_limit_up else 0,
                "signal_limit_up": is_limit_up,
                "signal_limit_down": False,
            })
    return build_market_data_matrix(
        pl.DataFrame(rows).sort(["date", "symbol"]),
        field_columns={"amount", "consecutive_limit_ups"},
    )


def _mapping() -> pl.DataFrame:
    return pl.DataFrame({
        "_sym_up": ["A1", "A2", "B1", "B2"],
        "industry": ["行业A-细分", "行业A-细分", "行业B-细分", "行业B-细分"],
    })


def _config(mode: str = "trend") -> dict:
    return {
        "kind": "industry",
        "level": 1,
        "mode": mode,
        "lag_bars": 0,
        "trend_window": 3,
        "trend_daily_top": 1,
        "trend_top": 1,
        "min_trend_return": 0.01,
        "min_trend_up_ratio": 0.5,
        "min_trend_top_ratio": 0.0,
        "min_trend_valid_ratio": 1.0,
        "mainline_window": 2,
        "mainline_top": 1,
        "min_mainline_limit_ups": 2,
        "min_mainline_active_days": 2,
        "min_mainline_limit_share": 0.5,
        "min_mainline_height": 1,
        "min_coverage": 1.0,
    }


def test_sector_context_uses_signal_bars_completed_close_data():
    normal = _market(shock_last=False)
    shocked = _market(shock_last=True)

    normal_filter = build_sector_context_filter(normal, _mapping(), _config())
    shocked_filter = build_sector_context_filter(shocked, _mapping(), _config())

    assert normal_filter.entry_mask[-1].tolist() == [True, True, False, False]
    assert not shocked_filter.entry_mask[-1].any()
    assert shocked_filter.metadata["lag_bars"] == 0


def test_one_bar_lag_remains_available_as_a_conservative_control():
    normal = build_sector_context_filter(
        _market(),
        _mapping(),
        {**_config(), "lag_bars": 1},
    )
    shocked = build_sector_context_filter(
        _market(shock_last=True),
        _mapping(),
        {**_config(), "lag_bars": 1},
    )

    np.testing.assert_array_equal(shocked.entry_mask[-1], normal.entry_mask[-1])


def test_next_session_data_cannot_change_prior_signal_bar_context():
    normal = build_sector_context_filter(
        _market(days=9),
        _mapping(),
        _config(),
    )
    next_session_shocked = build_sector_context_filter(
        _market(days=9, shock_last=True),
        _mapping(),
        _config(),
    )

    np.testing.assert_array_equal(
        next_session_shocked.entry_mask[-2],
        normal.entry_mask[-2],
    )
    np.testing.assert_array_equal(
        next_session_shocked.score[-2],
        normal.score[-2],
    )


def test_intersection_requires_the_same_sector_to_be_trend_and_mainline():
    market = _market(split_leadership=True)

    intersection = build_sector_context_filter(
        market,
        _mapping(),
        _config("intersection"),
    )
    union = build_sector_context_filter(market, _mapping(), _config("union"))

    assert not intersection.entry_mask[-1].any()
    assert union.entry_mask[-1].tolist() == [True, True, True, True]


def test_pipeline_context_mask_filters_entries_but_not_exits():
    market = _market()
    context = build_sector_context_filter(market, _mapping(), _config()).entry_mask

    class AllSignals:
        def compute_signals(self, market, params):
            del params
            active = np.ones(market.shape, dtype=np.uint8)
            return make_signal_matrix(
                market.shape,
                entry=active,
                exit=active,
                entry_signal_code=np.zeros(market.shape, dtype=np.int16),
                exit_signal_code=np.zeros(market.shape, dtype=np.int16),
                entry_signal_ids=("entry",),
                exit_signal_ids=("exit",),
            )

    signals = MatrixStrategyPipeline().run(
        AllSignals(),
        market,
        {},
        MatrixPipelineConfig(
            basic_filter={},
            scoring={},
            order_by="score",
            descending=True,
            entry_context_mask=context,
        ),
    )

    assert signals.entry[-1].tolist() == [1, 1, 0, 0]
    assert signals.exit[-1].tolist() == [1, 1, 1, 1]


def test_soft_context_scores_leaders_without_deleting_entries():
    market = _market()
    context = build_sector_context_filter(
        market,
        _mapping(),
        {**_config(), "apply_as": "score"},
    )

    assert context.entry_mask.all()
    assert context.score[-1, 0] > context.score[-1, 2]

    class AllEntries:
        def compute_signals(self, market, params):
            del params
            active = np.ones(market.shape, dtype=np.uint8)
            return make_signal_matrix(
                market.shape,
                entry=active,
                entry_signal_code=np.zeros(market.shape, dtype=np.int16),
                entry_signal_ids=("entry",),
            )

    signals = MatrixStrategyPipeline().run(
        AllEntries(),
        market,
        {},
        MatrixPipelineConfig(
            basic_filter={},
            scoring={},
            order_by="score",
            descending=True,
            entry_context_mask=context.entry_mask,
            entry_context_score=context.score,
            entry_context_weight=1.0,
        ),
    )

    assert signals.entry[-1].all()
    np.testing.assert_array_equal(signals.score, context.score)


def test_market_stage_gate_uses_signal_bars_completed_close_data():
    config = {**_config(), "apply_as": "score", "market_min_score": 50.0}
    normal = build_sector_context_filter(_market(days=25), _mapping(), config)
    crashed = build_sector_context_filter(
        _market(days=25, market_crash_last=True),
        _mapping(),
        config,
    )

    assert normal.entry_mask[-1].all()
    assert not crashed.entry_mask[-1].any()


def test_one_bar_lagged_market_gate_ignores_signal_bar_crash():
    config = {
        **_config(),
        "apply_as": "score",
        "market_min_score": 50.0,
        "lag_bars": 1,
    }
    normal = build_sector_context_filter(_market(days=25), _mapping(), config)
    crashed = build_sector_context_filter(
        _market(days=25, market_crash_last=True),
        _mapping(),
        config,
    )

    np.testing.assert_array_equal(crashed.entry_mask[-1], normal.entry_mask[-1])


def test_market_gate_can_require_consecutive_strong_closes():
    market = _market(days=25, market_weak_offsets=frozenset({23}))
    single_day = build_sector_context_filter(
        market,
        _mapping(),
        {**_config(), "apply_as": "score", "market_min_score": 50.0},
    )
    persistent = build_sector_context_filter(
        market,
        _mapping(),
        {
            **_config(),
            "apply_as": "score",
            "market_min_score": 50.0,
            "market_min_consecutive_days": 2,
        },
    )

    assert single_day.entry_mask[-1].all()
    assert not persistent.entry_mask[-1].any()


def test_three_research_versions_have_distinct_risk_layers():
    assert [version.id for version in VERSIONS] == [
        "strict_price_volume",
        "sector_leader_score",
        "regime_risk_control",
    ]
    assert VERSIONS[0].overrides == {}
    assert VERSIONS[1].overrides["sector_context_filter"]["apply_as"] == "score"
    assert VERSIONS[1].overrides["sector_context_filter"]["lag_bars"] == 0
    assert VERSIONS[2].overrides["sector_context_filter"]["market_min_score"] == 50.0
    assert (
        VERSIONS[2].overrides["sector_context_filter"][
            "market_min_consecutive_days"
        ]
        == 2
    )
    assert VERSIONS[2].max_positions == 3
    assert VERSIONS[2].params["use_breakout_quality_guard"] is True
    assert VERSIONS[2].max_exposure_pct < VERSIONS[1].max_exposure_pct


def test_research_runner_uses_account_level_mode_and_preset_slots():
    config = research_backtest_config(
        date(2026, 1, 1),
        date(2026, 8, 17),
        VERSIONS[2],
        None,
        None,
    )
    overridden = research_backtest_config(
        date(2026, 1, 1),
        date(2026, 8, 17),
        VERSIONS[2],
        7,
        None,
    )

    assert config.mode == "position"
    assert config.max_positions == 3
    assert overridden.max_positions == 7


def test_sector_context_rejects_negative_lag():
    with np.testing.assert_raises_regex(ValueError, "non-negative"):
        build_sector_context_filter(
            _market(),
            _mapping(),
            {**_config(), "lag_bars": -1},
        )


def test_skip_entry_range_preserves_market_and_exit_masks():
    from app.backtest.matrix import MatrixComputeCache
    from app.backtest.strategy import PreparedMatrixBacktest

    market = _market(days=8)
    entry_time_mask = np.ones(market.shape[0], dtype=bool)
    exit_time_mask = np.ones(market.shape[0], dtype=bool)
    prepared = PreparedMatrixBacktest(
        signature=(),
        market_data=market,
        feature_width=0,
        load_start=date(2024, 1, 1),
        load_end=date(2024, 1, 8),
        sim_end=date(2024, 1, 8),
        entry_time_mask=entry_time_mask,
        exit_time_mask=exit_time_mask,
        start_id=0,
        stop_id=8,
        reference_price=None,
        entry_context_mask=None,
        entry_context_score=None,
        entry_context_weight=0.0,
        sector_context_metadata=None,
        prepare_timing_ms={},
        compute_cache=MatrixComputeCache(max_bytes=1024),
    )

    skipped, skipped_days = _without_entry_range(
        prepared,
        date(2024, 1, 3),
        date(2024, 1, 5),
    )

    assert skipped_days == 3
    assert skipped.entry_time_mask.tolist() == [
        True,
        True,
        False,
        False,
        False,
        True,
        True,
        True,
    ]
    assert skipped.exit_time_mask is exit_time_mask
    assert skipped.market_data is market


def test_trade_range_check_includes_positions_carried_into_skip_window():
    trades = [
        {
            "symbol": "BEFORE",
            "entry_date": "2026-06-01",
            "exit_date": "2026-06-10",
            "pnl_pct": 0.01,
        },
        {
            "symbol": "CARRY",
            "entry_date": "2026-06-30",
            "exit_date": "2026-07-02",
            "pnl_pct": -0.02,
        },
        {
            "symbol": "AFTER",
            "entry_date": "2026-08-03",
            "exit_date": "2026-08-04",
            "pnl_pct": 0.03,
        },
    ]

    touching = _trades_touching_range(
        trades,
        date(2026, 7, 1),
        date(2026, 7, 31),
    )

    assert [trade["symbol"] for trade in touching] == ["CARRY"]
