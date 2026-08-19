from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from app.paper_agent.dataset import TrainingDatasetBuilder, build_point_in_time_candidates
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


def test_partial_enriched_history_is_filled_from_raw_daily() -> None:
    raw = pl.DataFrame({
        "symbol": ["600000.SH"] * 3,
        "date": [date(2023, 8, 18), date(2025, 8, 18), date(2026, 8, 18)],
        "close": [8.0, 9.0, 10.0],
        "amount": [80.0, 90.0, 100.0],
    })
    enriched = raw.tail(2).with_columns(pl.lit(1.0).alias("rps_20"))

    result = TrainingDatasetBuilder._merge_daily_history(enriched, raw).sort("date")

    assert result["date"].to_list() == raw["date"].to_list()
    assert result["rps_20"].to_list() == [None, 1.0, 1.0]
