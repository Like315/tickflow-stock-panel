from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.us_market import router
from app.data_providers.us_market_reference import (
    parse_nasdaq_classifications,
    parse_state_street_holdings,
)
from app.services.us_market_sectors import (
    NasdaqClassificationStore,
    StateStreetHoldingsStore,
    UsMarketSectorService,
    UsMarketSectorUnavailableError,
    aggregate_group,
)


def _quote(symbol: str, change_pct: float, *, name: str | None = None) -> dict:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "last_price": 100 * (1 + change_pct),
        "prev_close": 100,
        "change_amount": 100 * change_pct,
        "change_pct": change_pct,
        "volume": 1_000,
        "amount": 100_000,
        "amount_estimated": False,
        "timestamp": 1_754_000_000_000,
        "session": "regular",
    }


CLASSIFICATION = {
    "schema_version": 1,
    "status": "live",
    "source": "Nasdaq / Quotemedia SIC mapped sector and industry",
    "standard": "sic_mapped",
    "as_of": "2026-08-20",
    "rows": [
        {"symbol": "AAA.US", "name": "Alpha", "sector": "Technology", "industry": "Software"},
        {"symbol": "BBB.US", "name": "Beta", "sector": "Technology", "industry": "Semiconductors"},
        {"symbol": "CCC.US", "name": "Gamma", "sector": "Finance", "industry": "Banks"},
    ],
}

HOLDINGS = {
    "schema_version": 1,
    "status": "live",
    "source": "State Street daily fund holdings",
    "source_url": "https://example.test/xsd.xlsx",
    "as_of": "As of 20-Aug-2026",
    "members": [
        {"symbol": "AAA.US", "name": "Alpha", "weight_pct": 60.0, "sector": "-"},
        {"symbol": "BBB.US", "name": "Beta", "weight_pct": 40.0, "sector": "-"},
    ],
}


class _Overview:
    def __init__(self) -> None:
        self.rows = [_quote("AAA.US", 0.10, name="Alpha"), _quote("BBB.US", -0.05, name="Beta")]

    def get_market_snapshot(self, *, force: bool = False):
        return {
            "status": "live",
            "as_of": 1_754_000_000_000,
            "themes": [_quote("XSD.US", 0.03, name="半导体")],
        }, list(self.rows)

    def fetch_symbol_quotes(self, symbols: list[str]) -> list[dict]:
        return []


class _ClassificationStore:
    def get(self, *, force: bool = False):
        return CLASSIFICATION


class _HoldingsStore:
    def get(self, group_id: str, *, force: bool = False):
        assert group_id == "semiconductors"
        return HOLDINGS


def _service(tmp_path: Path) -> UsMarketSectorService:
    return UsMarketSectorService(
        tmp_path,
        _Overview(),
        classifications=_ClassificationStore(),
        holdings=_HoldingsStore(),
    )


def test_parse_nasdaq_classifications_normalizes_symbols_and_keeps_empty_sector() -> None:
    result = parse_nasdaq_classifications(
        {
            "data": {
                "asOf": "2026-08-20",
                "rows": [
                    {
                        "symbol": "A",
                        "name": "Agilent",
                        "sector": "Industrials",
                        "industry": "Diagnostics",
                        "lastsale": "$155.41",
                        "pctchange": "4.695%",
                        "marketCap": "43892748417",
                        "country": "United States",
                        "ipoyear": "1999",
                        "url": "/market-activity/stocks/a",
                    },
                    {
                        "symbol": "BRK/A",
                        "name": "Berkshire",
                        "sector": "Finance",
                        "industry": "Insurance",
                    },
                    {"symbol": "EMPTY", "name": "Missing", "sector": "", "industry": ""},
                ],
            }
        }
    )

    assert result["as_of"] == "2026-08-20"
    assert [row["symbol"] for row in result["rows"]] == ["A.US", "BRK.A.US", "EMPTY.US"]
    assert result["rows"][-1]["sector"] == ""
    assert result["rows"][0]["change_pct"] == pytest.approx(0.04695)
    assert result["rows"][0]["ipo_year"] == 1999


def test_parse_state_street_holdings_reads_header_weights_and_as_of() -> None:
    frame = pl.DataFrame(
        {
            "column_1": ["Fund Name:", "Holdings:", "Name", "ALPHA INC", "US DOLLAR"],
            "column_2": ["Example", "As of 20-Aug-2026", "Ticker", "AAA", "USD"],
            "column_3": [None, None, "Identifier", "0001", "cash"],
            "column_4": [None, None, "SEDOL", "ABC", "-"],
            "column_5": [None, None, "Weight", "3.25", "0.01"],
            "column_6": [None, None, "Sector", "Technology", "Cash"],
        }
    )

    result = parse_state_street_holdings(frame)

    assert result["as_of"] == "As of 20-Aug-2026"
    assert result["members"] == [
        {
            "symbol": "AAA.US",
            "name": "ALPHA INC",
            "weight_pct": pytest.approx(3.25),
            "sector": "Technology",
        }
    ]


def test_aggregate_group_calculates_breadth_and_weighted_change() -> None:
    members = [
        {"symbol": "AAA.US", "weight_pct": 60.0},
        {"symbol": "BBB.US", "weight_pct": 40.0},
    ]
    quotes = {
        "AAA.US": _quote("AAA.US", 0.10),
        "BBB.US": _quote("BBB.US", -0.05),
    }

    result = aggregate_group(
        "semiconductors",
        "半导体",
        "Semiconductors",
        members,
        quotes,
        kind="theme",
        proxy_symbol="XSD.US",
    )

    assert result["coverage_ratio"] == 1
    assert result["avg_change_pct"] == pytest.approx(0.025)
    assert result["weighted_change_pct"] == pytest.approx(0.04)
    assert result["up"] == 1
    assert result["down"] == 1
    assert result["leader"]["symbol"] == "AAA.US"


