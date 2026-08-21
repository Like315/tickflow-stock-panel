from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from app.api.investment_expert import status as investment_expert_status
from app.market_time import CN_TZ
from app.paper_agent.models import PositionLot
from app.services.investment_expert import InvestmentExpertService
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet


class _Repo:
    def __init__(self, trade_date: date) -> None:
        self.symbols = [f"SH.{600000 + index}" for index in range(10)]
        self.instruments = pl.DataFrame({
            "symbol": self.symbols,
            "name": [f"Company {index}" for index in range(10)],
            "industry": ["半导体"] * 5 + ["银行"] * 5,
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
            "sectors": [
                {"symbol": "XLK.US", "name": "信息技术", "change_pct": 0.02 * direction},
                {"symbol": "XLF.US", "name": "金融", "change_pct": -0.015 * direction},
            ],
            "themes": [
                {"symbol": "XSD.US", "name": "半导体", "change_pct": 0.03 * direction},
                {"symbol": "KBE.US", "name": "银行", "change_pct": -0.02 * direction},
            ],
        }

    def get_overview(self) -> dict:
        self.calls += 1
        return self.overview

    @staticmethod
    def get_proxy_volatilities(symbols: list[str], *, window: int = 20) -> dict[str, float]:
        assert window == 20
        return {symbol: 0.02 for symbol in symbols}


class _UnavailableUsMarketService:
    def get_overview(self) -> dict:
        raise RuntimeError("US market unavailable")


class _StaleUsMarketService(_UsMarketService):
    def __init__(self) -> None:
        super().__init__()
        self.overview["market_time"] = "2026-08-01T16:00:00-04:00"


class _NewsSentimentService:
    refresh_seconds = 600

    def __init__(self) -> None:
        self.calls = 0

    def get_context(self, as_of: datetime) -> dict:
        self.calls += 1
        return {
            "available": True,
            "status": "live",
            "as_of": as_of.isoformat(),
            "score": 0.4,
            "confidence": 1.0,
            "item_count": 8,
            "signal_count": 6,
            "source_count": 2,
            "regions": {"global": 2, "domestic": 3, "market": 3},
            "items": [],
        }

    @staticmethod
    def score_candidates(_context: dict, candidates: dict) -> dict:
        symbols = sorted(candidates)
        return {
            symbol: {
                "score": 1.0 if symbol == symbols[-1] else 0.0,
                "matched_count": 1 if symbol == symbols[-1] else 0,
                "headlines": [],
            }
            for symbol in symbols
        }


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
    assert set(context["modules"]) == {"XLK.US", "XLF.US", "XSD.US", "KBE.US"}
    semiconductor = service._candidate_context[service.repo.symbols[0]]
    bank = service._candidate_context[service.repo.symbols[-1]]
    assert semiconductor["overnight_us_module_symbol"] == "XSD.US"
    assert semiconductor["overnight_us_factor"] < 0
    assert bank["overnight_us_module_symbol"] == "KBE.US"
    assert bank["overnight_us_factor"] > 0


def test_overnight_us_modules_adjust_only_matching_candidate_ranking(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    service = InvestmentExpertService(_Repo(trade_date), tmp_path)
    positive_semiconductors = {
        "available": True,
        "score": -0.01,
        "modules": {
            "XSD.US": {
                "symbol": "XSD.US",
                "name": "半导体",
                "change_pct": 0.03,
                "normalized_signal": 1.0,
                "data_confidence": 1.0,
            },
            "KBE.US": {
                "symbol": "KBE.US",
                "name": "银行",
                "change_pct": -0.03,
                "normalized_signal": -1.0,
                "data_confidence": 1.0,
            },
        },
    }
    negative_semiconductors = {
        **positive_semiconductors,
        "score": 0.01,
        "modules": {
            "XSD.US": {
                **positive_semiconductors["modules"]["XSD.US"],
                "change_pct": -0.03,
                "normalized_signal": -1.0,
            },
            "KBE.US": {
                **positive_semiconductors["modules"]["KBE.US"],
                "change_pct": 0.03,
                "normalized_signal": 1.0,
            },
        },
    }
    try:
        risk_on, _ = service._select_candidates(
            trade_date,
            5,
            overnight_context=positive_semiconductors,
            overnight_weight=0.5,
        )
        risk_off, _ = service._select_candidates(
            trade_date,
            5,
            overnight_context=negative_semiconductors,
            overnight_weight=0.5,
        )
    finally:
        service.close()

    assert risk_on != risk_off
    assert set(risk_on) == set(service.repo.symbols[:5])
    assert set(risk_off) == set(service.repo.symbols[5:])


def test_overnight_us_market_background_does_not_change_candidate_ranking(
    tmp_path: Path,
) -> None:
    trade_date = date(2026, 8, 20)
    service = InvestmentExpertService(_Repo(trade_date), tmp_path)
    try:
        positive, _ = service._select_candidates(
            trade_date,
            10,
            overnight_context={"score": 0.04, "tilt": 1.0, "modules": {}},
            overnight_weight=0.5,
        )
        negative, _ = service._select_candidates(
            trade_date,
            10,
            overnight_context={"score": -0.04, "tilt": -1.0, "modules": {}},
            overnight_weight=0.5,
        )
    finally:
        service.close()

    assert positive == negative


def test_overnight_us_module_mapping_uses_a_share_industry_snapshot(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    repo = _Repo(trade_date)
    repo.instruments = repo.instruments.drop("industry")
    industry_path = tmp_path / "ext_data" / "ext_hy_ths" / "part.parquet"
    industry_path.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": repo.symbols,
        "所属同花顺行业": ["电子-半导体"] * 5 + ["金融-银行"] * 5,
    }).write_parquet(industry_path)
    service = InvestmentExpertService(repo, tmp_path)
    try:
        factors = service._score_candidate_overnight_modules(
            {
                str(row["symbol"]): row
                for row in repo.instruments.iter_rows(named=True)
            },
            {
                "modules": {
                    "XSD.US": {
                        "symbol": "XSD.US",
                        "name": "半导体",
                        "change_pct": -0.03,
                        "normalized_signal": -1.0,
                        "data_confidence": 1.0,
                    },
                    "KBE.US": {
                        "symbol": "KBE.US",
                        "name": "银行",
                        "change_pct": 0.02,
                        "normalized_signal": 1.0,
                        "data_confidence": 1.0,
                    },
                }
            },
        )
    finally:
        service.close()

    assert factors[repo.symbols[0]]["symbol"] == "XSD.US"
    assert factors[repo.symbols[0]]["factor"] == -1.0
    assert factors[repo.symbols[-1]]["symbol"] == "KBE.US"
    assert factors[repo.symbols[-1]]["factor"] == 1.0


def test_news_sentiment_adds_high_weight_candidate_factor(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    news = _NewsSentimentService()
    service = InvestmentExpertService(
        _Repo(trade_date),
        tmp_path,
        news_sentiment_service=news,
    )
    context = news.get_context(datetime(2026, 8, 20, 9, 15, tzinfo=CN_TZ))
    try:
        baseline, _ = service._select_candidates(trade_date, 10)
        selected, candidate_context = service._select_candidates(
            trade_date,
            10,
            news_context=context,
            news_weight=0.50,
        )
    finally:
        service.close()

    promoted = service.repo.symbols[-1]
    assert selected.index(promoted) < baseline.index(promoted)
    assert candidate_context[promoted]["candidate_news_sentiment"] == 1.0
    assert candidate_context[promoted]["score"] > candidate_context[promoted]["market_score"]
    assert candidate_context[promoted]["news_applied_weight"] == 0.5


def test_news_sentiment_refreshes_during_active_session(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    news = _NewsSentimentService()
    service = InvestmentExpertService(
        _Repo(trade_date),
        tmp_path,
        news_sentiment_service=news,
    )
    prepared_at = datetime(2026, 8, 20, 9, 15, tzinfo=CN_TZ)
    try:
        assert service._prepare_session(prepared_at)
        service._refresh_news_sentiment_context(prepared_at + timedelta(minutes=9))
        assert news.calls == 1
        service._refresh_news_sentiment_context(prepared_at + timedelta(minutes=10))
    finally:
        service.close()

    assert news.calls == 2


def test_session_preparation_degrades_without_us_overnight_data(tmp_path: Path) -> None:
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

    assert result == {"status": "idle", "reason": "outside_continuous_session"}
    assert len(service.store.list_sessions()) == 1
    assert service.status()["overnight_us_market"] == {
        "available": False,
        "status": "unavailable",
        "market_date": None,
        "score": 0.0,
        "tilt": 0.0,
        "market_background_available": False,
        "benchmarks": {},
        "modules": {},
    }
    assert all(
        row["overnight_us_available"] is False
        for row in service._candidate_context.values()
    )


def test_session_preparation_degrades_with_stale_us_overnight_data(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 20)
    service = InvestmentExpertService(
        _Repo(trade_date),
        tmp_path,
        us_market_service=_StaleUsMarketService(),
    )
    try:
        assert service._prepare_session(datetime(2026, 8, 20, 9, 15, tzinfo=CN_TZ))
        context = service.status()["overnight_us_market"]
    finally:
        service.close()

    assert len(service.store.list_sessions()) == 1
    assert context["available"] is False
    assert context["status"] == "stale"
    assert context["market_date"] == "2026-08-01"


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


def test_status_includes_latest_price_for_current_positions(tmp_path: Path) -> None:
    trade_date = date(2026, 8, 18)
    repo = _Repo(trade_date)
    service = InvestmentExpertService(repo, tmp_path)
    lot = PositionLot(
        lot_id="lot_test",
        symbol=repo.symbols[0],
        acquired_date=trade_date,
        shares=100,
        remaining_shares=100,
        entry_price=10.0,
        entry_cost=5.0,
    )
    try:
        assert service._prepare_session(datetime(2026, 8, 18, 9, 15, tzinfo=CN_TZ))
        service._executor.lots = [lot]
        service._executor.last_prices[repo.symbols[0]] = 10.25

        live_result = service.status()
        service.store.save_portfolio_snapshot(
            service._session["id"],
            as_of=datetime(2026, 8, 18, 9, 16, tzinfo=CN_TZ),
            cash=service._executor.cash,
            equity=service._executor.equity(),
            payload={
                "lots": [lot.model_dump(mode="json")],
                "last_prices": {repo.symbols[0]: 10.25},
                "executor_state": {"pending": []},
            },
        )
        service._executor = None
        snapshot_result = service.status()
    finally:
        service.close()

    assert live_result["positions"][0]["current_price"] == 10.25
    assert snapshot_result["positions"][0]["current_price"] == 10.25
