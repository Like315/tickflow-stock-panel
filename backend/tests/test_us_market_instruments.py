from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.us_market import router
from app.data_providers.us_market_data import (
    TickFlowUsMarketDataProvider,
    parse_tickflow_daily,
    parse_tickflow_instruments,
)
from app.services.us_market_instruments import (
    UsMarketHistoryStore,
    UsMarketHistoryUnavailableError,
    UsMarketInstrumentService,
    UsMarketInstrumentStore,
)

INSTRUMENTS = {
    "schema_version": 1,
    "source": "TickFlow US_Equity",
    "universe": "US_Equity",
    "declared_count": 3,
    "metadata_count": 3,
    "rows": [
        {
            "symbol": "AAA.US", "code": "AAA", "exchange": "US", "region": "US",
            "name": "阿尔法", "instrument_type": "stock", "total_shares": 1000.0,
            "float_shares": 900.0,
        },
        {
            "symbol": "BBB.US", "code": "BBB", "exchange": "US", "region": "US",
            "name": "贝塔", "instrument_type": "stock", "total_shares": 2000.0,
            "float_shares": 1800.0,
        },
        {
            "symbol": "CCC.US", "code": "CCC", "exchange": "US", "region": "US",
            "name": "伽马", "instrument_type": "stock", "total_shares": None,
            "float_shares": None,
        },
    ],
}

CLASSIFICATIONS = {
    "schema_version": 1,
    "status": "live",
    "source": "Nasdaq / Quotemedia SIC mapped sector and industry",
    "standard": "sic_mapped",
    "rows": [
        {
            "symbol": "AAA.US", "name": "Alpha Inc", "sector": "Technology",
            "industry": "Software", "country": "United States", "market_cap": 5000.0,
            "last_price": 10.0, "change_pct": 0.01, "volume": 100.0,
        },
        {
            "symbol": "BBB.US", "name": "Beta Inc", "sector": "Finance",
            "industry": "Banks", "country": "United States", "market_cap": 8000.0,
            "last_price": 20.0, "change_pct": -0.03, "volume": 500.0,
        },
        {
            "symbol": "DDD.US", "name": "Delta Inc", "sector": "Industrials",
            "industry": "Tools", "country": "Canada", "market_cap": 3000.0,
            "last_price": 5.0, "change_pct": 0.05, "volume": 200.0,
        },
    ],
}

DAILY = {
    "schema_version": 1,
    "source": "TickFlow",
    "symbol": "AAA.US",
    "adjust": "none",
    "rows": [
        {
            "date": "2026-08-18", "timestamp": 1, "open": 10.0, "high": 11.0,
            "low": 9.5, "close": 10.5, "volume": 100.0, "amount": 1000.0,
            "change_pct": None,
        },
        {
            "date": "2026-08-19", "timestamp": 2, "open": 10.5, "high": 12.0,
            "low": 10.0, "close": 11.0, "volume": 120.0, "amount": 1200.0,
            "change_pct": 11 / 10.5 - 1,
        },
    ],
}


class _Provider:
    name = "test"

    def get_instruments(self):
        return INSTRUMENTS

    def get_daily(self, symbol: str, *, count: int, adjust: str):
        return {**DAILY, "symbol": symbol, "adjust": adjust, "rows": DAILY["rows"][-count:]}


class _Overview:
    def get_market_snapshot(self, *, force: bool = False):
        return {"status": "live"}, [{
            "symbol": "AAA.US", "last_price": 12.0, "change_amount": 1.0,
            "change_pct": 0.02, "volume": 500.0, "amount": 6000.0, "timestamp": 3,
        }]


def _service(tmp_path: Path, overview=None) -> UsMarketInstrumentService:
    provider = _Provider()
    return UsMarketInstrumentService(
        tmp_path,
        overview or _Overview(),
        SimpleNamespace(get=lambda **_: CLASSIFICATIONS),
        instruments=UsMarketInstrumentStore(tmp_path, provider=provider),
        history=UsMarketHistoryStore(tmp_path, provider=provider),
    )


def test_parse_tickflow_instruments_keeps_every_universe_symbol() -> None:
    result = parse_tickflow_instruments(
        {"id": "US_Equity", "symbol_count": 2, "symbols": ["AAA.US", "MISSING.US"]},
        [{
            "symbol": "AAA.US", "code": "AAA", "exchange": "US", "region": "US",
            "name": "Alpha", "type": "stock", "ext": {"total_shares": 100.0},
        }],
    )

    assert result["declared_count"] == 2
    assert [row["symbol"] for row in result["rows"]] == ["AAA.US", "MISSING.US"]
    assert result["rows"][1]["name"] == "MISSING.US"


def test_parse_tickflow_daily_calculates_decimal_change() -> None:
    result = parse_tickflow_daily("AAA", {
        "timestamp": [1_776_744_000_000, 1_776_830_400_000],
        "open": [10, 11], "high": [11, 12], "low": [9, 10], "close": [10, 11],
        "volume": [100, 120], "amount": [1000, 1200],
    }, adjust="none")

    assert result["symbol"] == "AAA.US"
    assert result["rows"][1]["change_pct"] == pytest.approx(0.1)


