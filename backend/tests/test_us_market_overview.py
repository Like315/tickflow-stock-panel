from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.us_market_overview import (
    PROXY_SYMBOLS,
    REALTIME_SAMPLE,
    UsMarketOverviewService,
    build_live_overview,
    build_partial_overview,
    normalize_us_quote,
)


def _quote(
    symbol: str,
    last: float,
    previous: float,
    *,
    name: str | None = None,
    volume: float = 1_000,
    amount: float | None = None,
    timestamp: int = 1_754_000_000_000,
) -> dict:
    high = max(last, previous) * 1.01
    low = min(last, previous) * 0.99
    return {
        "symbol": symbol,
        "name": name or symbol,
        "last_price": last,
        "prev_close": previous,
        "open": previous,
        "high": high,
        "low": low,
        "volume": volume,
        "amount": amount,
        "timestamp": timestamp,
        "session": "regular",
        "ext": {"change_pct": (last - previous) / previous},
    }


def _market_quotes() -> list[dict]:
    return [
        _quote("AAA.US", 110, 100, name="Alpha"),
        _quote("BBB.US", 90, 100, name="Beta"),
        _quote("CCC.US", 100, 100, name="Flat"),
        _quote("PENNY.US", 0.5, 0.25, name="Penny"),
        _quote("SPY.US", 510, 500, name="SPY"),
    ]


class _RealtimeQuotes:
    def get_by_universes(self, *, universes: list[str]) -> list[dict]:
        assert universes == ["US_Equity"]
        return _market_quotes()

    def get(self, *, symbols: list[str]) -> list[dict]:
        assert "SPY.US" in symbols
        return [_quote("SPY.US", 510, 500), _quote("QQQ.US", 430, 425)]


class _ProxyOnlyQuotes:
    def get_by_universes(self, *, universes: list[str]) -> list[dict]:
        raise RuntimeError("pool permission denied")

    def get(self, *, symbols: list[str]) -> list[dict]:
        assert len(symbols) <= 5
        return [_quote(symbol, 102, 100) for symbol in symbols]


class _EtfOnlyQuotes(_ProxyOnlyQuotes):
    def get(self, *, symbols: list[str]) -> list[dict]:
        return [
            _quote(symbol, 102, 100)
            for symbol in symbols
            if symbol in PROXY_SYMBOLS
        ]


class _BatchQuotes(_ProxyOnlyQuotes):
    def __init__(self) -> None:
        self.batch_calls: list[list[str]] = []

    def get_by_symbols(self, symbols: list[str], *, as_dataframe: bool) -> list[dict]:
        assert as_dataframe is False
        assert len(symbols) <= 5
        self.batch_calls.append(symbols)
        return [_quote(symbol, 102, 100) for symbol in symbols]

    def get(self, *, symbols: list[str]) -> list[dict]:
        raise AssertionError("批量接口可用时不应走按标的接口")


class _HistoryKlines:
    def batch(self, symbols: list[str], **kwargs) -> dict[str, list[dict]]:
        assert kwargs["period"] == "1d"
        assert kwargs["count"] == 2
        return {
            symbol: [
                {"date": "2026-08-06", "close": 100, "volume": 10},
                {"date": "2026-08-07", "close": 102, "volume": 12},
            ]
            for symbol in symbols
        }


class _SingleHistoryKlines:
    def get(self, symbol: str, **kwargs) -> list[dict]:
        assert kwargs["period"] == "1d"
        assert kwargs["count"] == 2
        return [
            {"date": "2026-08-06", "close": 100, "volume": 10},
            {"date": "2026-08-07", "close": 102, "volume": 12},
        ]


class _VolatilityHistoryKlines:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, symbol: str, **kwargs) -> list[dict]:
        self.calls += 1
        assert symbol == "XSD.US"
        assert kwargs["period"] == "1d"
        assert kwargs["count"] == 21
        closes = [100 + index + (2 if index % 2 else -1) for index in range(21)]
        return [
            {"date": f"2026-07-{index + 1:02d}", "close": close}
            for index, close in enumerate(closes)
        ]


