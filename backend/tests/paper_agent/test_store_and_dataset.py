from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from app.paper_agent.dataset import (
    HistoricalMinuteDataError,
    TrainingDatasetBuilder,
    build_point_in_time_candidates,
)
from app.paper_agent.models import ExecutionEvent, ExpertPolicy
from app.paper_agent.store import PaperAgentStore


def test_candidate_source_date_is_always_before_trade_date() -> None:
    dates = pl.date_range(date(2026, 1, 1), date(2026, 2, 20), interval="1d", eager=True)
    rows = []
    for symbol, offset in (("A", 0.0), ("B", 1.0), ("C", -1.0)):
        for index, day in enumerate(dates):
            rows.append({
                "symbol": symbol,
                "name": symbol,
                "date": day,
                "close": 10 + offset + index * (0.1 if symbol != "C" else -0.02),
                "amount": 100_000_000 + index,
            })
    result = build_point_in_time_candidates(pl.DataFrame(rows), limit=2)

    assert not result.is_empty()
    assert result.filter(pl.col("source_date") >= pl.col("trade_date")).is_empty()
    assert result.group_by("trade_date").len()["len"].max() <= 2


def test_policy_versions_are_immutable_and_champion_uses_promotion_ledger(tmp_path) -> None:
    store = PaperAgentStore(tmp_path)
    baseline = store.ensure_baseline_policy()
    candidate = ExpertPolicy(
        id="expert_v2",
        version=2,
        parent_id=baseline.id,
        min_vwap_bias=0.002,
        mutation_note="raise min_vwap_bias",
    )
    store.save_policy(candidate)

    with pytest.raises(ValueError, match="conflict"):
        store.save_policy(candidate)
    assert store.get_champion().id == baseline.id  # type: ignore[union-attr]

    store.promote(candidate.id, reason="protected evaluation passed", metrics={"expectancy": 0.01})

    assert store.get_champion().id == candidate.id  # type: ignore[union-attr]

    rollback = store.rollback_last_promotion(
        reason="paper drawdown", metrics={"drawdown": -0.2}
    )
    assert rollback is not None
    assert store.get_champion().id == baseline.id  # type: ignore[union-attr]
    assert store.rollback_last_promotion(reason="again", metrics={}) is None


def test_session_and_execution_events_are_append_only(tmp_path) -> None:
    store = PaperAgentStore(tmp_path)
    policy = store.ensure_baseline_policy()
    session = store.start_session(date(2026, 8, 18), policy.id, mode="paper", candidates=["A"])
    event = ExecutionEvent(
        id="evt_1",
        event_type="order_filled",
        occurred_at=datetime(2026, 8, 18, 9, 32, tzinfo=UTC),
        order_id="order_1",
        symbol="A",
        side="buy",
        shares=100,
        price=10,
    )

    assert store.save_execution_events(session["id"], [event]) == 1
    assert store.save_execution_events(session["id"], [event]) == 1
    assert len(store.list_execution_events(session_id=session["id"])) == 1


def test_trade_history_joins_fill_to_recorded_decision_reason(tmp_path) -> None:
    store = PaperAgentStore(tmp_path)
    policy = store.ensure_baseline_policy()
    session = store.start_session(
        date(2026, 8, 18), policy.id, mode="paper", candidates=["A"]
    )
    decision_time = datetime(2026, 8, 18, 9, 32, tzinfo=UTC)
    features = {
        "candidate_score": 0.91,
        "daily_momentum_20d": 0.12,
        "vwap_bias": 0.015,
        "breakout_pct": 0.008,
        "model_probability": 0.72,
    }
    store.save_decision(
        decision_id="decision_buy_a",
        session_id=session["id"],
        symbol="A",
        decision_time=decision_time,
        action="buy",
        features=features,
        reason="vwap_and_opening_range_confirmed",
    )
    store.save_execution_events(session["id"], [ExecutionEvent(
        id="evt_buy_a",
        event_type="order_filled",
        occurred_at=decision_time + timedelta(minutes=1),
        order_id="order_decision_buy_a",
        symbol="A",
        side="buy",
        shares=100,
        price=10.2,
        fees=5,
        reason="next_minute_open",
    )])

    history = store.list_trade_history(limit=10)

    assert history == [{
        "id": "evt_buy_a",
        "session_id": session["id"],
        "trade_date": "2026-08-18",
        "order_id": "order_decision_buy_a",
        "symbol": "A",
        "side": "buy",
        "occurred_at": (decision_time + timedelta(minutes=1)).isoformat(),
        "fill_status": "order_filled",
        "shares": 100,
        "price": 10.2,
        "fees": 5.0,
        "realized_pnl": None,
        "execution_reason": "next_minute_open",
        "decision_id": "decision_buy_a",
        "decision_time": decision_time.isoformat(),
        "decision_action": "buy",
        "decision_reason": "vwap_and_opening_range_confirmed",
        "decision_features": features,
    }]


