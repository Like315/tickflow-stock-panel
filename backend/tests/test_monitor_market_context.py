from __future__ import annotations

from datetime import date, datetime

import polars as pl

from app.market_time import CN_TZ
from app.services.monitor_market_context import (
    MonitorMarketContextService,
    build_overnight_us_context,
)


def _overview(*, market_time: str = "2026-08-20T16:00:00-04:00") -> dict:
    return {
        "status": "live",
        "market_time": market_time,
        "as_of": "2026-08-21T04:00:00+08:00",
        "benchmarks": [
            {"symbol": "SPY.US", "change_pct": -0.01},
            {"symbol": "QQQ.US", "change_pct": -0.02},
            {"symbol": "DIA.US", "change_pct": -0.005},
            {"symbol": "IWM.US", "change_pct": -0.015},
        ],
        "breadth": {"up_ratio": 0.30, "down_ratio": 0.70},
    }


def test_build_overnight_us_context_scores_previous_session() -> None:
    context = build_overnight_us_context(_overview(), date(2026, 8, 21))

    assert context["available"] is True
    assert context["market_date"] == "2026-08-20"
    assert context["score"] < 0
    assert context["tilt"] < 0


def test_build_overnight_us_context_rejects_stale_session() -> None:
    context = build_overnight_us_context(
        _overview(market_time="2026-08-01T16:00:00-04:00"),
        date(2026, 8, 21),
    )

    assert context == {
        "available": False,
        "status": "stale",
        "market_date": "2026-08-01",
        "score": 0.0,
        "tilt": 0.0,
        "benchmarks": {},
    }


class _Repo:
    @staticmethod
    def get_instruments() -> pl.DataFrame:
        return pl.DataFrame({
            "symbol": ["300750.SZ"],
            "name": ["宁德时代"],
        })


class _UsMarket:
    def __init__(self) -> None:
        self.calls = 0

    def get_overview(self) -> dict:
        self.calls += 1
        return _overview()


class _News:
    def __init__(self) -> None:
        self.calls = 0

    def get_context(self, as_of: datetime) -> dict:
        self.calls += 1
        return {
            "available": True,
            "status": "live",
            "as_of": as_of.isoformat(),
            "score": 1.0,
            "items": [{
                "title": "宁德时代获批重大项目",
                "snippet": "",
                "sentiment": 1.0,
                "recency_weight": 1.0,
            }],
        }


def test_snapshot_for_scores_news_without_refreshing_network() -> None:
    us_market = _UsMarket()
    news = _News()
    service = MonitorMarketContextService(
        _Repo(),
        us_market,
        news,  # type: ignore[arg-type]
    )
    service.refresh_once(datetime(2026, 8, 21, 9, 15, tzinfo=CN_TZ))

    snapshot = service.snapshot_for(["300750.SZ"])

    assert us_market.calls == 1
    assert news.calls == 1
    assert snapshot["candidate_news"]["300750.SZ"] == {
        "score": 1.0,
        "matched_count": 1,
        "headlines": ["宁德时代获批重大项目"],
    }
    service.snapshot_for(["300750.SZ"])
    assert us_market.calls == 1
    assert news.calls == 1