def test_tickflow_provider_retries_large_instrument_batches() -> None:
    symbols = [f"S{index:03d}.US" for index in range(501)]

    class Instruments:
        @staticmethod
        def batch(chunk: list[str]):
            if len(chunk) > 100:
                raise RuntimeError("payload too large")
            return [{"symbol": symbol, "name": symbol, "type": "stock"} for symbol in chunk]

    client = SimpleNamespace(
        universes=SimpleNamespace(get=lambda _: {
            "id": "US_Equity", "symbol_count": len(symbols), "symbols": symbols,
        }),
        instruments=Instruments(),
    )

    result = TickFlowUsMarketDataProvider(client_factory=lambda: client).get_instruments()

    assert len(result["rows"]) == 501
    assert result["metadata_count"] == 501


def test_instrument_service_merges_searches_and_pages(tmp_path: Path) -> None:
    service = _service(tmp_path)

    technology = service.list_instruments(query="alpha", sector="Technology")
    all_rows = service.list_instruments(limit=2, offset=2)
    detail = service.get_instrument("AAA")

    assert technology["total"] == 4
    assert technology["matched"] == 1
    assert technology["rows"][0]["name"] == "阿尔法"
    assert technology["rows"][0]["name_en"] == "Alpha Inc"
    assert technology["rows"][0]["last_price"] == pytest.approx(12)
    assert len(all_rows["rows"]) == 2
    assert detail["instrument"]["industry"] == "Software"


def test_instrument_store_uses_snapshot_and_returns_unavailable_without_one(tmp_path: Path) -> None:
    live = UsMarketInstrumentStore(tmp_path, provider=_Provider(), wall_time=lambda: 100.0)
    assert live.get()["status"] == "live"

    offline = SimpleNamespace(
        name="offline",
        get_instruments=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    stale = UsMarketInstrumentStore(
        tmp_path,
        provider=offline,
        wall_time=lambda: 100.0 + UsMarketInstrumentStore.TTL_SECONDS + 1,
    )
    assert stale.get()["status"] == "snapshot"
    unavailable = UsMarketInstrumentStore(tmp_path / "empty", provider=offline).get()
    assert unavailable["status"] == "unavailable"
    assert unavailable["rows"] == []


def test_rankings_use_nasdaq_snapshot_when_full_market_quotes_are_unavailable(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        SimpleNamespace(get_market_snapshot=lambda **_: ({"status": "partial"}, [])),
    )

    result = service.get_rankings(limit=2)

    assert result["status"] == "snapshot"
    assert [row["symbol"] for row in result["rankings"]["gainers"]] == [
        "DDD.US", "AAA.US",
    ]
    assert result["rankings"]["losers"][0]["symbol"] == "BBB.US"
    assert result["rankings"]["active"][0]["symbol"] == "BBB.US"
    assert result["rankings"]["active"][0]["amount_estimated"] is True
    assert result["breadth"] == {
        "total": 3,
        "up": 2,
        "down": 1,
        "flat": 0,
        "strong": 1,
        "weak": 1,
        "up_ratio": pytest.approx(2 / 3),
        "down_ratio": pytest.approx(1 / 3),
    }
    assert sum(item["count"] for item in result["distribution"]) == 3


def test_history_store_uses_snapshot_and_fails_closed(tmp_path: Path) -> None:
    live = UsMarketHistoryStore(tmp_path, provider=_Provider(), wall_time=lambda: 100.0)
    assert live.get("AAA", count=2, adjust="none")["status"] == "live"

    offline = SimpleNamespace(
        name="offline",
        get_daily=lambda *_, **__: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    stale = UsMarketHistoryStore(
        tmp_path,
        provider=offline,
        wall_time=lambda: 100.0 + UsMarketHistoryStore.TTL_SECONDS + 1,
    )
    assert stale.get("AAA", count=2, adjust="none")["status"] == "snapshot"
    with pytest.raises(UsMarketHistoryUnavailableError):
        UsMarketHistoryStore(tmp_path / "empty", provider=offline).get(
            "AAA", count=2, adjust="none"
        )


def test_us_market_instrument_api_lists_details_and_history(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.us_market_instrument_service = _service(tmp_path)
    client = TestClient(app)

    catalog = client.get("/api/us-market/instruments", params={"q": "AAA"})
    detail = client.get("/api/us-market/instruments/AAA.US")
    daily = client.get("/api/us-market/instruments/AAA.US/daily", params={"count": 10})
    rankings = client.get("/api/us-market/rankings", params={"limit": 2})
    missing = client.get("/api/us-market/instruments/NOT_FOUND.US")

    assert catalog.status_code == 200
    assert catalog.json()["matched"] == 1
    assert detail.status_code == 200
    assert daily.status_code == 200
    assert daily.json()["rows"][-1]["close"] == pytest.approx(11)
    assert rankings.status_code == 200
    assert len(rankings.json()["rankings"]["gainers"]) == 2
    assert missing.status_code == 404
