from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from app.market_time import CN_TZ
from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import ExecutionEvent, PositionLot
from app.services.investment_expert import InvestmentExpertService
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet


class _Repo:
    def __init__(self, trade_date: date) -> None:
        self.symbols = [f"SH.{600000 + index}" for index in range(10)]
        self.instruments = pl.DataFrame({
            "symbol": self.symbols,
            "name": [f"Company {index}" for index in range(10)],
        })
        rows = []
        for symbol_index, symbol in enumerate(self.symbols):
            for day_index in range(35):
                day = trade_date - timedelta(days=35 - day_index)
                close = 10 + symbol_index * 0.1 + day_index * 0.02
                rows.append({
                    "symbol": symbol,
                    "date": day,
                    "open": close - 0.02,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "volume": 100_000.0,
                    "amount": close * 10_000_000,
                })
        self.daily = pl.DataFrame(rows)

    def get_instruments(self) -> pl.DataFrame:
        return self.instruments

    def get_daily_batch(
        self,
        symbols: list[str],
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        frame = self.daily.filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        )
        return frame.select(columns) if columns else frame

    def earliest_daily_date(self) -> date:
        return self.daily["date"].min()

    def latest_daily_date(self) -> date:
        return self.daily["date"].max()


class _MinuteProvider:
    def __init__(self, symbols: list[str], trade_date: date) -> None:
        self.calls: list[dict] = []
        rows = []
        for symbol in symbols:
            for minute in (31, 32):
                price = 10 + minute / 100
                rows.append({
                    "symbol": symbol,
                    "datetime": datetime.combine(
                        trade_date, datetime.min.time()
                    ).replace(hour=9, minute=minute),
                    "open": price,
                    "high": price + 0.02,
                    "low": price - 0.02,
                    "close": price,
                    "raw_open": price,
                    "raw_high": price + 0.02,
                    "raw_low": price - 0.02,
                    "raw_close": price,
                    "volume": 1_000.0,
                    "amount": price * 100_000,
                })
        self.frame = pl.DataFrame(rows)

    def get_minute(self, symbols: list[str], **kwargs) -> pl.DataFrame:
        self.calls.append({"symbols": symbols, **kwargs})
        return self.frame


class _IntradayMinuteProvider(_MinuteProvider):
    def __init__(self, symbols: list[str], trade_date: date) -> None:
        super().__init__(symbols, trade_date)
        self.intraday_calls: list[dict] = []

    def get_intraday_minute(self, symbols: list[str], **kwargs) -> pl.DataFrame:
        self.intraday_calls.append({"symbols": symbols, **kwargs})
        return self.frame