def test_execution_statistics_use_after_cost_closed_trades_and_refresh_cache(tmp_path) -> None:
    store = PaperAgentStore(tmp_path)
    policy = store.ensure_baseline_policy()
    session = store.start_session(date(2026, 8, 18), policy.id, mode="paper", candidates=["A"])
    occurred_at = datetime(2026, 8, 18, 9, 32, tzinfo=UTC)
    buy = ExecutionEvent(
        id="evt_buy",
        event_type="order_filled",
        occurred_at=occurred_at,
        order_id="order_buy",
        symbol="A",
        side="buy",
        shares=100,
        price=10,
        fees=5,
    )
    store.save_execution_events(session["id"], [buy])

    assert store.execution_statistics()["filled_order_count"] == 1

    exits = [
        ExecutionEvent(
            id="evt_win",
            event_type="order_filled",
            occurred_at=occurred_at + timedelta(minutes=1),
            order_id="order_win",
            symbol="A",
            side="sell",
            shares=100,
            price=11,
            fees=6,
            realized_pnl=100,
        ),
        ExecutionEvent(
            id="evt_loss",
            event_type="order_partially_filled",
            occurred_at=occurred_at + timedelta(minutes=2),
            order_id="order_loss",
            symbol="B",
            side="sell",
            shares=100,
            price=9,
            fees=6,
            realized_pnl=-40,
        ),
    ]
    store.save_execution_events(session["id"], exits)

    statistics = store.execution_statistics()
    assert statistics == {
        "filled_order_count": 3,
        "buy_order_count": 1,
        "sell_order_count": 2,
        "closed_trade_count": 2,
        "winning_trade_count": 1,
        "losing_trade_count": 1,
        "breakeven_trade_count": 0,
        "realized_pnl": 60.0,
        "win_rate": 0.5,
        "average_win_pnl": 100.0,
        "average_loss_pnl": 40.0,
        "profit_loss_ratio": 2.5,
        "latest_fill_at": (occurred_at + timedelta(minutes=2)).isoformat(),
    }


def test_historical_minute_partition_marks_one_price_limit_up() -> None:
    trade_date = date(2026, 8, 18)
    daily = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH"],
        "date": [date(2026, 8, 17), trade_date],
        "close": [10.0, 11.0],
        "raw_close": [10.0, 11.0],
        "name": ["浦发银行", "浦发银行"],
    })
    minute = pl.DataFrame({
        "symbol": ["600000.SH"],
        "datetime": [datetime(2026, 8, 18, 9, 30)],
        "raw_open": [11.0],
        "raw_high": [11.0],
        "raw_low": [11.0],
        "raw_close": [11.0],
        "volume": [1_000.0],
    })

    result = TrainingDatasetBuilder._add_execution_flags(minute, daily, trade_date)

    assert result["previous_close"][0] == 10.0
    assert result["is_limit_up"][0] is True
    assert result["is_limit_down"][0] is False


def test_dataset_dates_are_processed_in_chronological_order() -> None:
    candidates = pl.DataFrame({
        "trade_date": [date(2024, 10, 10), date(2024, 2, 1), date(2024, 1, 22)],
        "symbol": ["A", "A", "A"],
    })

    groups = TrainingDatasetBuilder._ordered_date_groups(candidates)

    assert [trade_date for trade_date, _ in groups] == [
        date(2024, 1, 22),
        date(2024, 2, 1),
        date(2024, 10, 10),
    ]


def test_dataset_fails_fast_when_historical_minute_day_is_empty(tmp_path) -> None:
    class Repo:
        def __init__(self) -> None:
            days = pl.date_range(date(2024, 1, 1), date(2024, 3, 15), interval="1d", eager=True)
            self.daily = pl.DataFrame({
                "symbol": ["600000.SH"] * len(days),
                "name": ["浦发银行"] * len(days),
                "date": days,
                "open": [10.0] * len(days),
                "high": [10.1] * len(days),
                "low": [9.9] * len(days),
                "close": [10.0 + index * 0.01 for index in range(len(days))],
                "raw_close": [10.0 + index * 0.01 for index in range(len(days))],
                "volume": [100_000.0] * len(days),
                "amount": [100_000_000.0] * len(days),
            })

        def latest_daily_date(self):
            return self.daily["date"].max()

        def get_instruments(self):
            return pl.DataFrame({"symbol": ["600000.SH"], "name": ["浦发银行"]})

        def get_enriched_range(self, *_args, **_kwargs):
            return self.daily

    class EmptyHistoryProvider:
        name = "empty_history"

        def get_minute(self, *_args, **_kwargs):
            return pl.DataFrame()

    builder = TrainingDatasetBuilder(Repo(), tmp_path, EmptyHistoryProvider())

    with pytest.raises(HistoricalMinuteDataError, match="returned no 1m data"):
        builder.build(
            start_date=date(2024, 2, 1),
            end_date=date(2024, 3, 15),
            candidate_limit=1,
        )