def test_normalize_quote_keeps_decimal_pct_and_estimates_amount() -> None:
    row = normalize_us_quote(
        {
            "symbol": "AAPL.US",
            "name": "Apple",
            "last_price": 206,
            "prev_close": 200,
            "open": 201,
            "high": 208,
            "low": 199,
            "volume": 10,
            "amount": 0,
            "ext": {"change_pct": 0.03},
        }
    )

    assert row is not None
    assert row["change_pct"] == pytest.approx(0.03)
    assert row["change_amount"] == pytest.approx(6)
    assert row["open"] == pytest.approx(201)
    assert row["amplitude"] == pytest.approx(0.045)
    assert row["amount"] == pytest.approx(2_060)
    assert row["amount_estimated"] is True


def test_build_live_overview_calculates_breadth_and_rankings() -> None:
    result = build_live_overview(_market_quotes())

    assert result["status"] == "live"
    assert result["realtime"] is True
    assert result["coverage"] == "full_market"
    assert [step["status"] for step in result["data_path"]] == ["ok", "ok", "ok"]
    assert result["breadth"] == {
        "total": 4,
        "up": 2,
        "down": 1,
        "flat": 1,
        "strong": 2,
        "weak": 1,
        "up_ratio": 0.5,
        "down_ratio": 0.25,
        "average_change_pct": pytest.approx(0.25),
        "median_change_pct": pytest.approx(0.05),
        "advance_decline_ratio": pytest.approx(2.0),
        "net_advance_ratio": pytest.approx(0.25),
    }
    assert result["rankings"]["gainers"][0]["symbol"] == "AAA.US"
    assert "PENNY.US" not in {row["symbol"] for row in result["rankings"]["gainers"]}
    assert result["rankings"]["losers"][0]["symbol"] == "BBB.US"
    assert result["benchmarks"][0]["symbol"] == "SPY.US"
    assert result["benchmarks"][0]["high"] == pytest.approx(515.1)
    assert result["rankings"]["volatile"][0]["symbol"] == "AAA.US"
    assert sum(item["count"] for item in result["distribution"]) == 4


def test_rankings_require_volume_and_use_source_amplitude() -> None:
    no_volume = _quote("IDLE.US", 120, 100, volume=0)
    source_amplitude = _quote("SWING.US", 101, 100)
    source_amplitude["ext"]["amplitude"] = 0.5

    result = build_live_overview([*_market_quotes(), no_volume, source_amplitude])

    ranked_symbols = {
        row["symbol"]
        for key in ("gainers", "losers", "volatile")
        for row in result["rankings"][key]
    }
    assert "IDLE.US" not in ranked_symbols
    assert result["rankings"]["volatile"][0]["symbol"] == "SWING.US"
    assert result["rankings"]["volatile"][0]["amplitude"] == pytest.approx(0.5)


def test_advance_decline_ratio_is_null_without_decliners() -> None:
    result = build_live_overview(
        [_quote("UP.US", 102, 100), _quote("FLAT.US", 100, 100)]
    )

    assert result["breadth"]["advance_decline_ratio"] is None
    json.dumps(result, allow_nan=False)


def test_build_partial_overview_uses_latest_two_daily_closes() -> None:
    raw = _HistoryKlines().batch(["SPY.US", "XLK.US"], period="1d", count=2)

    result = build_partial_overview(raw)

    assert result["status"] == "partial"
    assert result["realtime"] is False
    assert result["coverage"] == "etf_daily"
    assert result["breadth"] is None
    assert result["rankings"] == {
        "gainers": [],
        "losers": [],
        "active": [],
        "volatile": [],
    }
    assert result["benchmarks"][0]["change_pct"] == pytest.approx(0.02)
    assert result["sectors"][0]["symbol"] == "XLK.US"


