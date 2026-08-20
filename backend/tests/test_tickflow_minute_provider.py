from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from app.data_providers.normalizer import MINUTE_COLS, normalize_minute
from app.data_providers.tickflow_provider import TickFlowProvider
from app.market_time import CN_TZ


def test_normalize_minute_keeps_raw_execution_prices() -> None:
    received_at = datetime(2026, 8, 18, 1, 32, tzinfo=UTC)
    rows = pl.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "timestamp": [1787016660000, 1787016720000],
        "open": [10.0, 10.1],
        "high": [10.2, 10.3],
        "low": [9.9, 10.0],
        "close": [10.1, 10.2],
        "vol": [100.0, 120.0],
        "amt": [101000.0, 122400.0],
    })

    result = normalize_minute(rows, received_at=received_at)

    assert result.columns == MINUTE_COLS
    assert result["raw_open"].to_list() == result["open"].to_list()
    assert result["raw_close"].to_list() == result["close"].to_list()
    assert result.schema["received_at"].time_zone == "UTC"
    assert result["asset_type"].unique().to_list() == ["stock"]


def test_tickflow_provider_requests_unadjusted_minute_prices(monkeypatch) -> None:
    calls: list[dict] = []

    class Klines:
        def batch(self, symbols: list[str], **kwargs: Any) -> dict[str, pl.DataFrame]:
            calls.append({"symbols": symbols, **kwargs})
            return {
                symbols[0]: pl.DataFrame({
                    "timestamp": [1787016660000],
                    "open": [10.0],
                    "high": [10.1],
                    "low": [9.9],
                    "close": [10.0],
                    "volume": [100.0],
                    "amount": [100000.0],
                })
            }

    class Client:
        klines = Klines()

    monkeypatch.setattr("app.data_providers.tickflow_provider.get_client", lambda: Client())

    result = TickFlowProvider().get_minute(
        ["000001.SZ"],
        datetime(2026, 8, 18, 9, 30),
        datetime(2026, 8, 18, 9, 32),
        "stock",
    )

    assert result.height == 1
    assert calls[0]["period"] == "1m"
    assert calls[0]["adjust"] == "none"


def test_tickflow_provider_deduplicates_merged_chunks(monkeypatch) -> None:
    class Klines:
        def batch(self, symbols: list[str], **kwargs: Any) -> pl.DataFrame:
            return pl.DataFrame({
                "symbol": ["000001.SZ"],
                "timestamp": [1787016660000],
                "open": [10.0],
                "high": [10.1],
                "low": [9.9],
                "close": [10.0],
                "volume": [100.0],
                "amount": [100_000.0],
            })

    class Client:
        klines = Klines()

    monkeypatch.setattr("app.data_providers.tickflow_provider.get_client", lambda: Client())
    provider = TickFlowProvider()
    provider.configure_minute_limits(batch_size=1, rpm=0)

    result = provider.get_minute(
        ["000001.SZ", "000002.SZ"],
        datetime(2026, 8, 18, 9, 30),
        datetime(2026, 8, 18, 9, 32),
        "stock",
    )

    assert result.height == 1
    assert result.select(["symbol", "datetime"]).unique().height == 1


def test_normalize_minute_converts_aware_datetime_to_beijing_wall_time() -> None:
    result = normalize_minute(pl.DataFrame({
        "symbol": ["000001.SZ"],
        "datetime": [datetime(2026, 8, 18, 1, 31, tzinfo=UTC)],
        "open": [10.0],
        "high": [10.1],
        "low": [9.9],
        "close": [10.0],
        "volume": [100.0],
        "amount": [100_000.0],
    }))

    assert result["datetime"][0] == datetime(2026, 8, 18, 9, 31)