def test_service_never_replays_minutes_before_runtime_start(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    repo = _Repo(trade_date)
    service = InvestmentExpertService(repo, tmp_path)
    provider = _MinuteProvider(repo.symbols, trade_date)
    service.minute_provider = provider
    now = datetime(2026, 8, 18, 9, 33, tzinfo=CN_TZ)
    try:
        assert service._prepare_session(now)
        result = service._process_new_minute_bars(now)
    finally:
        service.close()

    assert provider.calls[0]["start_time"].strftime("%H:%M") == "09:32"
    assert result["processed_bars"] == len(repo.symbols)
    assert service.store.list_execution_events() == []


def test_service_recovers_when_global_cursor_was_not_committed(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    repo = _Repo(trade_date)
    service = InvestmentExpertService(repo, tmp_path)
    service.minute_provider = _MinuteProvider(repo.symbols, trade_date)
    now = datetime(2026, 8, 18, 9, 33, tzinfo=CN_TZ)
    try:
        assert service._prepare_session(now)
        first = service._process_new_minute_bars(now)
        service._last_processed_bar = None
        service._next_fetch_at = now.replace(hour=9, minute=32, second=0, microsecond=0)
        retried = service._process_new_minute_bars(now)
    finally:
        service.close()

    assert first["processed_bars"] == len(repo.symbols)
    assert retried["processed_bars"] == 0
    assert service._last_processed_bar == now.replace(
        hour=9, minute=32, second=0, microsecond=0
    )


def test_runtime_fails_closed_without_minute_capability(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    service = InvestmentExpertService(_Repo(trade_date), tmp_path, capset=CapabilitySet())
    try:
        result = service.start()
    finally:
        service.close()

    assert result["status"] == "blocked"
    assert result["running"] is False
    assert service.store.get_runtime_setting("enabled") is False


def test_runtime_uses_intraday_endpoint_when_batch_capability_exists(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    capset = CapabilitySet({
        Cap.INTRADAY_BATCH: CapabilityLimits(rpm=30, batch=100),
    })
    repo = _Repo(trade_date)
    service = InvestmentExpertService(repo, tmp_path, capset=capset)
    provider = _IntradayMinuteProvider(repo.symbols, trade_date)
    service.minute_provider = provider
    now = datetime(2026, 8, 18, 9, 33, tzinfo=CN_TZ)
    try:
        assert service._prepare_session(now)
        service._process_new_minute_bars(now)
    finally:
        service.close()

    assert len(provider.intraday_calls) == 1
    assert provider.calls == []


def test_three_year_dataset_is_blocked_for_tickflow_pro(tmp_path: Path, monkeypatch) -> None:
    trade_date = date(2026, 8, 18)
    capset = CapabilitySet({
        Cap.KLINE_MINUTE_BATCH: CapabilityLimits(rpm=30, batch=100),
    })
    monkeypatch.setattr(
        "app.services.investment_expert.base_tier_name",
        lambda: "pro",
    )
    service = InvestmentExpertService(_Repo(trade_date), tmp_path, capset=capset)
    try:
        result = service.submit_dataset_bootstrap(years=3)
        status = service.status()
    finally:
        service.close()

    assert result["status"] == "blocked"
    assert "仅覆盖近 1 年" in result["reason"]
    assert status["historical_minute_capable"] is True
    assert status["historical_minute_three_year_capable"] is False
    assert status["historical_minute_max_years"] == 1


def test_status_exposes_position_profit_and_execution_performance(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    service = InvestmentExpertService(_Repo(trade_date), tmp_path)
    occurred_at = datetime(2026, 8, 18, 9, 32, tzinfo=CN_TZ)
    policy = service.store.get_champion()
    assert policy is not None
    session = service.store.start_session(
        trade_date,
        policy.id,
        mode="paper",
        candidates=["SH.600000"],
    )
    service.store.save_execution_events(session["id"], [
        ExecutionEvent(
            id="evt_buy",
            event_type="order_filled",
            occurred_at=occurred_at,
            order_id="order_buy",
            symbol="SH.600000",
            side="buy",
            shares=100,
            price=10,
            fees=5,
        ),
        ExecutionEvent(
            id="evt_win",
            event_type="order_filled",
            occurred_at=occurred_at + timedelta(minutes=1),
            order_id="order_win",
            symbol="SH.600001",
            side="sell",
            shares=100,
            price=11,
            fees=6,
            realized_pnl=100,
        ),
        ExecutionEvent(
            id="evt_loss",
            event_type="order_filled",
            occurred_at=occurred_at + timedelta(minutes=2),
            order_id="order_loss",
            symbol="SH.600002",
            side="sell",
            shares=100,
            price=9,
            fees=6,
            realized_pnl=-40,
        ),
    ])
    executor = StrictMinuteExecutor(service.constitution)
    executor.cash = 999_055
    executor.lots = [PositionLot(
        lot_id="lot_current",
        symbol="SH.600000",
        acquired_date=trade_date,
        shares=100,
        remaining_shares=100,
        entry_price=10,
        entry_cost=5,
    )]
    executor.last_prices = {"SH.600000": 12}
    service._executor = executor

    try:
        status = service.status()
    finally:
        service.close()

    position = status["positions"][0]
    assert position["market_price"] == 12
    assert position["market_value"] == 1_200
    assert position["cost_basis"] == 1_005
    assert position["unrealized_pnl"] == 195
    assert position["unrealized_pnl_pct"] == 0.19403
    assert status["performance"] == {
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
        "position_count": 1,
        "position_lot_count": 1,
        "unpriced_position_count": 0,
        "unrealized_pnl": 195.0,
        "total_pnl": 255.0,
        "total_return": 0.000255,
        "valuation_as_of": None,
    }
