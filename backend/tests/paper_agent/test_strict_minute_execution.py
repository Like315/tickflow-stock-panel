from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import MinuteBar, OrderIntent, PositionLot, RiskConstitution


def _bar(minute: int, **updates) -> MinuteBar:
    start = datetime(2026, 8, 18, 9, minute, tzinfo=UTC)
    values = {
        "symbol": "000001.SZ",
        "datetime": start,
        "received_at": start + timedelta(minutes=1, seconds=5),
        "raw_open": 10.0,
        "raw_high": 10.2,
        "raw_low": 9.9,
        "raw_close": 10.1,
        "volume": 10_000.0,
        "amount": 10_100_000.0,
    }
    values.update(updates)
    return MinuteBar(**values)


def _intent(side: str, signal_minute: int, shares: int = 1000) -> OrderIntent:
    return OrderIntent(
        id=f"order_{side}_{signal_minute}",
        decision_id=f"decision_{side}_{signal_minute}",
        symbol="000001.SZ",
        side=side,
        shares=shares,
        signal_time=datetime(2026, 8, 18, 9, signal_minute, tzinfo=UTC),
        reason="test",
    )


def test_minute_bar_tolerates_provider_float_noise() -> None:
    bar = _bar(
        31,
        raw_open=4.60,
        raw_high=4.599999999999986,
        raw_low=4.550000000000012,
        raw_close=4.56,
    )

    assert bar.raw_open == 4.60
    assert bar.raw_high == 4.599999999999986


def test_minute_bar_rejects_materially_invalid_ohlc() -> None:
    with pytest.raises(ValueError, match="raw_high is inconsistent"):
        _bar(31, raw_open=10.0, raw_high=9.99, raw_low=9.9, raw_close=9.95)


def test_signal_cannot_fill_before_next_minute() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.submit(_intent("buy", 32))

    assert executor.process_bar(_bar(31)) == []
    events = executor.process_bar(_bar(32))

    assert events[0].event_type == "order_filled"
    assert events[0].price == 10.0


def test_same_day_purchase_cannot_be_sold() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.submit(_intent("buy", 32))
    executor.process_bar(_bar(32))
    executor.submit(_intent("sell", 34))

    event = executor.process_bar(_bar(34))[0]

    assert event.event_type == "order_rejected"
    assert event.reason == "sell_t_plus_one"
    assert executor.total_shares("000001.SZ") == 1000


def test_prior_day_lot_can_sell_but_limit_down_stays_pending() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.append(PositionLot(
        lot_id="lot_old",
        symbol="000001.SZ",
        acquired_date=date(2026, 8, 17),
        shares=1000,
        remaining_shares=1000,
        entry_price=9.5,
        entry_cost=5,
    ))
    executor.submit(_intent("sell", 32))

    blocked = executor.process_bar(_bar(32, is_limit_down=True))[0]
    filled = executor.process_bar(_bar(33))[0]

    assert blocked.event_type == "order_blocked"
    assert blocked.reason == "sell_limit_down"
    assert filled.event_type == "order_filled"
    assert executor.total_shares("000001.SZ") == 0


def test_stale_or_out_of_order_bars_never_fill() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0, max_data_lag_seconds=30))
    executor.submit(_intent("buy", 32))
    stale = _bar(32, received_at=datetime(2026, 8, 18, 9, 35, tzinfo=UTC))

    event = executor.process_bar(stale)[0]

    assert event.event_type == "data_rejected"
    assert executor.total_shares("000001.SZ") == 0
    with pytest.raises(ValueError, match="out-of-order"):
        executor.process_bar(_bar(32))


def test_volume_participation_forces_partial_fill() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(
        slippage_bps=0,
        max_volume_participation=0.05,
        volume_unit_shares=100,
    ))
    executor.submit(_intent("buy", 32, shares=10_000))

    event = executor.process_bar(_bar(32, volume=100))[0]

    assert event.event_type == "order_partially_filled"
    assert event.shares == 500


def test_buy_order_never_carries_overnight() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0, buy_order_ttl_minutes=1))
    executor.submit(_intent("buy", 32, shares=100))
    next_day = datetime(2026, 8, 19, 9, 30, tzinfo=UTC)

    event = executor.process_bar(_bar(
        32,
        datetime=next_day,
        received_at=next_day + timedelta(minutes=1, seconds=1),
    ))[0]

    assert event.event_type == "order_rejected"
    assert event.reason == "buy_order_expired"
    assert executor.total_shares("000001.SZ") == 0


def test_missing_immediate_execution_minute_cancels_buy() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0, buy_order_ttl_minutes=1))
    executor.submit(_intent("buy", 32, shares=100))

    event = executor.process_bar(_bar(33))[0]

    assert event.event_type == "order_rejected"
    assert event.reason == "buy_order_expired"


def test_blocked_sell_survives_executor_state_restore() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.append(PositionLot(
        lot_id="old",
        symbol="000001.SZ",
        acquired_date=date(2026, 8, 17),
        shares=100,
        remaining_shares=100,
        entry_price=10,
        entry_cost=5,
    ))
    executor.submit(_intent("sell", 32, shares=100))
    assert executor.process_bar(_bar(32, is_limit_down=True))[0].event_type == "order_blocked"
    restored = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    restored.restore_state(executor.export_state())

    event = restored.process_bar(_bar(33))[0]

    assert event.event_type == "order_filled"
    assert restored.total_shares("000001.SZ") == 0