def test_service_lists_real_sectors_and_theme_proxies(tmp_path: Path) -> None:
    result = _service(tmp_path).list_groups()

    technology = next(row for row in result["sectors"] if row["id"] == "technology")
    assert technology["name"] == "科技"
    assert technology["valid_count"] == 2
    assert technology["avg_change_pct"] == pytest.approx(0.025)
    semiconductor = next(row for row in result["themes"] if row["id"] == "semiconductors")
    assert semiconductor["proxy_quote"]["symbol"] == "XSD.US"


def test_service_sector_detail_includes_industries_and_members(tmp_path: Path) -> None:
    result = _service(tmp_path).get_detail("technology", kind="sector")

    assert result["summary"]["total_count"] == 2
    assert {row["name"] for row in result["industries"]} == {"Software", "Semiconductors"}
    assert result["members"][0]["symbol"] == "AAA.US"
    assert result["members"][0]["quote"]["change_pct"] == pytest.approx(0.10)


def test_service_theme_detail_uses_official_holdings_weights(tmp_path: Path) -> None:
    result = _service(tmp_path).get_detail("semiconductors", kind="theme")

    assert result["source"]["source"] == "State Street daily fund holdings"
    assert result["summary"]["weighted_change_pct"] == pytest.approx(0.04)
    assert result["summary"]["proxy_quote"]["symbol"] == "XSD.US"
    assert result["members"][0]["weight_pct"] == pytest.approx(60)


def test_service_theme_detail_fetches_members_missing_from_market_snapshot(tmp_path: Path) -> None:
    overview = _Overview()
    overview.rows = [_quote("ZZZ.US", 0.01)]
    overview.fetch_symbol_quotes = lambda symbols: [
        _quote(symbol, 0.02 if symbol == "AAA.US" else -0.01) for symbol in symbols
    ]
    service = UsMarketSectorService(
        tmp_path,
        overview,
        classifications=_ClassificationStore(),
        holdings=_HoldingsStore(),
    )

    result = service.get_detail("semiconductors", kind="theme")

    assert result["summary"]["valid_count"] == 2
    assert result["summary"]["weighted_change_pct"] == pytest.approx(0.008)


def test_classification_store_falls_back_to_disk_snapshot(tmp_path: Path) -> None:
    live = NasdaqClassificationStore(
        tmp_path,
        provider=SimpleNamespace(
            get_sector_classifications=lambda: {
                **CLASSIFICATION,
                "rows": CLASSIFICATION["rows"][:1],
            }
        ),
        wall_time=lambda: 100.0,
    )
    assert live.get()["status"] == "live"

    stale = NasdaqClassificationStore(
        tmp_path,
        provider=SimpleNamespace(
            get_sector_classifications=lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        ),
        wall_time=lambda: 100.0 + NasdaqClassificationStore.TTL_SECONDS + 1,
    )
    result = stale.get()

    assert result["status"] == "snapshot"
    assert len(result["rows"]) == 1
    assert "offline" not in result["message"]


def test_sector_detail_fails_closed_when_classification_is_unavailable(tmp_path: Path) -> None:
    unavailable = SimpleNamespace(
        get=lambda **_: {
            "status": "unavailable",
            "rows": [],
            "source": "Nasdaq",
            "standard": "sic_mapped",
        }
    )
    service = UsMarketSectorService(
        tmp_path,
        _Overview(),
        classifications=unavailable,
        holdings=_HoldingsStore(),
    )

    with pytest.raises(UsMarketSectorUnavailableError, match="分类当前不可用"):
        service.get_detail("technology", kind="sector")


def test_holdings_store_uses_snapshot_and_fails_closed_without_one(tmp_path: Path) -> None:
    live = StateStreetHoldingsStore(
        tmp_path,
        provider=SimpleNamespace(get_theme_holdings=lambda _: HOLDINGS),
        wall_time=lambda: 100.0,
    )
    assert live.get("semiconductors")["status"] == "live"

    offline_provider = SimpleNamespace(
        get_theme_holdings=lambda _: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    stale = StateStreetHoldingsStore(
        tmp_path,
        provider=offline_provider,
        wall_time=lambda: 100.0 + StateStreetHoldingsStore.TTL_SECONDS + 1,
    )
    result = stale.get("semiconductors")

    assert result["status"] == "snapshot"
    assert len(result["members"]) == 2
    with pytest.raises(UsMarketSectorUnavailableError, match="官方成分持仓当前不可用"):
        StateStreetHoldingsStore(
            tmp_path / "empty",
            provider=offline_provider,
        ).get("semiconductors")


def test_us_market_group_api_returns_success_and_not_found(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.us_market_overview_service = SimpleNamespace()
    app.state.us_market_sector_service = _service(tmp_path)
    client = TestClient(app)

    catalog = client.get("/api/us-market/groups")
    missing = client.get("/api/us-market/groups/not-found", params={"kind": "sector"})

    assert catalog.status_code == 200
    assert catalog.json()["sectors"][0]["kind"] == "sector"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "指定的美股板块不存在"
