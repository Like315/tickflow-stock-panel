from __future__ import annotations

from datetime import datetime

import polars as pl

from app.plugins.tushare_history import bridge
from app.plugins.tushare_history.provider import TushareHistoricalMinuteProvider


def test_tushare_history_provider_normalizes_share_volume_to_lots(monkeypatch) -> None:
    raw = pl.DataFrame({
        "ts_code": ["600000.SH", "600000.SH", "600000.SH"],
        "trade_time": [
            "2024-01-02 09:31:00",
            "2024-01-02 09:32:00",
            "2024-01-02 09:33:00",
        ],
        "open": [10.0, 10.1, 10.2],
        "high": [10.1, 10.2, 10.3],
        "low": [9.9, 10.0, 10.1],
        "close": [10.05, 10.15, 10.25],
        "vol": [1_000.0, 2_000.0, 3_000.0],
        "amount": [10_000.0, 20_000.0, 30_000.0],
    })
    monkeypatch.setattr(bridge, "fetch_minutes", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        "app.plugins.tushare_history.provider.sleep_between_batches",
        lambda *_args, **_kwargs: None,
    )
    provider = TushareHistoricalMinuteProvider()

    result = provider.get_minute(
        ["600000.SH"],
        datetime(2024, 1, 2, 9, 15),
        datetime(2024, 1, 2, 15, 5),
    )

    assert result.height == 3
    assert result["volume"].to_list() == [10.0, 20.0, 30.0]
    assert result["raw_close"].to_list() == result["close"].to_list()
    assert result["source"].unique().to_list() == ["tushare_history"]


def test_tushare_history_provider_segments_long_ranges(monkeypatch) -> None:
    calls: list[tuple[datetime, datetime]] = []

    def fake_fetch(_symbol, *, start_time, end_time, **_kwargs):
        calls.append((start_time, end_time))
        return pl.DataFrame()

    monkeypatch.setattr(bridge, "fetch_minutes", fake_fetch)
    monkeypatch.setattr(
        "app.plugins.tushare_history.provider.sleep_between_batches",
        lambda *_args, **_kwargs: None,
    )
    provider = TushareHistoricalMinuteProvider()

    provider.get_minute(
        ["600000.SH"],
        datetime(2024, 1, 1),
        datetime(2024, 2, 15),
    )

    assert len(calls) == 3
    assert calls[0] == (datetime(2024, 1, 1), datetime(2024, 1, 21))
    assert calls[-1][1] == datetime(2024, 2, 15)