def test_service_writes_live_snapshot_and_returns_it_when_realtime_fails(tmp_path: Path) -> None:
    live_service = UsMarketOverviewService(
        tmp_path,
        realtime_client_factory=lambda: SimpleNamespace(quotes=_RealtimeQuotes()),
    )
    live = live_service.get_overview()
    assert live["status"] == "live"

    def fail_realtime():
        raise RuntimeError("permission denied")

    snapshot_service = UsMarketOverviewService(
        tmp_path,
        realtime_client_factory=fail_realtime,
        history_client_factory=lambda: (_ for _ in ()).throw(AssertionError("不应请求日线")),
    )
    snapshot = snapshot_service.get_overview()

    assert snapshot["status"] == "snapshot"
    assert snapshot["breadth"]["total"] == 4
    assert "permission denied" not in snapshot["message"]


def test_service_uses_daily_proxy_when_snapshot_is_corrupt(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "us_market" / "overview_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("{broken", encoding="utf-8")

    service = UsMarketOverviewService(
        tmp_path,
        realtime_client_factory=lambda: None,
        history_client_factory=lambda: SimpleNamespace(klines=_SingleHistoryKlines()),
    )
    result = service.get_overview()

    assert result["status"] == "partial"
    assert len(result["benchmarks"]) == 4
    assert len(result["sectors"]) == 11


def test_service_uses_realtime_sample_when_market_pool_is_unavailable(
    tmp_path: Path,
) -> None:
    service = UsMarketOverviewService(
        tmp_path,
        realtime_client_factory=lambda: SimpleNamespace(quotes=_ProxyOnlyQuotes()),
        history_client_factory=lambda: (_ for _ in ()).throw(AssertionError("不应请求日线")),
    )

    result = service.get_overview()

    assert result["status"] == "partial"
    assert result["realtime"] is True
    assert result["coverage"] == "sample"
    assert result["breadth"]["total"] == len(REALTIME_SAMPLE)
    assert result["rankings"]["gainers"]
    assert result["session"] == "regular"
    assert len(result["benchmarks"]) == 4
    assert len(result["sectors"]) == 11


def test_service_keeps_etf_realtime_path_when_sample_quotes_are_unavailable(
    tmp_path: Path,
) -> None:
    service = UsMarketOverviewService(
        tmp_path,
        realtime_client_factory=lambda: SimpleNamespace(quotes=_EtfOnlyQuotes()),
        history_client_factory=lambda: (_ for _ in ()).throw(AssertionError("不应请求日线")),
    )

    result = service.get_overview()

    assert result["status"] == "partial"
    assert result["realtime"] is True
    assert result["coverage"] == "etf_realtime"
    assert result["breadth"] is None
    assert len(result["benchmarks"]) == 4


def test_service_prefers_batch_quotes_for_realtime_sample(tmp_path: Path) -> None:
    quotes = _BatchQuotes()
    service = UsMarketOverviewService(
        tmp_path,
        realtime_client_factory=lambda: SimpleNamespace(quotes=quotes),
    )

    result = service.get_overview()

    assert result["coverage"] == "sample"
    expected_batches = (len(PROXY_SYMBOLS) + len(REALTIME_SAMPLE) + 4) // 5
    assert len(quotes.batch_calls) == expected_batches


def test_cached_response_is_isolated_from_caller_mutation(tmp_path: Path) -> None:
    service = UsMarketOverviewService(
        tmp_path,
        realtime_client_factory=lambda: SimpleNamespace(quotes=_RealtimeQuotes()),
        monotonic=lambda: 10.0,
    )
    first = service.get_overview()
    first["benchmarks"][0]["name"] = "mutated"

    second = service.get_overview()

    assert second["benchmarks"][0]["name"] != "mutated"
    persisted = json.loads(
        (tmp_path / "us_market" / "overview_snapshot.json").read_text(encoding="utf-8")
    )
    assert persisted["benchmarks"][0]["name"] != "mutated"


def test_proxy_volatility_uses_twenty_daily_returns_and_cache(tmp_path: Path) -> None:
    klines = _VolatilityHistoryKlines()
    service = UsMarketOverviewService(
        tmp_path,
        history_client_factory=lambda: SimpleNamespace(klines=klines),
        monotonic=lambda: 10.0,
    )

    first = service.get_proxy_volatilities(["XSD.US"], window=20)
    second = service.get_proxy_volatilities(["XSD.US"], window=20)

    assert first["XSD.US"] > 0
    assert second == first
    assert klines.calls == 1
