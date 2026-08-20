from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from app.api.investment_expert import status as investment_expert_status
from app.market_time import CN_TZ
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


class _UsMarketService:
    def __init__(self, *, score_direction: int = 1) -> None:
        self.calls = 0
        direction = 1 if score_direction >= 0 else -1
        self.overview = {
            "status": "live",
            "as_of": int(datetime(2026, 8, 19, 16, 0).timestamp() * 1000),
            "market_time": "2026-08-19T16:00:00-04:00",
            "benchmarks": [
                {"symbol": "SPY.US", "change_pct": 0.012 * direction},
                {"symbol": "QQQ.US", "change_pct": 0.018 * direction},
                {"symbol": "DIA.US", "change_pct": 0.008 * direction},
                {"symbol": "IWM.US", "change_pct": 0.010 * direction},
            ],
            "breadth": {"up_ratio": 0.68 if direction > 0 else 0.25,
                        "down_ratio": 0.25 if direction > 0 else 0.68},
        }

    def get_overview(self) -> dict:
        self.calls += 1
        return self.overview


class _UnavailableUsMarketService:
    def get_overview(self) -> dict:
        raise RuntimeError("US market unavailable")


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


def test_service_skips_duplicate_and_already_processed_minutes(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    repo = _Repo(trade_date)
    service = InvestmentExpertService(repo, tmp_path)
    provider = _MinuteProvider(repo.symbols, trade_date)
    provider.frame = pl.concat([provider.frame, provider.frame])
    service.minute_provider = provider
    now = datetime(2026, 8, 18, 9, 33, tzinfo=CN_TZ)
    try:
        assert service._prepare_session(now)
        already_processed = repo.symbols[0]
        service._executor.last_bar_time[already_processed] = now.replace(minute=32)
        service._last_error = "old minute replay error"

        result = service._process_new_minute_bars(now)
    finally:
        service.close()

    assert result["status"] == "succeeded"
    assert result["processed_bars"] == len(repo.symbols) - 1
    assert service._last_error is None


def test_runtime_tick_is_not_reentrant(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    service = InvestmentExpertService(_Repo(trade_date), tmp_path)
    service._cycle_lock.acquire()
    try:
        result = service.run_paper_cycle_once(
            datetime(2026, 8, 18, 9, 33, tzinfo=CN_TZ)
        )
    finally:
        service._cycle_lock.release()
        service.close()

    assert result == {"status": "reused", "reason": "paper_cycle_in_progress"}


def test_runtime_stays_idle_on_exchange_holiday(tmp_path: Path) -> None:
    trade_date = date(2026, 10, 1)
    us_market = _UsMarketService()
    service = InvestmentExpertService(
        _Repo(trade_date),
        tmp_path,
        us_market_service=us_market,
    )
    try:
        result = service.run_paper_cycle_once(
            datetime(2026, 10, 1, 9, 20, tzinfo=CN_TZ)
        )
    finally:
        service.close()

    assert result == {"status": "idle", "reason": "market_closed"}
    assert us_market.calls == 0


def test_session_preparation_records_previous_us_session_factor(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    us_market = _UsMarketService(score_direction=-1)
    service = InvestmentExpertService(
        _Repo(trade_date),
        tmp_path,
        us_market_service=us_market,
    )
    try:
        assert service._prepare_session(datetime(2026, 8, 20, 9, 15, tzinfo=CN_TZ))
        context = service.status()["overnight_us_market"]
    finally:
        service.close()

    assert us_market.calls == 1
    assert context["market_date"] == "2026-08-19"
    assert context["score"] < 0
    assert all(
        row["overnight_us_score"] == context["score"]
        for row in service._candidate_context.values()
    )


def test_overnight_us_direction_changes_candidate_ranking(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    service = InvestmentExpertService(_Repo(trade_date), tmp_path)
    try:
        risk_on, _ = service._select_candidates(
            trade_date,
            5,
            overnight_context={"score": 0.02, "tilt": 1.0},
            overnight_weight=0.5,
        )
        risk_off, _ = service._select_candidates(
            trade_date,
            5,
            overnight_context={"score": -0.02, "tilt": -1.0},
            overnight_weight=0.5,
        )
    finally:
        service.close()

    assert risk_on != risk_off
    assert risk_on[0] == service.repo.symbols[0]
    assert risk_off[0] == service.repo.symbols[-1]


def test_session_preparation_fails_closed_without_us_overnight_data(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    service = InvestmentExpertService(
        _Repo(trade_date),
        tmp_path,
        us_market_service=_UnavailableUsMarketService(),
    )
    try:
        result = service.run_paper_cycle_once(
            datetime(2026, 8, 20, 9, 20, tzinfo=CN_TZ)
        )
    finally:
        service.close()

    assert result == {"status": "degraded", "reason": "overnight_us_market_unavailable"}
    assert service.store.list_sessions() == []


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


def test_status_uses_live_app_capabilities_after_key_refresh(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    service = InvestmentExpertService(_Repo(trade_date), tmp_path, capset=CapabilitySet())
    live_capset = CapabilitySet({
        Cap.KLINE_MINUTE_BATCH: CapabilityLimits(batch=100, rpm=30),
    })
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        investment_expert_service=service,
        capabilities=live_capset,
    )))
    try:
        result = investment_expert_status(request)
    finally:
        service.close()

    assert result["minute_capable"] is True
    assert service.capset is live_capset