def test_normalize_minute_accepts_tickflow_trade_date_and_trade_time_columns() -> None:
    result = normalize_minute(pl.DataFrame({
        "symbol": ["300404.SZ"],
        "timestamp": [1787016660000],
        "trade_date": ["2026-08-18"],
        "trade_time": ["2026-08-18 09:31:00"],
        "open": [16.92],
        "high": [17.60],
        "low": [16.92],
        "close": [17.25],
        "volume": [49_871],
        "amount": [86_210_226.66],
    }))

    assert result.height == 1
    assert result["datetime"][0] == datetime(2026, 8, 18, 9, 31)


def test_tickflow_intraday_provider_uses_intraday_batch_and_filters_range(monkeypatch) -> None:
    calls: list[dict] = []

    class Klines:
        def intraday_batch(self, symbols: list[str], **kwargs: Any) -> dict[str, pl.DataFrame]:
            calls.append({"symbols": symbols, **kwargs})
            return {
                symbols[0]: pl.DataFrame({
                    "timestamp": [
                        int(datetime(2026, 8, 18, 1, 30, tzinfo=UTC).timestamp() * 1000),
                        int(datetime(2026, 8, 18, 1, 31, tzinfo=UTC).timestamp() * 1000),
                    ],
                    "open": [10.0, 10.1],
                    "high": [10.1, 10.2],
                    "low": [9.9, 10.0],
                    "close": [10.0, 10.1],
                    "volume": [100.0, 120.0],
                    "amount": [100_000.0, 121_200.0],
                })
            }

    class Client:
        klines = Klines()

    monkeypatch.setattr("app.data_providers.tickflow_provider.get_client", lambda: Client())
    provider = TickFlowProvider()

    result = provider.get_intraday_minute(
        ["000001.SZ"],
        datetime(2026, 8, 18, 9, 31, tzinfo=CN_TZ),
        datetime(2026, 8, 18, 9, 32, tzinfo=CN_TZ),
    )

    assert result.height == 1
    assert result["datetime"][0] == datetime(2026, 8, 18, 9, 31)
    assert calls[0]["period"] == "1m"
    assert "start_time" not in calls[0]


def test_normalize_minute_prefers_canonical_columns_over_aliases() -> None:
    canonical_time = datetime(2026, 8, 18, 9, 31)
    result = normalize_minute(pl.DataFrame({
        "symbol": ["000001.SZ"],
        "ts_code": ["600000.SH"],
        "datetime": [canonical_time],
        "trade_time": [datetime(2026, 8, 18, 9, 32)],
        "trade_date": [datetime(2026, 8, 18)],
        "open": [10.0],
        "high": [10.1],
        "low": [9.9],
        "close": [10.0],
        "volume": [100.0],
        "vol": [999.0],
        "amount": [100_000.0],
        "amt": [999_000.0],
    }))

    assert result["symbol"].to_list() == ["000001.SZ"]
    assert result["datetime"].to_list() == [canonical_time]
    assert result["volume"].to_list() == [100.0]
    assert result["amount"].to_list() == [100_000.0]


def test_normalize_minute_uses_one_datetime_alias() -> None:
    trade_time = datetime(2026, 8, 18, 9, 31)
    result = normalize_minute(pl.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_time": [trade_time],
        "trade_date": [datetime(2026, 8, 18)],
        "open": [10.0],
        "high": [10.1],
        "low": [9.9],
        "close": [10.0],
        "vol": [100.0],
        "amt": [100_000.0],
    }))

    assert result["datetime"].to_list() == [trade_time]


def test_normalize_minute_prefers_epoch_timestamp_over_string_time_aliases() -> None:
    result = normalize_minute(pl.DataFrame({
        "symbol": ["600000.SH"],
        "timestamp": [1787016660000],
        "trade_date": ["2026-08-18"],
        "trade_time": ["09:31:00"],
        "open": [10.0],
        "high": [10.1],
        "low": [9.9],
        "close": [10.0],
        "volume": [100.0],
        "amount": [100_000.0],
    }))

    assert result.height == 1
    assert result["datetime"].to_list() == [datetime(2026, 8, 18, 9, 31)]
